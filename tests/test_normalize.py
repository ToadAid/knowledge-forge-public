from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.ingest import ingest_source
from knowledge_forge.normalize import NormalizationError, normalize_source
from tests.test_docx_extract import DOCX_BYTES
from tests.test_epub_extract import EPUB_BYTES
from tests.test_pdf_extract import TEXT_PDF_BYTES


STAMP = datetime(2026, 9, 22, tzinfo=timezone.utc)


class NormalizeTests(unittest.TestCase):
    def test_markdown_headings_create_stable_line_anchors(self) -> None:
        source = "preamble\n\n# Alpha\none\ntwo\n\n## Beta\nthree\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text(source, encoding="utf-8")
            manifest = ingest_source(path, ingested_at=STAMP)
            normalized = normalize_source(path, manifest)

        self.assertEqual(
            [(s["section_id"], s["heading"], s["anchor"]) for s in normalized["sections"]],
            [
                ("s001", None, {"kind": "line_range", "start_line": 1, "end_line": 2}),
                ("s002", "Alpha", {"kind": "line_range", "start_line": 3, "end_line": 6}),
                ("s003", "Beta", {"kind": "line_range", "start_line": 7, "end_line": 8}),
            ],
        )
        self.assertEqual(normalized["sections"][1]["text"], "one\ntwo")
        self.assertEqual(normalized["sections"][2]["text"], "three")
        self.assertEqual(normalized["source_role"], "ORDINARY_KNOWLEDGE")

    def test_plain_text_uses_nonblank_paragraphs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("alpha\nbeta\n\n\ngamma\n", encoding="utf-8")
            manifest = ingest_source(path, ingested_at=STAMP)
            normalized = normalize_source(path, manifest)

        self.assertEqual([s["text"] for s in normalized["sections"]], ["alpha\nbeta", "gamma"])
        self.assertEqual(normalized["sections"][0]["anchor"]["start_line"], 1)
        self.assertEqual(normalized["sections"][1]["anchor"]["start_line"], 5)

    def test_sections_carry_content_hash_and_byte_length(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("# Alpha\nhello π\n", encoding="utf-8")
            normalized = normalize_source(path, ingest_source(path, ingested_at=STAMP))
        section = normalized["sections"][0]
        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(section["section_byte_length"], len("hello π".encode("utf-8")))
        import hashlib
        self.assertEqual(section["section_sha256"], hashlib.sha256("hello π".encode("utf-8")).hexdigest())

    def test_same_section_text_keeps_hash_when_ordinal_moves(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            one = Path(tmp) / "one.md"
            two = Path(tmp) / "two.md"
            one.write_text("# Stable\nsame body\n", encoding="utf-8")
            two.write_text("# New\nnew body\n\n# Stable\nsame body\n", encoding="utf-8")
            a = normalize_source(one, ingest_source(one, ingested_at=STAMP))
            b = normalize_source(two, ingest_source(two, ingested_at=STAMP))
        sa = next(s for s in a["sections"] if s["heading"] == "Stable")
        sb = next(s for s in b["sections"] if s["heading"] == "Stable")
        self.assertNotEqual(sa["section_id"], sb["section_id"])
        self.assertEqual(sa["section_sha256"], sb["section_sha256"])

    def test_pdf_normalizes_to_stable_page_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "two-pages.pdf"
            path.write_bytes(TEXT_PDF_BYTES)
            manifest = ingest_source(path, ingested_at=STAMP)
            normalized = normalize_source(path, manifest)

        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(normalized["extractor"]["name"], "poppler-pdftotext")
        self.assertEqual(
            [section["anchor"] for section in normalized["sections"]],
            [
                {"kind": "pdf_page", "page_number": 1},
                {"kind": "pdf_page", "page_number": 2},
            ],
        )
        self.assertEqual(
            [section["text"] for section in normalized["sections"]],
            ["Alpha page one\nLine two", "Beta page two"],
        )

    def test_docx_normalizes_to_paragraph_sections_in_document_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.docx"
            path.write_bytes(DOCX_BYTES)
            manifest = ingest_source(path, ingested_at=STAMP)
            normalized = normalize_source(path, manifest)

        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(normalized["extractor"]["name"], "knowledge-forge-docx-xml")
        self.assertEqual(
            [section["anchor"] for section in normalized["sections"]],
            [
                {
                    "kind": "docx_paragraph",
                    "paragraph_number": 1,
                    "source_style_id": "Heading1",
                },
                {
                    "kind": "docx_paragraph",
                    "paragraph_number": 2,
                    "source_style_id": None,
                },
                {
                    "kind": "docx_paragraph",
                    "paragraph_number": 3,
                    "source_style_id": None,
                },
            ],
        )
        self.assertEqual(
            [section["text"] for section in normalized["sections"]],
            ["Alpha Heading", "First line\tTabbed\nSecond line", "Cell text"],
        )
        self.assertIsNone(normalized["sections"][0]["heading"])

    def test_docx_word_style_never_becomes_semantic_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "styled.docx"
            path.write_bytes(DOCX_BYTES)
            normalized = normalize_source(
                path,
                ingest_source(path, ingested_at=STAMP),
            )

        first = normalized["sections"][0]
        self.assertEqual(first["anchor"]["source_style_id"], "Heading1")
        self.assertIsNone(first["heading"])

    def test_epub_normalizes_in_spine_order_without_promoting_markup_to_heading(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(EPUB_BYTES)
            normalized = normalize_source(
                path,
                ingest_source(path, ingested_at=STAMP),
            )

        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(normalized["extractor"]["name"], "knowledge-forge-epub-spine")
        self.assertEqual(
            [section["text"] for section in normalized["sections"]],
            ["Beta Heading", "Beta body\nsecond line", "Alpha Title", "Alpha body"],
        )
        self.assertEqual(
            normalized["sections"][0]["anchor"],
            {
                "kind": "epub_spine_block",
                "spine_index": 1,
                "item_id": "c2",
                "document_path": "OEBPS/text/ch2.xhtml",
                "source_linear": True,
                "block_index": 1,
                "source_tag": "h2",
            },
        )
        self.assertFalse(normalized["sections"][2]["anchor"]["source_linear"])
        self.assertTrue(all(section["heading"] is None for section in normalized["sections"]))

    def test_epub_heading_tag_remains_source_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "book.epub"
            path.write_bytes(EPUB_BYTES)
            normalized = normalize_source(
                path,
                ingest_source(path, ingested_at=STAMP),
            )

        first = normalized["sections"][0]
        self.assertEqual(first["anchor"]["source_tag"], "h2")
        self.assertIsNone(first["heading"])

    def test_technical_text_normalizes_to_descriptive_line_anchors(self) -> None:
        source = "def alpha(value):\n    return value\n\nVALUE = 3\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.py"
            path.write_text(source, encoding="utf-8")
            normalized = normalize_source(
                path,
                ingest_source(path, ingested_at=STAMP),
            )

        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(
            normalized["extractor"]["name"],
            "knowledge-forge-technical-text",
        )
        self.assertEqual(
            [section["anchor"] for section in normalized["sections"]],
            [
                {
                    "kind": "technical_line_range",
                    "start_line": 1,
                    "end_line": 2,
                    "source_language": "python",
                },
                {
                    "kind": "technical_line_range",
                    "start_line": 4,
                    "end_line": 4,
                    "source_language": "python",
                },
            ],
        )
        self.assertTrue(
            all(
                section["heading"] is None
                for section in normalized["sections"]
            )
        )

    def test_refuses_manifest_missing_source_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("# Alpha\nbody\n", encoding="utf-8")
            manifest = ingest_source(path, ingested_at=STAMP)
            manifest.pop("source_role")
            with self.assertRaisesRegex(NormalizationError, "explicit source_role"):
                normalize_source(path, manifest)

    def test_refuses_forged_protected_source_role(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("# Alpha\nbody\n", encoding="utf-8")
            manifest = ingest_source(path, ingested_at=STAMP)
            manifest["source_role"] = "DOCTRINE"
            with self.assertRaisesRegex(NormalizationError, "CANON != SKILL.*DOCTRINE"):
                normalize_source(path, manifest)

    def test_refuses_source_drift_after_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.md"
            path.write_text("# Original\n", encoding="utf-8")
            manifest = ingest_source(path, ingested_at=STAMP)
            path.write_text("# Mutated\n", encoding="utf-8")
            with self.assertRaisesRegex(NormalizationError, "no longer match"):
                normalize_source(path, manifest)


if __name__ == "__main__":
    unittest.main()
