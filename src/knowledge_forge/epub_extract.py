"""Deterministic EPUB spine extraction using only Python standard-library parsers.

EPUB is an immutable ZIP container. P3C follows the package-declared reading
spine and extracts local XHTML block text only. It never unpacks to disk,
fetches remote resources, executes scripts, or treats source markup as
execution authority.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import posixpath
import re
from typing import Any
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

EPUB_EXTRACTOR_NAME = "knowledge-forge-epub-spine"
EPUB_EXTRACTOR_VERSION = "0.1.0"

MAX_ARCHIVE_MEMBERS = 4096
MAX_CONTAINER_XML_BYTES = 1024 * 1024
MAX_PACKAGE_XML_BYTES = 4 * 1024 * 1024
MAX_SPINE_DOCUMENT_BYTES = 8 * 1024 * 1024
MAX_TOTAL_SPINE_BYTES = 64 * 1024 * 1024
MAX_SPINE_ITEMS = 512

_CONTAINER_PATH = "META-INF/container.xml"
_MIMETYPE_PATH = "mimetype"
_MIMETYPE = b"application/epub+zip"
_BLOCK_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "pre", "td", "th", "dt", "dd", "figcaption",
}
_EXCLUDED_TAGS = {"script", "style", "noscript", "svg"}
_FORBIDDEN_XML = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class EpubExtractionError(ValueError):
    """Raised when deterministic EPUB reading-order extraction cannot be proven."""


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _read_member(
    archive: zipfile.ZipFile,
    member: str,
    *,
    max_bytes: int,
) -> bytes:
    try:
        info = archive.getinfo(member)
    except KeyError as exc:
        raise EpubExtractionError(f"EPUB member is missing: {member}") from exc
    if info.flag_bits & 0x1:
        raise EpubExtractionError(f"EPUB encrypted member is unsupported: {member}")
    if info.file_size > max_bytes:
        raise EpubExtractionError(f"EPUB member exceeds extraction size limit: {member}")
    return archive.read(info)


def _parse_xml(payload: bytes, *, label: str) -> ET.Element:
    if _FORBIDDEN_XML.search(payload):
        raise EpubExtractionError(f"{label} contains forbidden DTD/entity declarations")
    try:
        return ET.fromstring(payload)
    except ET.ParseError as exc:
        raise EpubExtractionError(f"{label} is malformed XML/XHTML") from exc


def _safe_archive_path(base_dir: str, href: str, *, label: str) -> str:
    if not isinstance(href, str) or not href.strip():
        raise EpubExtractionError(f"{label} requires a non-empty local path")

    split = urlsplit(href.strip())
    if split.scheme or split.netloc or split.query:
        raise EpubExtractionError(f"{label} must be a local archive path")

    decoded = unquote(split.path)
    if not decoded or decoded.startswith("/") or "\\" in decoded or "\x00" in decoded:
        raise EpubExtractionError(f"{label} is not a safe archive path")

    if ".." in PurePosixPath(decoded).parts:
        raise EpubExtractionError(f"{label} may not traverse parent directories")

    candidate = posixpath.normpath(posixpath.join(base_dir, decoded))
    if candidate in {"", "."} or candidate.startswith("../") or candidate.startswith("/"):
        raise EpubExtractionError(f"{label} escapes the EPUB package")
    return candidate


def _visible_text(element: ET.Element) -> str:
    pieces: list[str] = []

    def visit(node: ET.Element) -> None:
        if _local_name(node.tag) in _EXCLUDED_TAGS:
            return
        if node.text:
            pieces.append(node.text)
        for child in list(node):
            if _local_name(child.tag) == "br":
                pieces.append("\n")
            else:
                visit(child)
            if child.tail:
                pieces.append(child.tail)

    visit(element)
    raw = "".join(pieces).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    return "\n".join(line for line in lines if line).strip()


def _has_block_descendant(element: ET.Element) -> bool:
    return any(
        descendant is not element and _local_name(descendant.tag) in _BLOCK_TAGS
        for descendant in element.iter()
    )


def _extract_xhtml_blocks(payload: bytes, *, label: str) -> list[dict[str, Any]]:
    root = _parse_xml(payload, label=label)
    blocks: list[dict[str, Any]] = []

    for element in root.iter():
        tag = _local_name(element.tag)
        if tag not in _BLOCK_TAGS or _has_block_descendant(element):
            continue
        text = _visible_text(element)
        if text:
            blocks.append(
                {
                    "block_index": len(blocks) + 1,
                    "text": text,
                    "source_tag": tag,
                }
            )

    if not blocks:
        body = next(
            (element for element in root.iter() if _local_name(element.tag) == "body"),
            None,
        )
        if body is not None:
            text = _visible_text(body)
            if text:
                blocks.append(
                    {"block_index": 1, "text": text, "source_tag": "body"}
                )

    if not blocks:
        raise EpubExtractionError(f"{label} contains no extractable spine text")
    return blocks


def _single_child(parent: ET.Element, local_name: str, *, label: str) -> ET.Element:
    matches = [child for child in list(parent) if _local_name(child.tag) == local_name]
    if len(matches) != 1:
        raise EpubExtractionError(f"{label} requires exactly one {local_name}")
    return matches[0]


def extract_epub_spine(source: str | Path) -> dict[str, Any]:
    """Extract local XHTML blocks in exact OPF spine order."""

    path = Path(source)
    if not path.is_file():
        raise EpubExtractionError(f"EPUB source is not a regular file: {path}")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ARCHIVE_MEMBERS:
                raise EpubExtractionError("EPUB archive has too many members")
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise EpubExtractionError("EPUB archive contains duplicate member names")
            names_set = set(names)

            if not infos or infos[0].filename != _MIMETYPE_PATH:
                raise EpubExtractionError("EPUB mimetype must be the first archive member")
            if infos[0].compress_type != zipfile.ZIP_STORED:
                raise EpubExtractionError("EPUB mimetype member must be uncompressed")

            mimetype = _read_member(archive, _MIMETYPE_PATH, max_bytes=256)
            if mimetype != _MIMETYPE:
                raise EpubExtractionError("EPUB mimetype member must match exact bytes")

            container_payload = _read_member(
                archive, _CONTAINER_PATH, max_bytes=MAX_CONTAINER_XML_BYTES
            )
            container = _parse_xml(
                container_payload, label="EPUB META-INF/container.xml"
            )
            rootfiles = [
                node for node in container.iter() if _local_name(node.tag) == "rootfile"
            ]
            if len(rootfiles) != 1:
                raise EpubExtractionError(
                    "EPUB container must declare exactly one package rootfile"
                )
            rootfile_media_type = (rootfiles[0].get("media-type") or "").strip()
            if rootfile_media_type != "application/oebps-package+xml":
                raise EpubExtractionError(
                    "EPUB rootfile media-type must be application/oebps-package+xml"
                )
            package_path = _safe_archive_path(
                "",
                rootfiles[0].get("full-path", ""),
                label="EPUB package rootfile",
            )

            package_payload = _read_member(
                archive, package_path, max_bytes=MAX_PACKAGE_XML_BYTES
            )
            package = _parse_xml(
                package_payload, label=f"EPUB package {package_path}"
            )
            package_dir = str(PurePosixPath(package_path).parent)
            if package_dir == ".":
                package_dir = ""

            manifest_element = _single_child(
                package, "manifest", label="EPUB package"
            )
            spine_element = _single_child(package, "spine", label="EPUB package")

            manifest: dict[str, dict[str, str]] = {}
            for node in list(manifest_element):
                if _local_name(node.tag) != "item":
                    continue
                item_id = (node.get("id") or "").strip()
                href = (node.get("href") or "").strip()
                media_type = (node.get("media-type") or "").strip()
                if not item_id or not href or not media_type:
                    raise EpubExtractionError("EPUB manifest item is incomplete")
                if item_id in manifest:
                    raise EpubExtractionError("EPUB manifest ids must be unique")
                manifest[item_id] = {"href": href, "media_type": media_type}

            spine_nodes = [
                node for node in list(spine_element) if _local_name(node.tag) == "itemref"
            ]
            if not spine_nodes:
                raise EpubExtractionError("EPUB package spine is empty")
            if len(spine_nodes) > MAX_SPINE_ITEMS:
                raise EpubExtractionError("EPUB spine exceeds item limit")

            documents: list[dict[str, Any]] = []
            total_bytes = 0
            for spine_index, node in enumerate(spine_nodes, start=1):
                idref = (node.get("idref") or "").strip()
                item = manifest.get(idref)
                if item is None:
                    raise EpubExtractionError(
                        f"EPUB spine idref is missing from manifest: {idref}"
                    )
                if item["media_type"] != "application/xhtml+xml":
                    raise EpubExtractionError(
                        "P3C accepts application/xhtml+xml spine items only"
                    )

                member = _safe_archive_path(
                    package_dir,
                    item["href"],
                    label=f"EPUB manifest href for {idref}",
                )
                if member not in names_set:
                    raise EpubExtractionError(f"EPUB spine member is missing: {member}")

                info = archive.getinfo(member)
                total_bytes += info.file_size
                if total_bytes > MAX_TOTAL_SPINE_BYTES:
                    raise EpubExtractionError(
                        "EPUB total spine bytes exceed extraction limit"
                    )
                payload = _read_member(
                    archive, member, max_bytes=MAX_SPINE_DOCUMENT_BYTES
                )
                blocks = _extract_xhtml_blocks(
                    payload, label=f"EPUB spine document {member}"
                )
                documents.append(
                    {
                        "spine_index": spine_index,
                        "item_id": idref,
                        "document_path": member,
                        "linear": (node.get("linear") or "yes").lower() != "no",
                        "blocks": blocks,
                    }
                )

    except zipfile.BadZipFile as exc:
        raise EpubExtractionError("EPUB is not a valid ZIP container") from exc

    if not documents:
        raise EpubExtractionError("EPUB produced no spine documents")

    return {
        "extractor": {
            "name": EPUB_EXTRACTOR_NAME,
            "version": EPUB_EXTRACTOR_VERSION,
        },
        "package_path": package_path,
        "spine_count": len(documents),
        "documents": documents,
    }
