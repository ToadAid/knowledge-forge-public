"""Deterministic text-only PDF extraction for Stage 3 P3A.

P3A uses Poppler ``pdftotext`` as an explicit external extractor.
The exact extractor version is recorded in normalized provenance.
Image-only/scanned PDFs fail closed; OCR remains the separate P3G boundary.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

PDF_EXTRACTOR_NAME = "poppler-pdftotext"
PDF_EXTRACTION_CONTRACT_VERSION = "0.1"
_VERSION_RE = re.compile(r"^pdftotext version (?P<version>\S+)$")


class PdfExtractionError(ValueError):
    """Raised when deterministic text extraction cannot be proven."""


def _binary() -> str:
    binary = shutil.which("pdftotext")
    if binary is None:
        raise PdfExtractionError(
            "pdftotext is required for P3A PDF text extraction"
        )
    return binary


def _version(binary: str) -> str:
    run = subprocess.run(
        [binary, "-v"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    raw = (run.stderr or run.stdout).decode("utf-8", errors="replace")
    first = raw.splitlines()[0].strip() if raw.splitlines() else ""
    match = _VERSION_RE.match(first)
    if run.returncode != 0 or match is None:
        raise PdfExtractionError(
            "cannot determine pdftotext extractor version"
        )
    return match.group("version")


def extract_pdf_pages(source: str | Path) -> dict[str, Any]:
    """Extract page-delimited UTF-8 text without OCR or semantic rewriting."""

    path = Path(source)
    if not path.is_file():
        raise PdfExtractionError(f"PDF source is not a regular file: {path}")

    binary = _binary()
    version = _version(binary)
    run = subprocess.run(
        [binary, "-enc", "UTF-8", str(path), "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if run.returncode != 0:
        detail = run.stderr.decode("utf-8", errors="replace").strip()
        raise PdfExtractionError(
            "pdftotext extraction failed"
            + (f": {detail}" if detail else "")
        )

    try:
        text = run.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PdfExtractionError(
            "pdftotext output is not valid UTF-8"
        ) from exc

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    raw_pages = text.split("\f")
    if raw_pages and raw_pages[-1] == "":
        raw_pages.pop()
    pages = [page.strip("\n") for page in raw_pages]

    if not pages or not any(page.strip() for page in pages):
        raise PdfExtractionError(
            "PDF contains no extractable text; use the explicit optional P3G OCR path"
        )

    return {
        "extractor": {
            "name": PDF_EXTRACTOR_NAME,
            "version": version,
        },
        "contract_version": PDF_EXTRACTION_CONTRACT_VERSION,
        "page_count": len(pages),
        "pages": pages,
    }
