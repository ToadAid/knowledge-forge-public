"""Deterministic fast routing index for governed Knowledge Forge skills.

The index is a disposable routing hint, never lifecycle, package, claim, or
authority truth. It is derived only from validated registry truth and contains
only the existing provider-neutral discovery descriptors: no skill bodies and
no claim values.

Fast routing intentionally does not reopen the full registry. Every selected
skill MUST pass the existing current-registry/trust/temporal load path before
its knowledge can enter reasoning context.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

from .consumer_discovery import (
    AUTHORITY,
    DISCOVERY_SURFACE,
    EXECUTION_AUTHORITY,
    GLOBAL_HARD_MAX_SKILLS_PER_QUERY,
    ConsumerDiscoveryError,
    build_discovery_descriptors,
    route_discovery_descriptors,
    validate_discovery_descriptors,
)
from .registry import registry_bytes, validate_skill_registry

SKILL_INDEX_SCHEMA_VERSION = "0.1"
SKILL_INDEX_KIND = "ROUTING_HINT_ONLY_NOT_LIFECYCLE_OR_KNOWLEDGE_TRUTH"
SELECTION_REQUIRES_CURRENT_REGISTRY_REVALIDATION = True

INDEX_FIELDS = {
    "schema_version",
    "index_kind",
    "surface",
    "registry_sha256",
    "skills_sha256",
    "hard_max_skills_per_query",
    "skill_count",
    "selection_requires_current_registry_revalidation",
    "authority",
    "execution_authority",
    "tool_grants",
    "skills",
}


class SkillIndexError(ValueError):
    """Raised when a fast skill index cannot remain deterministic and bounded."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillIndexError("skill index value is not canonical JSON data") from exc


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillIndexError("skill index is not JSON serializable") from exc


def _sha256_ref(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _is_sha256_ref(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return (
        len(digest) == 64
        and all(ch in "0123456789abcdef" for ch in digest)
    )


def build_skill_index(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Project exact current PROMOTED registry truth into a small routing index."""

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot build skill index from invalid registry: "
            + "; ".join(report["errors"])
        )
    try:
        skills = build_discovery_descriptors(registry)
    except ConsumerDiscoveryError as exc:
        raise SkillIndexError(f"cannot build skill index: {exc}") from exc

    index = {
        "schema_version": SKILL_INDEX_SCHEMA_VERSION,
        "index_kind": SKILL_INDEX_KIND,
        "surface": DISCOVERY_SURFACE,
        "registry_sha256": _sha256_ref(registry_bytes(registry)),
        "skills_sha256": _sha256_ref(_canonical_json_bytes(skills)),
        "hard_max_skills_per_query": GLOBAL_HARD_MAX_SKILLS_PER_QUERY,
        "skill_count": len(skills),
        "selection_requires_current_registry_revalidation": (
            SELECTION_REQUIRES_CURRENT_REGISTRY_REVALIDATION
        ),
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
        "skills": skills,
    }
    index_report = validate_skill_index(index)
    if not index_report["valid"]:
        raise SkillIndexError(
            "derived skill index is invalid: "
            + "; ".join(index_report["errors"])
        )
    return index


def validate_skill_index(index: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(index, Mapping):
        return {"valid": False, "errors": ["skill index must be a mapping"]}

    if set(index) != INDEX_FIELDS:
        errors.append("skill index fields must match exact contract")
    if index.get("schema_version") != SKILL_INDEX_SCHEMA_VERSION:
        errors.append("skill index schema_version mismatch")
    if index.get("index_kind") != SKILL_INDEX_KIND:
        errors.append("skill index kind mismatch")
    if index.get("surface") != DISCOVERY_SURFACE:
        errors.append("skill index surface mismatch")
    if not _is_sha256_ref(index.get("registry_sha256")):
        errors.append("skill index registry_sha256 must be a sha256 reference")
    if not _is_sha256_ref(index.get("skills_sha256")):
        errors.append("skill index skills_sha256 must be a sha256 reference")
    if (
        index.get("hard_max_skills_per_query")
        != GLOBAL_HARD_MAX_SKILLS_PER_QUERY
    ):
        errors.append("skill index hard max mismatch")
    if (
        index.get("selection_requires_current_registry_revalidation")
        is not True
    ):
        errors.append(
            "skill index must require current registry revalidation after selection"
        )
    if index.get("authority") != AUTHORITY:
        errors.append("skill index authority mismatch")
    if index.get("execution_authority") != EXECUTION_AUTHORITY:
        errors.append("skill index execution_authority mismatch")
    if index.get("tool_grants") != []:
        errors.append("skill index must not grant tools")

    skills = index.get("skills")
    descriptor_report = validate_discovery_descriptors(skills)
    errors.extend(descriptor_report["errors"])

    if isinstance(skills, list):
        if index.get("skill_count") != len(skills):
            errors.append("skill index skill_count mismatch")
        expected_skills_sha256 = _sha256_ref(_canonical_json_bytes(skills))
        if index.get("skills_sha256") != expected_skills_sha256:
            errors.append("skill index skills_sha256 mismatch")
    else:
        errors.append("skill index skills must be a list")

    return {"valid": not errors, "errors": errors}


def skill_index_bytes(index: Mapping[str, Any]) -> bytes:
    report = validate_skill_index(index)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot serialize invalid skill index: "
            + "; ".join(report["errors"])
        )
    return _pretty_json_bytes(index)


def skill_index_sha256(index: Mapping[str, Any]) -> str:
    report = validate_skill_index(index)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot hash invalid skill index: "
            + "; ".join(report["errors"])
        )
    return _sha256_ref(_canonical_json_bytes(dict(index)))


def list_skill_index(index: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return a lightweight human/agent librarian listing, still zero-authority."""

    report = validate_skill_index(index)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot list invalid skill index: "
            + "; ".join(report["errors"])
        )

    result: list[dict[str, Any]] = []
    for item in index["skills"]:
        unit_routes = item.get("unit_routes")
        result.append({
            "skill_id": item.get("skill_id"),
            "skill_version": item.get("skill_version"),
            "knowledge_id": item.get("knowledge_id"),
            "title": item.get("title"),
            "purpose": item.get("purpose"),
            "package_ref": item.get("package_ref"),
            "lifecycle_head_sha256": item.get("lifecycle_head_sha256"),
            "unit_count": len(unit_routes) if isinstance(unit_routes, list) else 0,
        })
    return result


def route_skill_index(
    index: Mapping[str, Any],
    question: str,
    *,
    consumer_id: str,
    max_skills: int = 1,
) -> dict[str, Any]:
    """Fast candidate selection from index metadata only.

    No registry is accepted by this function. The returned candidate identities
    are not load authority and MUST be passed through current registry/trust/
    temporal validation before any unit is loaded.
    """

    report = validate_skill_index(index)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot route invalid skill index: "
            + "; ".join(report["errors"])
        )
    try:
        selected = route_discovery_descriptors(
            index["skills"],
            question,
            consumer_id=consumer_id,
            max_skills=max_skills,
            hard_max_skills_per_query=index["hard_max_skills_per_query"],
        )
    except ConsumerDiscoveryError as exc:
        raise SkillIndexError(f"skill index routing refused: {exc}") from exc

    return {
        "registry_sha256": index["registry_sha256"],
        "skill_index_sha256": skill_index_sha256(index),
        "selection_requires_current_registry_revalidation": True,
        "selected_skill_ids": [item["skill_id"] for item in selected],
        "selected_skills": selected,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }


def verify_skill_index_current(
    index: Mapping[str, Any],
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Maintenance check: compare index to a fresh exact registry projection."""

    report = validate_skill_index(index)
    if not report["valid"]:
        raise SkillIndexError(
            "cannot verify invalid skill index: "
            + "; ".join(report["errors"])
        )

    expected = build_skill_index(registry)
    current = dict(index) == expected
    return {
        "current": current,
        "requires_rebuild": not current,
        "index_registry_sha256": index["registry_sha256"],
        "registry_sha256": expected["registry_sha256"],
        "index_skill_index_sha256": skill_index_sha256(index),
        "expected_skill_index_sha256": skill_index_sha256(expected),
    }


def _absolute_without_resolution(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def _reject_symlink_components(path: Path, label: str) -> None:
    absolute = _absolute_without_resolution(path)
    anchor = Path(absolute.anchor)
    current = anchor
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        current = current / part
        if not os.path.lexists(current):
            continue
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SkillIndexError(f"{label} path is unreadable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SkillIndexError(
                f"{label} may not traverse a symlink: {current}"
            )


def load_skill_index(path_value: str | Path) -> dict[str, Any]:
    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, "skill index")
    if not path.is_file():
        raise SkillIndexError("skill index must be an existing regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillIndexError(f"cannot read skill index: {path}") from exc
    if not isinstance(value, dict):
        raise SkillIndexError("skill index file must contain a JSON object")
    report = validate_skill_index(value)
    if not report["valid"]:
        raise SkillIndexError(
            "skill index validation failed: "
            + "; ".join(report["errors"])
        )
    return value


def save_skill_index(
    index: Mapping[str, Any],
    path_value: str | Path,
) -> Path:
    """Atomically persist a replaceable derived index for host integration."""

    payload = skill_index_bytes(index)
    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, "skill index")

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise SkillIndexError("skill index parent is unavailable") from exc
    _reject_symlink_components(path.parent, "skill index parent")

    if path.exists() and not path.is_file():
        raise SkillIndexError("skill index target must be a regular file")

    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise

    return path
