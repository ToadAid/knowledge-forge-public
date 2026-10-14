"""Deterministic source identity for Knowledge Forge.

The ingest layer binds immutable byte identity and minimal metadata. Stage 3
adds PDF, DOCX, EPUB, and technical-text source types without changing the
identity law: exact original file bytes still define source_id; extraction
happens later in normalize.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .canonical_boundary import (
    CanonicalBoundaryError,
    ORDINARY_KNOWLEDGE,
    classify_source_role,
)
from .technical_extract import TECHNICAL_SUFFIX_TO_MEDIA_TYPE

SCHEMA_VERSION = "0.1"
SUPPORTED_MEDIA_TYPES = {
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".epub": "application/epub+zip",
    **TECHNICAL_SUFFIX_TO_MEDIA_TYPE,
}


class IngestError(ValueError):
    """Raised when a source cannot enter the deterministic ingest boundary."""


def _utc_rfc3339(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise IngestError("ingested_at must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ingest_source(
    source: str | Path,
    *,
    ingested_at: datetime | None = None,
    source_role: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic identity manifest for one local source.

    Identity derives only from exact source bytes. The timestamp is lifecycle
    metadata and therefore deliberately excluded from ``source_id``.
    """

    path = Path(source)
    if not path.is_file():
        raise IngestError(f"source is not a regular file: {path}")

    try:
        classified_role = classify_source_role(path, declared_role=source_role)
    except CanonicalBoundaryError as exc:
        raise IngestError(str(exc)) from exc
    if classified_role != ORDINARY_KNOWLEDGE:
        raise IngestError(
            f"CANON != SKILL: protected source_role {classified_role} cannot enter ordinary Knowledge Forge ingest"
        )

    suffix = path.suffix.lower()
    media_type = SUPPORTED_MEDIA_TYPES.get(suffix)
    if media_type is None:
        allowed = ", ".join(sorted(SUPPORTED_MEDIA_TYPES))
        raise IngestError(f"unsupported source type {suffix or '<none>'}; expected {allowed}")

    payload = path.read_bytes()
    digest = sha256(payload).hexdigest()
    stamp = _utc_rfc3339(ingested_at or datetime.now(timezone.utc))

    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": f"sha256:{digest}",
        "source_role": classified_role,
        "name": path.name,
        "media_type": media_type,
        "byte_sha256": digest,
        "byte_length": len(payload),
        "ingested_at": stamp,
        "published_at": None,
        "license_hint": None,
        "origin": {
            "kind": "local_file",
            "locator": path.name,
        },
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Create a Knowledge Forge source manifest")
    parser.add_argument("source", help="Markdown, plain-text, PDF, DOCX, EPUB, or technical-text source")
    args = parser.parse_args(argv)
    manifest = ingest_source(args.source)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
