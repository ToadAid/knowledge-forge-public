"""Deterministic DOCX text extraction using the Python standard library.

DOCX is treated as an immutable ZIP/XML source container. P3B reads only the
main ``word/document.xml`` part, never unpacks the archive to disk, and never
interprets document content as execution authority.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET
import zipfile

DOCX_EXTRACTOR_NAME = "knowledge-forge-docx-xml"
DOCX_EXTRACTOR_VERSION = "0.1.0"
MAX_DOCUMENT_XML_BYTES = 16 * 1024 * 1024

_WORD_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W = f"{{{_WORD_NS}}}"
_CONTENT_TYPES = "[Content_Types].xml"
_DOCUMENT_XML = "word/document.xml"


class DocxExtractionError(ValueError):
    """Raised when deterministic DOCX extraction cannot be proven."""


def _paragraph_text(paragraph: ET.Element) -> str:
    chunks: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            chunks.append(node.text or "")
        elif node.tag == f"{_W}tab":
            chunks.append("\t")
        elif node.tag in {f"{_W}br", f"{_W}cr"}:
            chunks.append("\n")
    return "".join(chunks).strip()


def _paragraph_style(paragraph: ET.Element) -> str | None:
    p_pr = paragraph.find(f"{_W}pPr")
    if p_pr is None:
        return None
    style = p_pr.find(f"{_W}pStyle")
    if style is None:
        return None
    value = style.get(f"{_W}val")
    return value.strip() if isinstance(value, str) and value.strip() else None


def extract_docx_paragraphs(source: str | Path) -> dict[str, Any]:
    """Extract visible paragraph text from ``word/document.xml`` in order."""

    path = Path(source)
    if not path.is_file():
        raise DocxExtractionError(f"DOCX source is not a regular file: {path}")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            names = set(archive.namelist())
            if _CONTENT_TYPES not in names or _DOCUMENT_XML not in names:
                raise DocxExtractionError(
                    "DOCX requires [Content_Types].xml and word/document.xml"
                )

            info = archive.getinfo(_DOCUMENT_XML)
            if info.file_size > MAX_DOCUMENT_XML_BYTES:
                raise DocxExtractionError(
                    "DOCX word/document.xml exceeds extraction size limit"
                )
            xml_bytes = archive.read(_DOCUMENT_XML)
    except zipfile.BadZipFile as exc:
        raise DocxExtractionError("DOCX is not a valid ZIP container") from exc
    except KeyError as exc:
        raise DocxExtractionError("DOCX main document part is missing") from exc

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise DocxExtractionError("DOCX word/document.xml is malformed XML") from exc

    paragraphs: list[dict[str, Any]] = []
    paragraph_number = 0
    for paragraph in root.iter(f"{_W}p"):
        paragraph_number += 1
        text = _paragraph_text(paragraph)
        if not text:
            continue
        style_id = _paragraph_style(paragraph)
        paragraphs.append(
            {
                "paragraph_number": paragraph_number,
                "text": text,
                "style_id": style_id,
            }
        )

    if not paragraphs:
        raise DocxExtractionError("DOCX contains no extractable paragraph text")

    return {
        "extractor": {
            "name": DOCX_EXTRACTOR_NAME,
            "version": DOCX_EXTRACTOR_VERSION,
        },
        "paragraph_count": len(paragraphs),
        "paragraphs": paragraphs,
    }
