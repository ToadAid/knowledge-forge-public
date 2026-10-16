from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from _platform_support import skip_posix_shebang_tools_on_windows
from unittest.mock import patch

from knowledge_forge.ingest import ingest_source
from knowledge_forge.normalize import NormalizationError, normalize_source
from knowledge_forge.ocr_extract import (
    OCR_TEXT_ORIGIN,
    OcrExtractionError,
    extract_pdf_pages_ocr,
)
from knowledge_forge.ocr_normalize import (
    OcrNormalizationError,
    normalize_pdf_with_ocr,
)

PDF_BYTES = b"%PDF-1.4\n% explicit OCR fixture\n"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | 0o111)


def _fake_tools(root: Path, *, blank: bool = False) -> tuple[Path, Path]:
    pdftoppm = root / "pdftoppm"
    tesseract = root / "tesseract"

    _write_executable(
        pdftoppm,
        """#!/usr/bin/env python3
import pathlib
import sys

if "-v" in sys.argv:
    print("pdftoppm version 24.02.0", file=sys.stderr)
    raise SystemExit(0)

prefix = pathlib.Path(sys.argv[-1])
(prefix.parent / (prefix.name + "-1.png")).write_bytes(b"fake-page-1")
(prefix.parent / (prefix.name + "-2.png")).write_bytes(b"fake-page-2")
""",
    )

    if blank:
        tesseract_body = """#!/usr/bin/env python3
import sys

if "--version" in sys.argv:
    print("tesseract 5.3.4")
    raise SystemExit(0)

print("")
"""
    else:
        tesseract_body = """#!/usr/bin/env python3
import pathlib
import sys

if "--version" in sys.argv:
    print("tesseract 5.3.4")
    raise SystemExit(0)

name = pathlib.Path(sys.argv[1]).name
if name.endswith("-1.png"):
    print("OCR page one")
else:
    print("OCR page two\\nLine two")
"""

    _write_executable(tesseract, tesseract_body)
    return pdftoppm, tesseract


class OcrBoundaryTests(unittest.TestCase):
    @skip_posix_shebang_tools_on_windows
    def test_explicit_external_ocr_records_tools_language_dpi_and_pages(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            pdftoppm, tesseract = _fake_tools(root)

            first = extract_pdf_pages_ocr(
                pdf,
                language="eng",
                dpi=300,
                pdftoppm_binary=pdftoppm,
                tesseract_binary=tesseract,
            )
            second = extract_pdf_pages_ocr(
                pdf,
                language="eng",
                dpi=300,
                pdftoppm_binary=pdftoppm,
                tesseract_binary=tesseract,
            )

        self.assertEqual(first, second)
        self.assertEqual(first["contract_version"], "0.1")
        self.assertEqual(first["text_origin"], OCR_TEXT_ORIGIN)
        self.assertEqual(first["page_count"], 2)
        self.assertEqual(
            first["pages"],
            ["OCR page one", "OCR page two\nLine two"],
        )
        self.assertEqual(
            first["extractor"]["renderer"],
            {
                "name": "poppler-pdftoppm",
                "version": "24.02.0",
                "dpi": 300,
            },
        )
        self.assertEqual(
            first["extractor"]["ocr"],
            {
                "name": "tesseract",
                "version": "5.3.4",
                "language": "eng",
                "page_segmentation_mode": 6,
            },
        )

    @skip_posix_shebang_tools_on_windows
    def test_ocr_normalization_keeps_source_identity_and_marks_every_page_derived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = ingest_source(pdf)
            pdftoppm, tesseract = _fake_tools(root)

            normalized = normalize_pdf_with_ocr(
                pdf,
                manifest,
                pdftoppm_binary=pdftoppm,
                tesseract_binary=tesseract,
            )

        self.assertEqual(normalized["source_id"], manifest["source_id"])
        self.assertEqual(normalized["source_role"], "ORDINARY_KNOWLEDGE")
        self.assertEqual(normalized["schema_version"], "0.6")
        self.assertEqual(
            normalized["extraction_mode"],
            "OCR_EXPLICIT_EXTERNAL",
        )
        self.assertEqual(
            normalized["text_origin"],
            "OCR_DERIVED_UNVERIFIED",
        )
        self.assertEqual(
            [section["anchor"] for section in normalized["sections"]],
            [
                {
                    "kind": "pdf_ocr_page",
                    "page_number": 1,
                    "text_origin": "OCR_DERIVED_UNVERIFIED",
                },
                {
                    "kind": "pdf_ocr_page",
                    "page_number": 2,
                    "text_origin": "OCR_DERIVED_UNVERIFIED",
                },
            ],
        )

    def test_ocr_normalization_refuses_protected_source_role_before_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = deepcopy(ingest_source(pdf))
            manifest["source_role"] = "DOCTRINE"

            with patch(
                "knowledge_forge.ocr_normalize.extract_pdf_pages_ocr"
            ) as mocked_ocr:
                with self.assertRaisesRegex(
                    OcrNormalizationError,
                    "CANON != SKILL",
                ):
                    normalize_pdf_with_ocr(pdf, manifest)

        mocked_ocr.assert_not_called()

    def test_ocr_normalization_refuses_missing_source_role_before_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = deepcopy(ingest_source(pdf))
            manifest.pop("source_role")

            with patch(
                "knowledge_forge.ocr_normalize.extract_pdf_pages_ocr"
            ) as mocked_ocr:
                with self.assertRaisesRegex(
                    OcrNormalizationError,
                    "requires explicit source_role",
                ):
                    normalize_pdf_with_ocr(pdf, manifest)

        mocked_ocr.assert_not_called()

    def test_default_normalizer_never_calls_explicit_ocr_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = ingest_source(pdf)

            with patch(
                "knowledge_forge.ocr_extract.extract_pdf_pages_ocr"
            ) as mocked_ocr:
                with self.assertRaises(NormalizationError):
                    normalize_source(pdf, manifest)

        mocked_ocr.assert_not_called()

    def test_source_drift_refuses_before_ocr_tools_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = ingest_source(pdf)
            pdf.write_bytes(PDF_BYTES + b"drift")
            pdftoppm, tesseract = _fake_tools(root)

            with self.assertRaisesRegex(
                OcrNormalizationError,
                "source bytes no longer match",
            ):
                normalize_pdf_with_ocr(
                    pdf,
                    manifest,
                    pdftoppm_binary=pdftoppm,
                    tesseract_binary=tesseract,
                )

    def test_wrong_media_type_refuses_explicit_ocr_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            manifest = deepcopy(ingest_source(pdf))
            manifest["media_type"] = "text/plain"
            pdftoppm, tesseract = _fake_tools(root)

            with self.assertRaisesRegex(
                OcrNormalizationError,
                "application/pdf only",
            ):
                normalize_pdf_with_ocr(
                    pdf,
                    manifest,
                    pdftoppm_binary=pdftoppm,
                    tesseract_binary=tesseract,
                )

    def test_unsafe_language_and_out_of_range_dpi_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            pdftoppm, tesseract = _fake_tools(root)

            with self.assertRaisesRegex(OcrExtractionError, "safe language id"):
                extract_pdf_pages_ocr(
                    pdf,
                    language="eng;rm",
                    pdftoppm_binary=pdftoppm,
                    tesseract_binary=tesseract,
                )
            with self.assertRaisesRegex(OcrExtractionError, "72, 600"):
                extract_pdf_pages_ocr(
                    pdf,
                    dpi=1200,
                    pdftoppm_binary=pdftoppm,
                    tesseract_binary=tesseract,
                )

    @skip_posix_shebang_tools_on_windows
    def test_blank_ocr_output_refuses_instead_of_emitting_empty_knowledge(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            pdftoppm, tesseract = _fake_tools(root, blank=True)

            with self.assertRaisesRegex(
                OcrExtractionError,
                "no nonblank text",
            ):
                extract_pdf_pages_ocr(
                    pdf,
                    pdftoppm_binary=pdftoppm,
                    tesseract_binary=tesseract,
                )

    def test_explicit_missing_binary_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "scan.pdf"
            pdf.write_bytes(PDF_BYTES)
            missing = root / "missing-pdftoppm"
            _, tesseract = _fake_tools(root)

            with self.assertRaisesRegex(
                OcrExtractionError,
                "not an executable regular file",
            ):
                extract_pdf_pages_ocr(
                    pdf,
                    pdftoppm_binary=missing,
                    tesseract_binary=tesseract,
                )


if __name__ == "__main__":
    unittest.main()
