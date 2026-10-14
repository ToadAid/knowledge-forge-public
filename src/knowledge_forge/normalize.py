"""Deterministic text normalization and structural anchors."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
from typing import Any

from .canonical_boundary import CanonicalBoundaryError, require_ordinary_skill_role
from .docx_extract import DocxExtractionError, extract_docx_paragraphs
from .epub_extract import EpubExtractionError, extract_epub_spine
from .pdf_extract import PdfExtractionError, extract_pdf_pages
from .technical_extract import TechnicalExtractionError, extract_technical_blocks

NORMALIZED_SCHEMA_VERSION = "0.6"
EXTRACTOR_NAME = "knowledge-forge-text"
EXTRACTOR_VERSION = "0.1.0"
_MARKDOWN_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


class NormalizationError(ValueError):
    """Raised when a source fails the normalized-source boundary."""


def _verify_identity(path: Path, manifest: dict[str, Any]) -> bytes:
    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    expected = manifest.get("byte_sha256")
    source_id = manifest.get("source_id")
    if expected != digest or source_id != f"sha256:{digest}":
        raise NormalizationError("source bytes no longer match ingest manifest")
    return payload


def _decode_text(payload: bytes) -> str:
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NormalizationError("P1B accepts UTF-8 text only") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _markdown_sections(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings: list[tuple[int, str]] = []
    for index, line in enumerate(lines, start=1):
        match = _MARKDOWN_HEADING.match(line)
        if match:
            headings.append((index, match.group(2).strip()))

    boundaries: list[tuple[int, str | None]] = []
    if not headings or headings[0][0] > 1:
        boundaries.append((1, None))
    boundaries.extend(headings)

    sections: list[dict[str, Any]] = []
    for idx, (start, heading) in enumerate(boundaries):
        next_start = boundaries[idx + 1][0] if idx + 1 < len(boundaries) else len(lines) + 1
        end = max(start, next_start - 1)
        content_start = start + 1 if heading is not None else start
        body = "\n".join(lines[content_start - 1 : end]).strip()
        sections.append(
            {
                "section_id": f"s{idx + 1:03d}",
                "heading": heading,
                "ordinal": idx + 1,
                "text": body,
                "section_sha256": sha256(body.encode("utf-8")).hexdigest(),
                "section_byte_length": len(body.encode("utf-8")),
                "anchor": {
                    "kind": "line_range",
                    "start_line": start,
                    "end_line": end,
                },
            }
        )
    return sections


def _plain_text_sections(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    index = 0
    ordinal = 1
    while index < len(lines):
        while index < len(lines) and not lines[index].strip():
            index += 1
        if index >= len(lines):
            break
        start = index + 1
        chunk: list[str] = []
        while index < len(lines) and lines[index].strip():
            chunk.append(lines[index])
            index += 1
        end = index
        sections.append(
            {
                "section_id": f"s{ordinal:03d}",
                "heading": None,
                "ordinal": ordinal,
                "text": "\n".join(chunk).strip(),
                "section_sha256": sha256("\n".join(chunk).strip().encode("utf-8")).hexdigest(),
                "section_byte_length": len("\n".join(chunk).strip().encode("utf-8")),
                "anchor": {
                    "kind": "line_range",
                    "start_line": start,
                    "end_line": end,
                },
            }
        )
        ordinal += 1
    return sections


def _pdf_sections(pages: list[str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        body = page.strip("\n")
        encoded = body.encode("utf-8")
        sections.append(
            {
                "section_id": f"s{index:03d}",
                "heading": None,
                "ordinal": index,
                "text": body,
                "section_sha256": sha256(encoded).hexdigest(),
                "section_byte_length": len(encoded),
                "anchor": {
                    "kind": "pdf_page",
                    "page_number": index,
                },
            }
        )
    return sections


def _docx_sections(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for ordinal, paragraph in enumerate(paragraphs, start=1):
        body = paragraph["text"]
        encoded = body.encode("utf-8")
        style_id = paragraph.get("style_id")
        sections.append(
            {
                "section_id": f"s{ordinal:03d}",
                "heading": None,
                "ordinal": ordinal,
                "text": body,
                "section_sha256": sha256(encoded).hexdigest(),
                "section_byte_length": len(encoded),
                "anchor": {
                    "kind": "docx_paragraph",
                    "paragraph_number": paragraph["paragraph_number"],
                    "source_style_id": style_id,
                },
            }
        )
    return sections


def _epub_sections(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for document in documents:
        for block in document["blocks"]:
            body = block["text"]
            encoded = body.encode("utf-8")
            ordinal = len(sections) + 1
            sections.append(
                {
                    "section_id": f"s{ordinal:03d}",
                    "heading": None,
                    "ordinal": ordinal,
                    "text": body,
                    "section_sha256": sha256(encoded).hexdigest(),
                    "section_byte_length": len(encoded),
                    "anchor": {
                        "kind": "epub_spine_block",
                        "spine_index": document["spine_index"],
                        "item_id": document["item_id"],
                        "document_path": document["document_path"],
                        "source_linear": document["linear"],
                        "block_index": block["block_index"],
                        "source_tag": block["source_tag"],
                    },
                }
            )
    return sections


def _technical_sections(
    blocks: list[dict[str, Any]],
    *,
    language: str,
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    for ordinal, block in enumerate(blocks, start=1):
        body = block["text"]
        encoded = body.encode("utf-8")
        sections.append(
            {
                "section_id": f"s{ordinal:03d}",
                "heading": None,
                "ordinal": ordinal,
                "text": body,
                "section_sha256": sha256(encoded).hexdigest(),
                "section_byte_length": len(encoded),
                "anchor": {
                    "kind": "technical_line_range",
                    "start_line": block["start_line"],
                    "end_line": block["end_line"],
                    "source_language": language,
                },
            }
        )
    return sections


def normalize_source(source: str | Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """Normalize an ingested Markdown/TXT/PDF/DOCX/EPUB source into anchored sections."""

    path = Path(source)
    if not path.is_file():
        raise NormalizationError(f"source is not a regular file: {path}")

    payload = _verify_identity(path, manifest)
    try:
        source_role = require_ordinary_skill_role(manifest.get("source_role"))
    except CanonicalBoundaryError as exc:
        raise NormalizationError(str(exc)) from exc
    media_type = manifest.get("media_type")

    if media_type == "text/markdown":
        text = _decode_text(payload)
        sections = _markdown_sections(text)
        extractor = {"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION}
        normalized_text = text
    elif media_type == "text/plain":
        text = _decode_text(payload)
        sections = _plain_text_sections(text)
        extractor = {"name": EXTRACTOR_NAME, "version": EXTRACTOR_VERSION}
        normalized_text = text
    elif media_type == "application/pdf":
        try:
            extracted = extract_pdf_pages(path)
        except PdfExtractionError as exc:
            raise NormalizationError(str(exc)) from exc
        sections = _pdf_sections(extracted["pages"])
        extractor = extracted["extractor"]
        normalized_text = "\f".join(extracted["pages"])
    elif media_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            extracted = extract_docx_paragraphs(path)
        except DocxExtractionError as exc:
            raise NormalizationError(str(exc)) from exc
        sections = _docx_sections(extracted["paragraphs"])
        extractor = extracted["extractor"]
        normalized_text = "\n\n".join(item["text"] for item in extracted["paragraphs"])
    elif media_type == "application/epub+zip":
        try:
            extracted = extract_epub_spine(path)
        except EpubExtractionError as exc:
            raise NormalizationError(str(exc)) from exc
        sections = _epub_sections(extracted["documents"])
        extractor = extracted["extractor"]
        normalized_text = "\n\n".join(section["text"] for section in sections)
    elif media_type == "text/x-technical":
        try:
            extracted = extract_technical_blocks(path)
        except TechnicalExtractionError as exc:
            raise NormalizationError(str(exc)) from exc
        sections = _technical_sections(
            extracted["blocks"],
            language=extracted["language"],
        )
        extractor = extracted["extractor"]
        normalized_text = extracted["normalized_text"]
    else:
        raise NormalizationError(f"unsupported manifest media_type: {media_type!r}")

    normalized_digest = sha256(normalized_text.encode("utf-8")).hexdigest()
    return {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "source_id": manifest["source_id"],
        "source_role": source_role,
        "extractor": extractor,
        "normalized_text_sha256": normalized_digest,
        "line_count": len(normalized_text.splitlines()),
        "sections": sections,
    }
