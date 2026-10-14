"""Deterministic folder/document-set ingest without collapsing source identity.

P3D treats a folder as a collection boundary, never as one synthetic document.
Each supported file retains its exact byte-owned ``source_id`` and ordinary
SourceManifest. A separate ``document_set_id`` binds deterministic membership
(relative path + source identity) for collection provenance.

Semantic compilation remains content-bound: relative paths and collection
identity do not become semantic source identity or model authority.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .canonical_boundary import (
    CanonicalBoundaryError,
    ORDINARY_KNOWLEDGE,
    require_ordinary_skill_role,
)
from .compiler import build_compile_request_from_sources
from .ingest import IngestError, SUPPORTED_MEDIA_TYPES, ingest_source
from .normalize import NormalizationError, normalize_source

DOCUMENT_SET_SCHEMA_VERSION = "0.1"
NORMALIZED_DOCUMENT_SET_SCHEMA_VERSION = "0.1"
MAX_DOCUMENT_SET_MEMBERS = 512
MAX_DOCUMENT_SET_BYTES = 512 * 1024 * 1024


class DocumentSetError(ValueError):
    """Raised when a folder cannot cross the deterministic document-set boundary."""


def _valid_source_id(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)


def _safe_relative_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise DocumentSetError("document-set relative_path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise DocumentSetError("document-set relative_path must stay inside the root")
    normalized = path.as_posix()
    if normalized in {"", "."}:
        raise DocumentSetError("document-set relative_path is invalid")
    return normalized


def _validate_member_source_manifest(
    relative_path: str,
    source_manifest: Mapping[str, Any],
) -> None:
    expected_keys = {
        "schema_version",
        "source_id",
        "source_role",
        "name",
        "media_type",
        "byte_sha256",
        "byte_length",
        "ingested_at",
        "published_at",
        "license_hint",
        "origin",
    }
    if set(source_manifest) != expected_keys:
        raise DocumentSetError("document-set member source manifest shape is invalid")
    if source_manifest.get("schema_version") != "0.1":
        raise DocumentSetError("document-set member source schema_version is invalid")

    try:
        source_role = require_ordinary_skill_role(source_manifest.get("source_role"))
    except CanonicalBoundaryError as exc:
        raise DocumentSetError(str(exc)) from exc
    if source_role != ORDINARY_KNOWLEDGE:
        raise DocumentSetError("document-set member source_role must remain ORDINARY_KNOWLEDGE")

    expected_name = PurePosixPath(relative_path).name
    if source_manifest.get("name") != expected_name:
        raise DocumentSetError(
            "document-set member source name must match relative_path basename"
        )

    suffix = PurePosixPath(relative_path).suffix.lower()
    expected_media_type = SUPPORTED_MEDIA_TYPES.get(suffix)
    if expected_media_type is None or source_manifest.get("media_type") != expected_media_type:
        raise DocumentSetError(
            "document-set member media_type must match relative_path suffix"
        )

    source_id = source_manifest.get("source_id")
    if not _valid_source_id(source_id):
        raise DocumentSetError("document-set member source_id is invalid")
    digest = source_id.removeprefix("sha256:")
    if source_manifest.get("byte_sha256") != digest:
        raise DocumentSetError(
            "document-set member byte_sha256 must match source_id"
        )

    byte_length = source_manifest.get("byte_length")
    if (
        isinstance(byte_length, bool)
        or not isinstance(byte_length, int)
        or byte_length < 0
    ):
        raise DocumentSetError("document-set member byte_length is invalid")

    ingested_at = source_manifest.get("ingested_at")
    if not isinstance(ingested_at, str) or not ingested_at:
        raise DocumentSetError("document-set member ingested_at is invalid")

    published_at = source_manifest.get("published_at")
    if published_at is not None and not isinstance(published_at, str):
        raise DocumentSetError("document-set member published_at is invalid")
    license_hint = source_manifest.get("license_hint")
    if license_hint is not None and not isinstance(license_hint, str):
        raise DocumentSetError("document-set member license_hint is invalid")

    origin = source_manifest.get("origin")
    if not isinstance(origin, Mapping):
        raise DocumentSetError("document-set member source origin is invalid")
    if set(origin) != {"kind", "locator"}:
        raise DocumentSetError("document-set member source origin shape is invalid")
    if (
        origin.get("kind") != "document_set_member"
        or origin.get("locator") != relative_path
    ):
        raise DocumentSetError(
            "document-set member origin must bind its exact relative path"
        )


def _identity_payload(members: list[dict[str, Any]]) -> bytes:
    identity = [
        {
            "relative_path": member["relative_path"],
            "source_id": member["source_manifest"]["source_id"],
        }
        for member in members
    ]
    return json.dumps(
        identity,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _document_set_id(members: list[dict[str, Any]]) -> str:
    return "sha256:" + sha256(_identity_payload(members)).hexdigest()


def _scan_supported_files(root: Path) -> list[tuple[str, Path]]:
    if root.is_symlink():
        raise DocumentSetError("document-set root may not be a symlink")
    if not root.is_dir():
        raise DocumentSetError(f"document-set root is not a directory: {root}")

    found: list[tuple[str, Path]] = []
    total_bytes = 0

    for current, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)

        dirnames.sort()
        filenames.sort()

        for dirname in list(dirnames):
            candidate = current_path / dirname
            if candidate.is_symlink():
                raise DocumentSetError(
                    f"document-set symlink is refused: {candidate.relative_to(root).as_posix()}"
                )

        for filename in filenames:
            candidate = current_path / filename
            relative = candidate.relative_to(root).as_posix()

            if candidate.is_symlink():
                raise DocumentSetError(f"document-set symlink is refused: {relative}")
            if not candidate.is_file():
                raise DocumentSetError(
                    f"document-set member is not a regular file: {relative}"
                )
            if candidate.suffix.lower() not in SUPPORTED_MEDIA_TYPES:
                raise DocumentSetError(
                    f"unsupported file in document set: {relative}"
                )

            found.append((relative, candidate))
            total_bytes += candidate.stat().st_size

            if len(found) > MAX_DOCUMENT_SET_MEMBERS:
                raise DocumentSetError("document set exceeds member limit")
            if total_bytes > MAX_DOCUMENT_SET_BYTES:
                raise DocumentSetError("document set exceeds total byte limit")

    found.sort(key=lambda item: item[0])
    if not found:
        raise DocumentSetError("document set contains no supported source files")
    return found


def validate_document_set_manifest(manifest: Mapping[str, Any]) -> None:
    """Validate exact membership identity without consulting the filesystem."""

    if manifest.get("schema_version") != DOCUMENT_SET_SCHEMA_VERSION:
        raise DocumentSetError("document-set schema_version is invalid")

    document_set_id = manifest.get("document_set_id")
    members = manifest.get("members")
    if not _valid_source_id(document_set_id):
        raise DocumentSetError("document_set_id is invalid")
    if not isinstance(members, list) or not members:
        raise DocumentSetError("document set requires non-empty members")

    normalized_members: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    for member in members:
        if not isinstance(member, Mapping):
            raise DocumentSetError("document-set members must be mappings")
        if set(member) != {"relative_path", "source_manifest"}:
            raise DocumentSetError(
                "document-set member must contain relative_path and source_manifest only"
            )

        relative = _safe_relative_path(member.get("relative_path"))
        if relative in seen_paths:
            raise DocumentSetError("document-set relative paths must be unique")

        source_manifest = member.get("source_manifest")
        if not isinstance(source_manifest, Mapping):
            raise DocumentSetError("document-set member requires source_manifest")
        _validate_member_source_manifest(relative, source_manifest)

        normalized_members.append(
            {
                "relative_path": relative,
                "source_manifest": deepcopy(dict(source_manifest)),
            }
        )
        seen_paths.add(relative)

    paths = [member["relative_path"] for member in normalized_members]
    if paths != sorted(paths):
        raise DocumentSetError("document-set members must be sorted by relative_path")

    expected_id = _document_set_id(normalized_members)
    if document_set_id != expected_id:
        raise DocumentSetError("document_set_id does not match member identity")


def ingest_document_set(
    root: str | Path,
    *,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    """Ingest all supported regular files under ``root`` in deterministic order."""

    root_path = Path(root)
    scanned = _scan_supported_files(root_path)
    stamp = ingested_at or datetime.now(timezone.utc)

    members: list[dict[str, Any]] = []
    try:
        for relative, path in scanned:
            source_manifest = ingest_source(path, ingested_at=stamp)
            source_manifest["origin"] = {
                "kind": "document_set_member",
                "locator": relative,
            }
            members.append(
                {
                    "relative_path": relative,
                    "source_manifest": source_manifest,
                }
            )
    except IngestError as exc:
        raise DocumentSetError(str(exc)) from exc

    manifest = {
        "schema_version": DOCUMENT_SET_SCHEMA_VERSION,
        "document_set_id": _document_set_id(members),
        "ingested_at": members[0]["source_manifest"]["ingested_at"],
        "members": members,
    }
    validate_document_set_manifest(manifest)
    return manifest


def normalize_document_set(
    root: str | Path,
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize each exact manifest member while refusing membership/source drift."""

    validate_document_set_manifest(manifest)
    root_path = Path(root)
    scanned = _scan_supported_files(root_path)

    expected_paths = [
        member["relative_path"]
        for member in manifest["members"]
    ]
    actual_paths = [relative for relative, _ in scanned]
    if actual_paths != expected_paths:
        raise DocumentSetError(
            "document-set membership changed after ingest"
        )

    path_by_relative = dict(scanned)
    normalized_members: list[dict[str, Any]] = []
    for member in manifest["members"]:
        relative = member["relative_path"]
        try:
            normalized = normalize_source(
                path_by_relative[relative],
                member["source_manifest"],
            )
        except NormalizationError as exc:
            raise DocumentSetError(
                f"document-set member failed normalization: {relative}: {exc}"
            ) from exc

        normalized_members.append(
            {
                "relative_path": relative,
                "source_manifest": deepcopy(dict(member["source_manifest"])),
                "normalized_source": normalized,
            }
        )

    return {
        "schema_version": NORMALIZED_DOCUMENT_SET_SCHEMA_VERSION,
        "document_set_id": manifest["document_set_id"],
        "members": normalized_members,
    }


def build_document_set_compile_request(
    normalized_set: Mapping[str, Any],
    *,
    knowledge_id: str,
    skill_slug: str,
    compiler_profile: Mapping[str, Any],
    purpose: str = "reference",
) -> dict[str, Any]:
    """Build a path-insensitive multi-source semantic request from a normalized set."""

    if normalized_set.get("schema_version") != NORMALIZED_DOCUMENT_SET_SCHEMA_VERSION:
        raise DocumentSetError("normalized document-set schema_version is invalid")
    if not _valid_source_id(normalized_set.get("document_set_id")):
        raise DocumentSetError("normalized document_set_id is invalid")

    members = normalized_set.get("members")
    if not isinstance(members, list) or not members:
        raise DocumentSetError("normalized document set requires members")

    sources: list[Mapping[str, Any]] = []
    for member in members:
        if not isinstance(member, Mapping):
            raise DocumentSetError("normalized document-set members must be mappings")
        normalized_source = member.get("normalized_source")
        source_manifest = member.get("source_manifest")
        if not isinstance(normalized_source, Mapping) or not isinstance(
            source_manifest, Mapping
        ):
            raise DocumentSetError(
                "normalized document-set member requires source manifest and normalized source"
            )
        if normalized_source.get("source_id") != source_manifest.get("source_id"):
            raise DocumentSetError(
                "normalized document-set member source identity mismatch"
            )
        if normalized_source.get("source_role") != source_manifest.get("source_role"):
            raise DocumentSetError(
                "normalized document-set member source_role mismatch"
            )
        try:
            require_ordinary_skill_role(normalized_source.get("source_role"))
        except CanonicalBoundaryError as exc:
            raise DocumentSetError(str(exc)) from exc
        sources.append(normalized_source)

    # Semantic compilation is intentionally path-insensitive. Sort by immutable
    # source identity, not folder path. Exact duplicate source bytes are harmlessly
    # deduplicated by build_compile_request_from_sources when their sections agree.
    sources.sort(key=lambda source: source["source_id"])

    return build_compile_request_from_sources(
        sources,
        knowledge_id=knowledge_id,
        skill_slug=skill_slug,
        compiler_profile=compiler_profile,
        purpose=purpose,
    )
