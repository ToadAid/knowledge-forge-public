"""Explicit optional OCR boundary for scanned/image-only PDFs.

P3G never runs through the default PDF normalizer. Callers must deliberately
select this module and provide or have external Poppler/Tesseract binaries.
OCR-derived text remains visibly marked as derived/unverified source text.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any

OCR_EXTRACTOR_NAME = "poppler-pdftoppm+tesseract"
OCR_EXTRACTION_CONTRACT_VERSION = "0.1"
OCR_TEXT_ORIGIN = "OCR_DERIVED_UNVERIFIED"
DEFAULT_DPI = 300
DEFAULT_LANGUAGE = "eng"
TESSERACT_PSM = 6

_PDFTOPPM_VERSION_RE = re.compile(r"^pdftoppm version (?P<version>\S+)$")
_TESSERACT_VERSION_RE = re.compile(r"^tesseract (?P<version>\S+)")
_LANGUAGE_RE = re.compile(r"^[A-Za-z0-9_.+-]+$")
_PAGE_RE = re.compile(r"^page-(?P<number>[1-9][0-9]*)\.png$")


class OcrExtractionError(ValueError):
    """Raised when the explicit external OCR boundary cannot be proven."""


def _resolve_binary(explicit: str | Path | None, name: str) -> str:
    if explicit is None:
        resolved = shutil.which(name)
        if resolved is None:
            raise OcrExtractionError(
                f"{name} is unavailable; P3G OCR is optional and explicit"
            )
        return resolved

    path = Path(explicit)
    if not path.is_file() or not os.access(path, os.X_OK):
        raise OcrExtractionError(
            f"{name} binary is not an executable regular file: {path}"
        )
    return str(path)


def _version(
    binary: str,
    args: list[str],
    pattern: re.Pattern[str],
    label: str,
) -> str:
    run = subprocess.run(
        [binary, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    raw = (run.stdout or run.stderr).decode("utf-8", errors="replace")
    lines = raw.splitlines()
    first = lines[0].strip() if lines else ""
    match = pattern.match(first)
    if run.returncode != 0 or match is None:
        raise OcrExtractionError(f"cannot determine {label} version")
    return match.group("version")


def _page_number(path: Path) -> int:
    match = _PAGE_RE.match(path.name)
    if match is None:
        raise OcrExtractionError(
            f"unexpected rendered OCR page filename: {path.name}"
        )
    return int(match.group("number"))


def extract_pdf_pages_ocr(
    source: str | Path,
    *,
    language: str = DEFAULT_LANGUAGE,
    dpi: int = DEFAULT_DPI,
    pdftoppm_binary: str | Path | None = None,
    tesseract_binary: str | Path | None = None,
) -> dict[str, Any]:
    """Render a PDF and OCR each page through explicit external tools."""

    path = Path(source)
    if not path.is_file():
        raise OcrExtractionError(f"OCR source is not a regular file: {path}")

    if not isinstance(language, str) or not _LANGUAGE_RE.fullmatch(language):
        raise OcrExtractionError("OCR language must be an explicit safe language id")
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 600:
        raise OcrExtractionError("OCR dpi must be an integer in [72, 600]")

    pdftoppm = _resolve_binary(pdftoppm_binary, "pdftoppm")
    tesseract = _resolve_binary(tesseract_binary, "tesseract")

    pdftoppm_version = _version(
        pdftoppm,
        ["-v"],
        _PDFTOPPM_VERSION_RE,
        "pdftoppm",
    )
    tesseract_version = _version(
        tesseract,
        ["--version"],
        _TESSERACT_VERSION_RE,
        "tesseract",
    )

    with tempfile.TemporaryDirectory(prefix="knowledge-forge-ocr-") as tmp:
        temp_root = Path(tmp)
        prefix = temp_root / "page"

        render = subprocess.run(
            [
                pdftoppm,
                "-png",
                "-r",
                str(dpi),
                str(path),
                str(prefix),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if render.returncode != 0:
            detail = render.stderr.decode("utf-8", errors="replace").strip()
            raise OcrExtractionError(
                "pdftoppm OCR rendering failed"
                + (f": {detail}" if detail else "")
            )

        rendered = sorted(
            temp_root.glob("page-*.png"),
            key=_page_number,
        )
        if not rendered:
            raise OcrExtractionError("OCR renderer produced no PDF pages")

        expected_numbers = list(range(1, len(rendered) + 1))
        actual_numbers = [_page_number(item) for item in rendered]
        if actual_numbers != expected_numbers:
            raise OcrExtractionError(
                "OCR rendered page numbering is not contiguous from page 1"
            )

        pages: list[str] = []
        for image in rendered:
            run = subprocess.run(
                [
                    tesseract,
                    str(image),
                    "stdout",
                    "-l",
                    language,
                    "--psm",
                    str(TESSERACT_PSM),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if run.returncode != 0:
                detail = run.stderr.decode("utf-8", errors="replace").strip()
                raise OcrExtractionError(
                    f"tesseract OCR failed for page {_page_number(image)}"
                    + (f": {detail}" if detail else "")
                )
            try:
                text = run.stdout.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise OcrExtractionError(
                    "tesseract OCR output is not valid UTF-8"
                ) from exc
            pages.append(
                text.replace("\r\n", "\n")
                .replace("\r", "\n")
                .strip("\n\f")
            )

    if not any(page.strip() for page in pages):
        raise OcrExtractionError(
            "OCR produced no nonblank text; no knowledge was emitted"
        )

    return {
        "contract_version": OCR_EXTRACTION_CONTRACT_VERSION,
        "text_origin": OCR_TEXT_ORIGIN,
        "extractor": {
            "name": OCR_EXTRACTOR_NAME,
            "renderer": {
                "name": "poppler-pdftoppm",
                "version": pdftoppm_version,
                "dpi": dpi,
            },
            "ocr": {
                "name": "tesseract",
                "version": tesseract_version,
                "language": language,
                "page_segmentation_mode": TESSERACT_PSM,
            },
        },
        "page_count": len(pages),
        "pages": pages,
    }
