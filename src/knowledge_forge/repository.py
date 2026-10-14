from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import subprocess
import tempfile
from typing import Any, Mapping

from .canonical_boundary import (
    CanonicalBoundaryError,
    ORDINARY_KNOWLEDGE,
    classify_source_role,
)
from .compiler import build_compile_request_from_sources
from .ingest import SUPPORTED_MEDIA_TYPES, ingest_source
from .normalize import NormalizationError, normalize_source

REPOSITORY_MANIFEST_SCHEMA_VERSION = "0.1"
NORMALIZED_REPOSITORY_SCHEMA_VERSION = "0.1"
MAX_REPOSITORY_MEMBERS = 2048
MAX_REPOSITORY_SOURCE_BYTES = 8 * 1024 * 1024
MAX_REPOSITORY_TOTAL_BYTES = 128 * 1024 * 1024


class RepositoryIngestError(ValueError):
    pass


def _git(root: Path, *args: str) -> bytes:
    try:
        cp = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise RepositoryIngestError("git executable is unavailable") from exc
    if cp.returncode != 0:
        err = cp.stderr.decode("utf-8", errors="replace").strip()
        raise RepositoryIngestError(f"git command failed: {' '.join(args)}: {err}")
    return cp.stdout


def _git_text(root: Path, *args: str) -> str:
    try:
        return _git(root, *args).decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise RepositoryIngestError("git metadata is not UTF-8") from exc


def _source_id_ok(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value[7:]
    return len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)


def _oid_ok(value: Any, fmt: str) -> bool:
    n = 64 if fmt == "sha256" else 40
    return isinstance(value, str) and len(value) == n and all(c in "0123456789abcdef" for c in value)


def _safe_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise RepositoryIngestError("repository relative_path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise RepositoryIngestError("repository relative_path escapes repository")
    return path.as_posix()


def _root(root: str | Path) -> Path:
    candidate = Path(root)
    if candidate.is_symlink() or not candidate.is_dir():
        raise RepositoryIngestError("repository root must be a real directory")
    top = Path(_git_text(candidate, "rev-parse", "--show-toplevel"))
    if top.resolve() != candidate.resolve():
        raise RepositoryIngestError("repository root must be the Git top-level directory")
    return top


def _format(root: Path) -> str:
    fmt = _git_text(root, "rev-parse", "--show-object-format")
    if fmt not in {"sha1", "sha256"}:
        raise RepositoryIngestError("unsupported Git object format")
    return fmt


def _head(root: Path, revision: str, fmt: str) -> str:
    head = _git_text(root, "rev-parse", "--verify", f"{revision}^{{commit}}")
    if not _oid_ok(head, fmt):
        raise RepositoryIngestError("repository commit id is invalid")
    return head


def _scan(root: Path, head: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    raw = _git(root, "ls-tree", "-r", "-z", "--full-tree", head)
    included: list[dict[str, str]] = []
    excluded: list[dict[str, str]] = []
    for record in raw.split(b"\x00"):
        if not record:
            continue
        try:
            header, raw_path = record.split(b"\t", 1)
            mode_b, type_b, oid_b = header.split(b" ", 2)
            mode = mode_b.decode("ascii")
            obj_type = type_b.decode("ascii")
            oid = oid_b.decode("ascii")
            relative = _safe_path(raw_path.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise RepositoryIngestError("repository tree entry is malformed") from exc

        if mode == "120000":
            excluded.append({"relative_path": relative, "reason": "symlink"})
        elif obj_type != "blob":
            excluded.append({"relative_path": relative, "reason": "gitlink_or_non_blob"})
        elif PurePosixPath(relative).suffix.lower() not in SUPPORTED_MEDIA_TYPES:
            excluded.append({"relative_path": relative, "reason": "unsupported_source_type"})
        else:
            included.append({"relative_path": relative, "git_mode": mode, "git_blob": oid})

    included.sort(key=lambda x: x["relative_path"])
    excluded.sort(key=lambda x: x["relative_path"])
    if not included:
        raise RepositoryIngestError("repository has no supported tracked sources")
    if len(included) > MAX_REPOSITORY_MEMBERS:
        raise RepositoryIngestError("repository exceeds member limit")
    return included, excluded


def _blob(root: Path, oid: str) -> bytes:
    data = _git(root, "cat-file", "blob", oid)
    if len(data) > MAX_REPOSITORY_SOURCE_BYTES:
        raise RepositoryIngestError("repository source exceeds per-file byte limit")
    return data


def _repository_source_role(relative: str) -> str:
    try:
        source_role = classify_source_role(PurePosixPath(relative))
    except CanonicalBoundaryError as exc:
        raise RepositoryIngestError(
            f"repository member has invalid source role: {relative}"
        ) from exc
    if source_role != ORDINARY_KNOWLEDGE:
        raise RepositoryIngestError(
            f"CANON != SKILL: repository member '{relative}' has protected "
            f"source_role '{source_role}' and cannot enter the skill pipeline"
        )
    return source_role


def _source_manifest(data: bytes, *, relative: str, head: str, blob: str, stamp: datetime) -> dict[str, Any]:
    source_role = _repository_source_role(relative)
    suffix = PurePosixPath(relative).suffix.lower()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"source{suffix}"
        path.write_bytes(data)
        manifest = ingest_source(path, ingested_at=stamp)
    manifest["name"] = PurePosixPath(relative).name
    manifest["source_role"] = source_role
    manifest["origin"] = {
        "kind": "repository_member",
        "locator": relative,
        "repository_head": head,
        "git_blob": blob,
    }
    return manifest


def _snapshot_id(fmt: str, head: str, members: list[dict[str, Any]], excluded: list[dict[str, str]]) -> str:
    payload = {
        "object_format": fmt,
        "repository_head": head,
        "members": [
            {
                "relative_path": m["relative_path"],
                "git_mode": m["git_mode"],
                "git_blob": m["git_blob"],
                "source_id": m["source_manifest"]["source_id"],
            }
            for m in members
        ],
        "excluded": excluded,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + sha256(encoded).hexdigest()


def _validate_source(relative: str, manifest: Mapping[str, Any], *, head: str, blob: str) -> None:
    if manifest.get("schema_version") != "0.1":
        raise RepositoryIngestError("repository member source schema is invalid")
    if manifest.get("name") != PurePosixPath(relative).name:
        raise RepositoryIngestError("repository member source name mismatch")
    expected_role = _repository_source_role(relative)
    if manifest.get("source_role") != expected_role:
        raise RepositoryIngestError(
            f"CANON != SKILL: repository member source_role must be {expected_role}: {relative}"
        )
    expected_media = SUPPORTED_MEDIA_TYPES.get(PurePosixPath(relative).suffix.lower())
    if manifest.get("media_type") != expected_media:
        raise RepositoryIngestError("repository member media_type mismatch")
    if not _source_id_ok(manifest.get("source_id")):
        raise RepositoryIngestError("repository member source_id is invalid")
    if manifest.get("byte_sha256") != manifest["source_id"][7:]:
        raise RepositoryIngestError("repository member digest mismatch")
    origin = manifest.get("origin")
    if not isinstance(origin, Mapping) or origin != {
        "kind": "repository_member",
        "locator": relative,
        "repository_head": head,
        "git_blob": blob,
    }:
        raise RepositoryIngestError("repository member origin mismatch")


def validate_repository_manifest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("schema_version") != REPOSITORY_MANIFEST_SCHEMA_VERSION:
        raise RepositoryIngestError("repository manifest schema is invalid")
    fmt = manifest.get("object_format")
    head = manifest.get("repository_head")
    members = manifest.get("members")
    excluded = manifest.get("excluded")
    if fmt not in {"sha1", "sha256"} or not _oid_ok(head, fmt):
        raise RepositoryIngestError("repository identity is invalid")
    if not isinstance(members, list) or not members or not isinstance(excluded, list):
        raise RepositoryIngestError("repository manifest members/excluded are invalid")

    seen: set[str] = set()
    for member in members:
        relative = _safe_path(member.get("relative_path"))
        if relative in seen:
            raise RepositoryIngestError("repository path is duplicated")
        if member.get("git_mode") not in {"100644", "100755"} or not _oid_ok(member.get("git_blob"), fmt):
            raise RepositoryIngestError("repository member Git binding is invalid")
        source_manifest = member.get("source_manifest")
        if not isinstance(source_manifest, Mapping):
            raise RepositoryIngestError("repository member source manifest is invalid")
        _validate_source(relative, source_manifest, head=head, blob=member["git_blob"])
        seen.add(relative)

    for item in excluded:
        relative = _safe_path(item.get("relative_path"))
        if relative in seen:
            raise RepositoryIngestError("repository path is both included and excluded")
        if item.get("reason") not in {"symlink", "gitlink_or_non_blob", "unsupported_source_type"}:
            raise RepositoryIngestError("repository exclusion reason is invalid")
        seen.add(relative)

    if [m["relative_path"] for m in members] != sorted(m["relative_path"] for m in members):
        raise RepositoryIngestError("repository members are not sorted")
    if [e["relative_path"] for e in excluded] != sorted(e["relative_path"] for e in excluded):
        raise RepositoryIngestError("repository exclusions are not sorted")
    if manifest.get("repository_snapshot_id") != _snapshot_id(fmt, head, list(members), list(excluded)):
        raise RepositoryIngestError("repository_snapshot_id does not match manifest")


def ingest_repository(root: str | Path, *, revision: str = "HEAD", ingested_at: datetime | None = None) -> dict[str, Any]:
    repo = _root(root)
    fmt = _format(repo)
    head = _head(repo, revision, fmt)
    selected, excluded = _scan(repo, head)
    stamp = ingested_at or datetime.now(timezone.utc)
    members: list[dict[str, Any]] = []
    total = 0
    for entry in selected:
        data = _blob(repo, entry["git_blob"])
        total += len(data)
        if total > MAX_REPOSITORY_TOTAL_BYTES:
            raise RepositoryIngestError("repository exceeds aggregate byte limit")
        members.append({
            **entry,
            "source_manifest": _source_manifest(
                data,
                relative=entry["relative_path"],
                head=head,
                blob=entry["git_blob"],
                stamp=stamp,
            ),
        })
    manifest = {
        "schema_version": REPOSITORY_MANIFEST_SCHEMA_VERSION,
        "repository_snapshot_id": _snapshot_id(fmt, head, members, excluded),
        "object_format": fmt,
        "repository_head": head,
        "ingested_at": members[0]["source_manifest"]["ingested_at"],
        "members": members,
        "excluded": excluded,
    }
    validate_repository_manifest(manifest)
    return manifest


def normalize_repository(root: str | Path, manifest: Mapping[str, Any]) -> dict[str, Any]:
    validate_repository_manifest(manifest)
    repo = _root(root)
    fmt = _format(repo)
    if fmt != manifest["object_format"]:
        raise RepositoryIngestError("repository object format changed")
    _head(repo, manifest["repository_head"], fmt)
    selected, excluded = _scan(repo, manifest["repository_head"])
    actual = [(x["relative_path"], x["git_mode"], x["git_blob"]) for x in selected]
    expected = [(x["relative_path"], x["git_mode"], x["git_blob"]) for x in manifest["members"]]
    if actual != expected or excluded != manifest["excluded"]:
        raise RepositoryIngestError("repository manifest selection does not match pinned commit")

    normalized_members: list[dict[str, Any]] = []
    for member in manifest["members"]:
        data = _blob(repo, member["git_blob"])
        suffix = PurePosixPath(member["relative_path"]).suffix.lower()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"source{suffix}"
            path.write_bytes(data)
            try:
                normalized = normalize_source(path, member["source_manifest"])
            except NormalizationError as exc:
                raise RepositoryIngestError(
                    f"repository member normalization failed: {member['relative_path']}: {exc}"
                ) from exc
        normalized_members.append({**deepcopy(dict(member)), "normalized_source": normalized})

    return {
        "schema_version": NORMALIZED_REPOSITORY_SCHEMA_VERSION,
        "repository_snapshot_id": manifest["repository_snapshot_id"],
        "repository_head": manifest["repository_head"],
        "members": normalized_members,
        "excluded": deepcopy(manifest["excluded"]),
    }


def build_repository_compile_request(
    normalized_repository: Mapping[str, Any],
    *,
    knowledge_id: str,
    skill_slug: str,
    compiler_profile: Mapping[str, Any],
    purpose: str = "reference",
) -> dict[str, Any]:
    if normalized_repository.get("schema_version") != NORMALIZED_REPOSITORY_SCHEMA_VERSION:
        raise RepositoryIngestError("normalized repository schema is invalid")
    if not _source_id_ok(normalized_repository.get("repository_snapshot_id")):
        raise RepositoryIngestError("normalized repository_snapshot_id is invalid")
    sources = []
    for member in normalized_repository.get("members", []):
        normalized = member.get("normalized_source")
        source_manifest = member.get("source_manifest")
        if not isinstance(normalized, Mapping) or not isinstance(source_manifest, Mapping):
            raise RepositoryIngestError("normalized repository member is invalid")
        if normalized.get("source_id") != source_manifest.get("source_id"):
            raise RepositoryIngestError("normalized repository source identity mismatch")
        sources.append(normalized)
    if not sources:
        raise RepositoryIngestError("normalized repository has no sources")
    sources.sort(key=lambda source: source["source_id"])
    return build_compile_request_from_sources(
        sources,
        knowledge_id=knowledge_id,
        skill_slug=skill_slug,
        compiler_profile=compiler_profile,
        purpose=purpose,
    )
