from __future__ import annotations

from pathlib import Path
from typing import Any

TECHNICAL_EXTRACTOR_NAME = "knowledge-forge-technical-text"
TECHNICAL_EXTRACTOR_VERSION = "0.1.0"
MAX_TECHNICAL_BYTES = 8 * 1024 * 1024
MAX_BLOCK_LINES = 120

TECHNICAL_SUFFIX_LANGUAGE = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".jsx": "javascript", ".json": "json", ".toml": "toml",
    ".yaml": "yaml", ".yml": "yaml", ".sh": "shell", ".bash": "shell",
    ".zsh": "shell", ".rs": "rust", ".go": "go", ".sol": "solidity",
    ".sql": "sql", ".xml": "xml", ".html": "html", ".htm": "html",
    ".css": "css", ".ini": "ini", ".cfg": "config", ".conf": "config",
    ".c": "c", ".h": "c-header", ".cc": "cpp", ".cpp": "cpp",
    ".hpp": "cpp-header", ".java": "java", ".kt": "kotlin",
    ".swift": "swift", ".rb": "ruby", ".php": "php",
}
TECHNICAL_SUFFIX_TO_MEDIA_TYPE = {
    suffix: "text/x-technical" for suffix in TECHNICAL_SUFFIX_LANGUAGE
}


class TechnicalExtractionError(ValueError):
    pass


def _flush(blocks: list[dict[str, Any]], chunk: list[str], start: int | None, end: int | None) -> None:
    if chunk and start is not None and end is not None:
        blocks.append({
            "block_index": len(blocks) + 1,
            "start_line": start,
            "end_line": end,
            "text": "\n".join(chunk),
        })


def extract_technical_blocks(source: str | Path) -> dict[str, Any]:
    path = Path(source)
    if not path.is_file():
        raise TechnicalExtractionError(f"technical source is not a regular file: {path}")
    language = TECHNICAL_SUFFIX_LANGUAGE.get(path.suffix.lower())
    if language is None:
        raise TechnicalExtractionError("unsupported technical source suffix")
    if path.stat().st_size > MAX_TECHNICAL_BYTES:
        raise TechnicalExtractionError("technical source exceeds extraction size limit")

    try:
        text = path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise TechnicalExtractionError("technical source must be UTF-8 text") from exc

    normalized_text = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[dict[str, Any]] = []
    chunk: list[str] = []
    start: int | None = None
    end: int | None = None

    for line_no, line in enumerate(normalized_text.splitlines(), start=1):
        if not line.strip():
            _flush(blocks, chunk, start, end)
            chunk, start, end = [], None, None
            continue
        if len(chunk) >= MAX_BLOCK_LINES:
            _flush(blocks, chunk, start, end)
            chunk, start, end = [], None, None
        if start is None:
            start = line_no
        chunk.append(line)
        end = line_no
    _flush(blocks, chunk, start, end)

    return {
        "extractor": {
            "name": TECHNICAL_EXTRACTOR_NAME,
            "version": TECHNICAL_EXTRACTOR_VERSION,
        },
        "language": language,
        "normalized_text": normalized_text,
        "blocks": blocks,
    }
