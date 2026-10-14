from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.cli import main
from tests.test_trust_wall import _governed_package


def _write_registry(root: Path, registry) -> Path:
    path = root / "registry.json"
    path.write_text(
        json.dumps(registry, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _run(argv):
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = main(argv)
    out = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    err = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return rc, out, err


class CliTests(unittest.TestCase):
    def test_validate_package_uses_real_package_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root, _, _, _ = _governed_package(root)
            rc, out, err = _run([
                "validate-package",
                str(skill_root),
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(err)
        self.assertTrue(out["valid"])
        self.assertGreater(out["artifact_count"], 5)
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["findings"], [])

    def test_registry_inspect_validates_and_summarizes_without_skill_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, registry, manifest, _ = _governed_package(root)
            registry_path = _write_registry(root, registry)
            rc, out, err = _run([
                "registry-inspect",
                str(registry_path),
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(err)
        self.assertTrue(out["valid"])
        self.assertEqual(out["skill_count"], 1)
        self.assertEqual(
            out["current_skills"][0]["skill_id"],
            manifest["skill_id"],
        )
        encoded = json.dumps(out, sort_keys=True)
        self.assertNotIn("7 members", encoded)
        self.assertNotIn("wallet_send", encoded)

    def test_discover_returns_identity_metadata_only_and_zero_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, registry, manifest, _ = _governed_package(root)
            registry_path = _write_registry(root, registry)
            rc, out, err = _run([
                "discover",
                str(registry_path),
                "What approval quorum is required?",
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(err)
        self.assertEqual(out["match_count"], 1)
        self.assertEqual(out["matches"][0]["skill_id"], manifest["skill_id"])
        self.assertEqual(out["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(out["execution_authority"], "NONE")
        self.assertEqual(out["tool_grants"], [])
        encoded = json.dumps(out, sort_keys=True)
        self.assertNotIn("7 members", encoded)
        self.assertNotIn("wallet_send", encoded)

    def test_load_current_uses_trust_and_temporal_runtime_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root, registry, manifest, _ = _governed_package(root)
            registry_path = _write_registry(root, registry)
            rc, out, err = _run([
                "load-current",
                str(skill_root),
                str(registry_path),
                manifest["skill_id"],
                "What approval quorum is required?",
                "--world-as-of",
                "2026-09-25T12:00:00Z",
                "--runtime-tool",
                "market_price",
            ])

        self.assertEqual(rc, 0)
        self.assertIsNone(err)
        result = out["result"]
        self.assertEqual(result["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(result["instruction_authority"], "NONE")
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertEqual(result["effective_tools"], ["market_price"])
        self.assertEqual(
            result["reasoning_context"][0]["temporal_condition"],
            "CURRENT",
        )
        self.assertEqual(
            result["temporal_evidence"]["authority"],
            "WORLD_CLOCK",
        )

    def test_invalid_registry_fails_closed_with_machine_readable_stderr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "registry.json"
            path.write_text('{"schema_version":"fake"}\n', encoding="utf-8")
            rc, out, err = _run([
                "registry-inspect",
                str(path),
            ])

        self.assertEqual(rc, 2)
        self.assertIsNone(out)
        self.assertFalse(err["ok"])
        self.assertIn("registry validation failed", err["error"])

    def test_package_symlink_refuses_before_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root, _, _, _ = _governed_package(root)
            target = skill_root / "manifest.json"
            link = skill_root / "linked-manifest.json"
            link.symlink_to(target.name)
            rc, out, err = _run([
                "validate-package",
                str(skill_root),
            ])

        self.assertEqual(rc, 2)
        self.assertIsNone(out)
        self.assertFalse(err["ok"])
        self.assertIn("may not be a symlink", err["error"])

    def test_discover_hard_cap_is_parser_bounded_to_three(self):
        parser_exit = None
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                main([
                    "discover",
                    "registry.json",
                    "question",
                    "--max-skills",
                    "4",
                ])
        parser_exit = caught.exception.code
        self.assertEqual(parser_exit, 2)


    def test_fast_skill_index_build_list_discover_and_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, registry, manifest, _ = _governed_package(root)
            registry_path = _write_registry(root, registry)

            rc, index, err = _run([
                "index-build",
                str(registry_path),
            ])
            self.assertEqual(rc, 0)
            self.assertIsNone(err)
            self.assertEqual(
                index["index_kind"],
                "ROUTING_HINT_ONLY_NOT_LIFECYCLE_OR_KNOWLEDGE_TRUTH",
            )
            self.assertTrue(
                index["selection_requires_current_registry_revalidation"]
            )

            index_path = root / "skill-index.v1.json"
            index_path.write_text(
                json.dumps(index, sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )

            rc, listed, err = _run([
                "list",
                str(index_path),
            ])
            self.assertEqual(rc, 0)
            self.assertIsNone(err)
            self.assertEqual(listed["skill_count"], 1)
            self.assertEqual(
                listed["skills"][0]["skill_id"],
                manifest["skill_id"],
            )

            rc, routed, err = _run([
                "discover-index",
                str(index_path),
                "What approval quorum is required?",
            ])
            self.assertEqual(rc, 0)
            self.assertIsNone(err)
            self.assertEqual(
                routed["selected_skill_ids"],
                [manifest["skill_id"]],
            )
            self.assertTrue(
                routed["selection_requires_current_registry_revalidation"]
            )
            self.assertEqual(routed["tool_grants"], [])

            rc, verified, err = _run([
                "index-verify",
                str(index_path),
                str(registry_path),
            ])
            self.assertEqual(rc, 0)
            self.assertIsNone(err)
            self.assertTrue(verified["current"])
            self.assertFalse(verified["requires_rebuild"])

            encoded = json.dumps(
                {
                    "list": listed,
                    "route": routed,
                },
                sort_keys=True,
            )
            self.assertNotIn("7 members", encoded)
            self.assertNotIn("wallet_send", encoded)

    def test_discover_index_hard_cap_is_parser_bounded_to_three(self):
        stdout = StringIO()
        stderr = StringIO()
        with self.assertRaises(SystemExit) as caught:
            with redirect_stdout(stdout), redirect_stderr(stderr):
                main([
                    "discover-index",
                    "skill-index.v1.json",
                    "question",
                    "--max-skills",
                    "4",
                ])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
