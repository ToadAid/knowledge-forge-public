from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.ingest import IngestError, ingest_source


class IngestTests(unittest.TestCase):
    def test_exact_bytes_bind_source_identity(self) -> None:
        payload = b"# Hello\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_bytes(payload)
            manifest = ingest_source(
                path,
                ingested_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
            )

        expected = sha256(payload).hexdigest()
        self.assertEqual(manifest["source_id"], f"sha256:{expected}")
        self.assertEqual(manifest["byte_sha256"], expected)
        self.assertEqual(manifest["byte_length"], len(payload))
        self.assertEqual(manifest["media_type"], "text/markdown")
        self.assertEqual(manifest["source_role"], "ORDINARY_KNOWLEDGE")
        self.assertEqual(manifest["ingested_at"], "2026-09-22T20:30:00Z")
        self.assertEqual(manifest["origin"], {"kind": "local_file", "locator": "note.md"})

    def test_timestamp_does_not_change_source_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "same.txt"
            path.write_text("same bytes", encoding="utf-8")
            first = ingest_source(path, ingested_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
            second = ingest_source(path, ingested_at=datetime(2026, 2, 1, tzinfo=timezone.utc))

        self.assertEqual(first["source_id"], second["source_id"])
        self.assertNotEqual(first["ingested_at"], second["ingested_at"])

    def test_pdf_exact_bytes_enter_ingest_without_semantic_parsing(self) -> None:
        payload = b"%PDF-1.4\nfixture-bytes\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.pdf"
            path.write_bytes(payload)
            manifest = ingest_source(
                path,
                ingested_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
            )

        expected = sha256(payload).hexdigest()
        self.assertEqual(manifest["media_type"], "application/pdf")
        self.assertEqual(manifest["source_id"], f"sha256:{expected}")
        self.assertEqual(manifest["byte_sha256"], expected)

    def test_docx_exact_bytes_enter_ingest_without_unzipping(self) -> None:
        payload = b"PK\x03\x04fixture-docx-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.docx"
            path.write_bytes(payload)
            manifest = ingest_source(
                path,
                ingested_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
            )

        expected = sha256(payload).hexdigest()
        self.assertEqual(
            manifest["media_type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.assertEqual(manifest["source_id"], f"sha256:{expected}")
        self.assertEqual(manifest["byte_sha256"], expected)

    def test_epub_exact_bytes_enter_ingest_without_unzipping(self) -> None:
        payload = b"PK\x03\x04fixture-epub-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(payload)
            manifest = ingest_source(
                path,
                ingested_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
            )

        expected = sha256(payload).hexdigest()
        self.assertEqual(manifest["media_type"], "application/epub+zip")
        self.assertEqual(manifest["source_id"], f"sha256:{expected}")
        self.assertEqual(manifest["byte_sha256"], expected)

    def test_technical_text_exact_bytes_enter_ingest_without_execution(self) -> None:
        payload = b"def frog():\n    return 'pond'\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frog.py"
            path.write_bytes(payload)
            manifest = ingest_source(
                path,
                ingested_at=datetime(2026, 9, 22, 20, 30, tzinfo=timezone.utc),
            )

        expected = sha256(payload).hexdigest()
        self.assertEqual(manifest["media_type"], "text/x-technical")
        self.assertEqual(manifest["source_id"], f"sha256:{expected}")
        self.assertEqual(manifest["byte_sha256"], expected)

    def test_refuses_reserved_doctrine_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "DOCTRINE.md"
            path.write_text("governing law", encoding="utf-8")
            with self.assertRaisesRegex(IngestError, "CANON != SKILL.*DOCTRINE"):
                ingest_source(path)

    def test_refuses_explicit_protected_role_on_generic_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.md"
            path.write_text("authority rules", encoding="utf-8")
            with self.assertRaisesRegex(IngestError, "CANON != SKILL.*AUTHORITY_POLICY"):
                ingest_source(path, source_role="AUTHORITY_POLICY")

    def test_refuses_unsupported_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.png"
            path.write_bytes(b"not-yet-supported")
            with self.assertRaisesRegex(IngestError, "unsupported source type"):
                ingest_source(path)

    def test_refuses_naive_lifecycle_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.md"
            path.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(IngestError, "timezone-aware"):
                ingest_source(path, ingested_at=datetime(2026, 1, 1))


if __name__ == "__main__":
    unittest.main()
