from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.document_set import (
    DocumentSetError,
    build_document_set_compile_request,
    ingest_document_set,
    normalize_document_set,
)
from knowledge_forge.provenance import build_compiler_profile


STAMP_A = datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)
STAMP_B = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)


def profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


def write_fixture(root: Path) -> None:
    (root / "nested").mkdir(parents=True)
    (root / "zeta.txt").write_text("Zeta paragraph.\n", encoding="utf-8")
    (root / "nested" / "alpha.md").write_text(
        "# Alpha\n\nAlpha body.\n",
        encoding="utf-8",
    )


class DocumentSetTests(unittest.TestCase):
    def test_ingest_is_sorted_and_collection_id_ignores_timestamp_and_root_location(self):
        with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
            first = Path(first_tmp) / "one"
            second = Path(second_tmp) / "renamed-root"
            first.mkdir()
            second.mkdir()
            write_fixture(first)
            write_fixture(second)

            a = ingest_document_set(first, ingested_at=STAMP_A)
            b = ingest_document_set(first, ingested_at=STAMP_B)
            relocated = ingest_document_set(second, ingested_at=STAMP_A)

        self.assertEqual(
            [member["relative_path"] for member in a["members"]],
            ["nested/alpha.md", "zeta.txt"],
        )
        self.assertEqual(a["document_set_id"], b["document_set_id"])
        self.assertEqual(a["document_set_id"], relocated["document_set_id"])
        self.assertNotEqual(a["ingested_at"], b["ingested_at"])

    def test_member_manifests_keep_exact_source_identity_and_relative_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)

        first = manifest["members"][0]
        self.assertEqual(first["relative_path"], "nested/alpha.md")
        self.assertTrue(first["source_manifest"]["source_id"].startswith("sha256:"))
        self.assertEqual(first["source_manifest"]["source_role"], "ORDINARY_KNOWLEDGE")
        self.assertEqual(
            first["source_manifest"]["origin"],
            {
                "kind": "document_set_member",
                "locator": "nested/alpha.md",
            },
        )

    def test_member_manifest_parser_metadata_cannot_be_tampered_under_same_set_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            original_set_id = manifest["document_set_id"]

            manifest["members"][0]["source_manifest"]["media_type"] = "text/plain"
            self.assertEqual(manifest["document_set_id"], original_set_id)
            with self.assertRaisesRegex(
                DocumentSetError,
                "media_type must match relative_path suffix",
            ):
                normalize_document_set(root, manifest)

    def test_member_manifest_digest_metadata_must_match_source_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            manifest["members"][0]["source_manifest"]["byte_sha256"] = "0" * 64

            with self.assertRaisesRegex(
                DocumentSetError,
                "byte_sha256 must match source_id",
            ):
                normalize_document_set(root, manifest)

    def test_member_manifest_name_must_match_relative_path_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            manifest["members"][0]["source_manifest"]["name"] = "other.md"

            with self.assertRaisesRegex(
                DocumentSetError,
                "name must match relative_path basename",
            ):
                normalize_document_set(root, manifest)

    def test_normalization_preserves_each_source_boundary_and_refuses_membership_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            normalized = normalize_document_set(root, manifest)

            self.assertEqual(
                [
                    member["normalized_source"]["source_id"]
                    for member in normalized["members"]
                ],
                [
                    member["source_manifest"]["source_id"]
                    for member in manifest["members"]
                ],
            )
            self.assertEqual(
                normalized["members"][0]["normalized_source"]["sections"][0]["heading"],
                "Alpha",
            )
            self.assertTrue(
                all(
                    member["normalized_source"]["source_role"] == "ORDINARY_KNOWLEDGE"
                    for member in normalized["members"]
                )
            )

            (root / "new.txt").write_text("new member\n", encoding="utf-8")
            with self.assertRaisesRegex(
                DocumentSetError,
                "membership changed after ingest",
            ):
                normalize_document_set(root, manifest)

    def test_source_byte_drift_refuses_even_when_membership_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)

            (root / "zeta.txt").write_text("changed bytes\n", encoding="utf-8")
            with self.assertRaisesRegex(
                DocumentSetError,
                "source bytes no longer match ingest manifest",
            ):
                normalize_document_set(root, manifest)

    def test_document_set_refuses_reserved_doctrine_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manual.md").write_text("# Manual\nbody\n", encoding="utf-8")
            (root / "DOCTRINE.md").write_text("# Doctrine\nlaw\n", encoding="utf-8")

            with self.assertRaisesRegex(
                DocumentSetError,
                "CANON != SKILL.*DOCTRINE",
            ):
                ingest_document_set(root, ingested_at=STAMP_A)

    def test_document_set_manifest_refuses_forged_protected_source_role(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            manifest["members"][0]["source_manifest"]["source_role"] = "DOCTRINE"

            with self.assertRaisesRegex(
                DocumentSetError,
                "CANON != SKILL.*DOCTRINE",
            ):
                normalize_document_set(root, manifest)

    def test_document_set_compile_refuses_normalized_source_role_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            normalized = normalize_document_set(root, manifest)
            normalized["members"][0]["normalized_source"]["source_role"] = "DOCTRINE"

            with self.assertRaisesRegex(
                DocumentSetError,
                "source_role mismatch",
            ):
                build_document_set_compile_request(
                    normalized,
                    knowledge_id="topic:document-set",
                    skill_slug="document-set",
                    compiler_profile=profile(),
                )

    def test_unsupported_regular_file_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            (root / ".DS_Store").write_bytes(b"noise")
            with self.assertRaisesRegex(
                DocumentSetError,
                "unsupported file in document set",
            ):
                ingest_document_set(root, ingested_at=STAMP_A)

    def test_symlink_member_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            link = root / "alias.txt"
            try:
                link.symlink_to(root / "zeta.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaisesRegex(DocumentSetError, "symlink is refused"):
                ingest_document_set(root, ingested_at=STAMP_A)

    def test_compile_request_is_multi_source_and_path_insensitive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root)
            normalized = normalize_document_set(
                root,
                ingest_document_set(root, ingested_at=STAMP_A),
            )
            request = build_document_set_compile_request(
                normalized,
                knowledge_id="topic:document-set",
                skill_slug="document-set",
                compiler_profile=profile(),
            )

        source_ids = [source["source_id"] for source in request["sources"]]
        self.assertEqual(source_ids, sorted(source_ids))
        self.assertEqual(len(source_ids), 2)
        self.assertEqual(request["authority"], "KNOWLEDGE_ONLY")
        self.assertTrue(
            all(source["source_role"] == "ORDINARY_KNOWLEDGE" for source in request["sources"])
        )
        self.assertNotIn("document_set_id", request)
        self.assertTrue(all("relative_path" not in source for source in request["sources"]))

    def test_duplicate_byte_sources_remain_collection_members_but_compile_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.txt").write_text("same\n", encoding="utf-8")
            (root / "b.txt").write_text("same\n", encoding="utf-8")
            manifest = ingest_document_set(root, ingested_at=STAMP_A)
            normalized = normalize_document_set(root, manifest)
            request = build_document_set_compile_request(
                normalized,
                knowledge_id="topic:duplicates",
                skill_slug="duplicates",
                compiler_profile=profile(),
            )

        self.assertEqual(len(manifest["members"]), 2)
        self.assertEqual(len(request["sources"]), 1)


if __name__ == "__main__":
    unittest.main()
