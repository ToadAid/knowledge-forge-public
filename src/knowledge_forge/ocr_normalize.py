"""Explicit OCR-derived PDF normalization.

This is intentionally separate from ``normalize_source``. OCR cannot become a
silent fallback for ordinary PDF text extraction.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

from .canonical_boundary import (
    CanonicalBoundaryError,
    require_ordinary_skill_role,
)
from .normalize import NORMALIZED_SCHEMA_VERSION
from .ocr_extract import (
    OCR_TEXT_ORIGIN,
    OcrExtractionError,
    extract_pdf_pages_ocr,
)


class OcrNormalizationError(ValueError):
    """Raised when OCR-derived normalization cannot preserve source truth."""


def _verify_source_identity(path: Path, manifest: dict[str, Any]) -> None:
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    if (
        manifest.get("source_id") != f"sha256:{digest}"
        or manifest.get("byte_sha256") != digest
    ):
        raise OcrNormalizationError(
            "source bytes no longer match ingest manifest"
        )
    if manifest.get("media_type") != "application/pdf":
        raise OcrNormalizationError(
            "explicit OCR normalization accepts application/pdf only"
        )


def normalize_pdf_with_ocr(
    source: str | Path,
    manifest: dict[str, Any],
    *,
    language: str = "eng",
    dpi: int = 300,
    pdftoppm_binary: str | Path | None = None,
    tesseract_binary: str | Path | None = None,
) -> dict[str, Any]:
    """Normalize one PDF only after an explicit OCR call by the caller."""

    path = Path(source)
    if not path.is_file():
        raise OcrNormalizationError(
            f"OCR source is not a regular file: {path}"
        )

    _verify_source_identity(path, manifest)
    try:
        source_role = require_ordinary_skill_role(manifest.get("source_role"))
    except CanonicalBoundaryError as exc:
        raise OcrNormalizationError(str(exc)) from exc

    try:
        extracted = extract_pdf_pages_ocr(
            path,
            language=language,
            dpi=dpi,
            pdftoppm_binary=pdftoppm_binary,
            tesseract_binary=tesseract_binary,
        )
    except OcrExtractionError as exc:
        raise OcrNormalizationError(str(exc)) from exc

    sections: list[dict[str, Any]] = []
    for page_number, page in enumerate(extracted["pages"], start=1):
        encoded = page.encode("utf-8")
        sections.append(
            {
                "section_id": f"s{page_number:03d}",
                "heading": None,
                "ordinal": page_number,
                "text": page,
                "section_sha256": sha256(encoded).hexdigest(),
                "section_byte_length": len(encoded),
                "anchor": {
                    "kind": "pdf_ocr_page",
                    "page_number": page_number,
                    "text_origin": OCR_TEXT_ORIGIN,
                },
            }
        )

    normalized_text = "\f".join(extracted["pages"])
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "source_id": manifest["source_id"],
        "source_role": source_role,
        "extraction_mode": "OCR_EXPLICIT_EXTERNAL",
        "text_origin": OCR_TEXT_ORIGIN,
        "extractor": extracted["extractor"],
        "normalized_text_sha256": sha256(
            normalized_text.encode("utf-8")
        ).hexdigest(),
        "line_count": len(normalized_text.splitlines()),
        "sections": sections,
    }
