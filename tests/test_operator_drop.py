from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.operator_drop import (
    COPY_AUTHORITY,
    SOURCE_ACCESS,
    OperatorDropError,
    OperatorDropPolicy,
    copy_from_operator_drop,
    default_operator_knowledge_drop_root,
    discover_operator_drop,
    validate_operator_drop_policy,
)


class OperatorDropTests(unittest.TestCase):
    def _roots(self):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        drop = root / "drop"
        sandbox = root / "sandbox"
        intake = root / "intake"
        other = root / "other"
        for path in (drop, sandbox, intake, other):
            path.mkdir()
        policy = OperatorDropPolicy(
            drop_root=drop,
            allowed_copy_roots=(sandbox, intake),
            max_file_bytes=1024,
            max_entries=32,
        )
        return temp, drop, sandbox, intake, other, policy

    def test_default_root_is_shared_outside_agent_specific_sandbox(self):
        home = Path("/home/example")
        self.assertEqual(
            default_operator_knowledge_drop_root(home),
            home / ".toadaid" / "drop" / "knowledge",
        )

    def test_policy_projects_read_only_source_and_bounded_copy_authority(self):
        temp, _drop, sandbox, intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)

        summary = validate_operator_drop_policy(policy)

        self.assertTrue(summary["valid"])
        self.assertEqual(summary["source_access"], SOURCE_ACCESS)
        self.assertEqual(summary["copy_authority"], COPY_AUTHORITY)
        self.assertEqual(
            summary["allowed_copy_roots"],
            [str(sandbox.resolve()), str(intake.resolve())],
        )

    def test_discovery_lists_regular_files_and_never_follows_symlinks(self):
        temp, drop, _sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "book.pdf").write_bytes(b"pdf")
        nested = drop / "notes"
        nested.mkdir()
        (nested / "chapter.txt").write_text("chapter", encoding="utf-8")
        os.symlink(drop / "book.pdf", drop / "linked.pdf")
        os.symlink(nested, drop / "linked-dir")

        result = discover_operator_drop(policy)

        self.assertEqual(
            [entry["relative_path"] for entry in result["entries"]],
            ["book.pdf", "notes/chapter.txt"],
        )
        self.assertEqual(result["skipped_symlinks"], ["linked-dir", "linked.pdf"])
        self.assertEqual(result["source_access"], "READ_ONLY")

    def test_discovery_marks_oversize_file_ineligible(self):
        temp, drop, _sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "large.bin").write_bytes(b"x" * 1025)

        result = discover_operator_drop(policy)

        self.assertFalse(result["entries"][0]["eligible"])
        self.assertEqual(
            result["entries"][0]["refusal_reason"],
            "FILE_TOO_LARGE",
        )

    def test_copy_preserves_source_and_binds_exact_sha(self):
        temp, drop, sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        payload = b"uniswap-v3-whitepaper"
        source = drop / "Uniswap-v3-corewhitepaper.pdf"
        source.write_bytes(payload)
        before = source.read_bytes()

        receipt = copy_from_operator_drop(
            policy,
            "Uniswap-v3-corewhitepaper.pdf",
            destination_root=sandbox,
            destination_relative_path="knowledge/Uniswap-v3-corewhitepaper.pdf",
        )

        copied = sandbox / "knowledge" / "Uniswap-v3-corewhitepaper.pdf"
        self.assertEqual(copied.read_bytes(), payload)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(
            receipt["source_sha256"],
            "sha256:" + sha256(payload).hexdigest(),
        )
        self.assertFalse(receipt["overwrite"])
        self.assertFalse(receipt["source_mutated"])

    def test_copy_to_second_host_approved_root_is_allowed(self):
        temp, drop, _sandbox, intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "source.txt").write_text("hello", encoding="utf-8")

        copy_from_operator_drop(
            policy,
            "source.txt",
            destination_root=intake,
        )

        self.assertEqual(
            (intake / "source.txt").read_text(encoding="utf-8"),
            "hello",
        )

    def test_unapproved_destination_root_is_refused(self):
        temp, drop, _sandbox, _intake, other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "source.txt").write_text("hello", encoding="utf-8")

        with self.assertRaisesRegex(OperatorDropError, "not host-approved"):
            copy_from_operator_drop(
                policy,
                "source.txt",
                destination_root=other,
            )

    def test_source_traversal_and_absolute_paths_are_refused(self):
        temp, _drop, sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)

        for source in ("../escape.txt", "/tmp/escape.txt", r"C:\escape.txt"):
            with self.subTest(source=source):
                with self.assertRaises(OperatorDropError):
                    copy_from_operator_drop(
                        policy,
                        source,
                        destination_root=sandbox,
                    )

    def test_source_symlink_is_refused(self):
        temp, drop, sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        target = drop / "target.txt"
        target.write_text("hello", encoding="utf-8")
        os.symlink(target, drop / "link.txt")

        with self.assertRaisesRegex(OperatorDropError, "symlink"):
            copy_from_operator_drop(
                policy,
                "link.txt",
                destination_root=sandbox,
            )

    def test_symlink_destination_parent_is_refused(self):
        temp, drop, sandbox, _intake, other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "source.txt").write_text("hello", encoding="utf-8")
        os.symlink(other, sandbox / "linked")

        with self.assertRaisesRegex(OperatorDropError, "symlink"):
            copy_from_operator_drop(
                policy,
                "source.txt",
                destination_root=sandbox,
                destination_relative_path="linked/copied.txt",
            )
        self.assertFalse((other / "copied.txt").exists())

    def test_existing_destination_is_never_overwritten(self):
        temp, drop, sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "source.txt").write_text("new", encoding="utf-8")
        destination = sandbox / "source.txt"
        destination.write_text("old", encoding="utf-8")

        with self.assertRaisesRegex(OperatorDropError, "overwrite is forbidden"):
            copy_from_operator_drop(
                policy,
                "source.txt",
                destination_root=sandbox,
            )
        self.assertEqual(destination.read_text(encoding="utf-8"), "old")

    def test_oversize_source_is_refused_on_copy(self):
        temp, drop, sandbox, _intake, _other, policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "large.bin").write_bytes(b"x" * 1025)

        with self.assertRaisesRegex(OperatorDropError, "file limit"):
            copy_from_operator_drop(
                policy,
                "large.bin",
                destination_root=sandbox,
            )

    def test_drop_and_destination_roots_may_not_overlap(self):
        temp, drop, _sandbox, _intake, _other, _policy = self._roots()
        self.addCleanup(temp.cleanup)
        nested = drop / "agent"
        nested.mkdir()
        policy = OperatorDropPolicy(
            drop_root=drop,
            allowed_copy_roots=(nested,),
        )

        with self.assertRaisesRegex(OperatorDropError, "may not overlap"):
            validate_operator_drop_policy(policy)

    def test_entry_budget_fails_closed(self):
        temp, drop, sandbox, _intake, _other, _policy = self._roots()
        self.addCleanup(temp.cleanup)
        (drop / "a.txt").write_text("a", encoding="utf-8")
        (drop / "b.txt").write_text("b", encoding="utf-8")
        policy = OperatorDropPolicy(
            drop_root=drop,
            allowed_copy_roots=(sandbox,),
            max_entries=1,
        )

        with self.assertRaisesRegex(OperatorDropError, "entry limit"):
            discover_operator_drop(policy)


if __name__ == "__main__":
    unittest.main()
