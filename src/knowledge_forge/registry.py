"""Durable governed skill registry with current-claim conflict truth.

P2B registers lifecycle-PROMOTED skill packages. P2C provides explicit
version handoff. P2D-B2B stores the verified structured claim index for every
registered version and deterministically derives ConflictSets from only the
current promoted versions. Conflict discovery never blocks promotion and
never chooses a winner.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .claim_index import (
    ClaimIndexError,
    claim_index_artifact_sha256,
    validate_claim_index,
)
from .conflicts import (
    ConflictSetError,
    record_conflict_resolution,
    scan_conflict_sets,
    validate_conflict_set,
)
from .lifecycle import (
    DISPUTED,
    PROMOTED,
    REVOKED,
    SUPERSEDED,
    SUSPENDED,
    VALIDATED,
    validate_lifecycle_record,
)

REGISTRY_SCHEMA_VERSION = "0.3"
CURRENT_STATES = frozenset({PROMOTED, SUSPENDED, DISPUTED, REVOKED})
HISTORICAL_STATES = frozenset({SUPERSEDED, REVOKED})
INCIDENT_FOLD_STATES = frozenset({PROMOTED, SUSPENDED, DISPUTED, REVOKED})
AUTHORITY = "KNOWLEDGE_ONLY"


class SkillRegistryError(ValueError):
    """Raised when registry truth is invalid or a registration is unsafe."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SkillRegistryError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise SkillRegistryError(f"{name} must be a positive integer")
    return value


def _sha256_ref(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if not text.startswith("sha256:"):
        raise SkillRegistryError(f"{name} must be a sha256 reference")
    digest = text.removeprefix("sha256:")
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise SkillRegistryError(f"{name} must be a sha256 reference")
    return text


def _source_receipt_sha256(source_receipt: Mapping[str, Any]) -> str:
    if not isinstance(source_receipt, Mapping):
        raise SkillRegistryError("source_receipt must be a mapping")
    try:
        payload = (
            json.dumps(
                dict(source_receipt),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SkillRegistryError("source_receipt is not canonical JSON data") from exc
    return f"sha256:{sha256(payload).hexdigest()}"


def _validated_claim_digests(lifecycle: Mapping[str, Any]) -> dict[str, str]:
    events = lifecycle.get("events")
    if not isinstance(events, list):
        raise SkillRegistryError("lifecycle requires event history")
    validated = [
        event
        for event in events
        if isinstance(event, Mapping) and event.get("to_state") == VALIDATED
    ]
    if len(validated) != 1:
        raise SkillRegistryError("lifecycle requires exactly one VALIDATED event")
    evidence = validated[0].get("evidence")
    if not isinstance(evidence, Mapping):
        raise SkillRegistryError("VALIDATED event requires evidence")
    report = evidence.get("validation_report")
    if not isinstance(report, Mapping):
        raise SkillRegistryError("VALIDATED event requires validation_report")
    claims = report.get("claim_sha256_by_unit")
    if not isinstance(claims, Mapping) or not claims:
        raise SkillRegistryError(
            "VALIDATED validation_report requires claim_sha256_by_unit"
        )
    result: dict[str, str] = {}
    for unit_id, digest in claims.items():
        unit_key = _nonempty(unit_id, "validated unit_id")
        result[unit_key] = _sha256_ref(digest, "validated claim digest")
    return result


def _claim_digest_map(index: Mapping[str, Any]) -> dict[str, str]:
    claims = index.get("claims")
    if not isinstance(claims, list) or not claims:
        raise SkillRegistryError("claim index requires claims")
    result: dict[str, str] = {}
    for item in claims:
        if not isinstance(item, Mapping):
            raise SkillRegistryError("claim index entry must be a mapping")
        unit_id = _nonempty(item.get("unit_id"), "claim index unit_id")
        digest = _sha256_ref(item.get("claim_sha256"), "claim_sha256")
        if unit_id in result:
            raise SkillRegistryError("claim index unit_ids must be unique")
        result[unit_id] = digest
    return result


def _bind_claim_index(
    manifest: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    claim_index: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
) -> str:
    try:
        validate_claim_index(claim_index, manifest)
    except ClaimIndexError as exc:
        raise SkillRegistryError(f"claim index invalid: {exc}") from exc

    receipt_digest = _source_receipt_sha256(source_receipt)
    if lifecycle.get("package_receipt_sha256") != receipt_digest:
        raise SkillRegistryError(
            "source receipt does not match promoted lifecycle package receipt"
        )
    for field in (
        "skill_id",
        "skill_version",
        "knowledge_id",
        "candidate_sha256",
        "source_ids",
    ):
        if source_receipt.get(field) != lifecycle.get(field):
            raise SkillRegistryError(
                f"source receipt {field} does not match promoted lifecycle"
            )
    artifact_hashes = source_receipt.get("artifact_hashes")
    if not isinstance(artifact_hashes, Mapping) or not artifact_hashes:
        raise SkillRegistryError("source receipt requires artifact_hashes")

    meta = manifest.get("claim_index")
    if not isinstance(meta, Mapping):
        raise SkillRegistryError("manifest requires claim_index binding")
    if set(meta) != {"path", "schema_version", "sha256", "claim_count"}:
        raise SkillRegistryError("manifest claim_index fields must match exact contract")
    if meta.get("path") != "claim-index.json" or meta.get("schema_version") != "0.1":
        raise SkillRegistryError("manifest claim_index contract mismatch")

    digest = claim_index_artifact_sha256(claim_index)
    if artifact_hashes.get("claim-index.json") != digest:
        raise SkillRegistryError(
            "source receipt claim-index artifact hash mismatch"
        )
    if meta.get("sha256") != digest:
        raise SkillRegistryError("manifest claim_index sha256 mismatch")
    claims = claim_index.get("claims")
    if not isinstance(claims, list) or meta.get("claim_count") != len(claims):
        raise SkillRegistryError("manifest claim_index claim_count mismatch")

    if claim_index.get("skill_id") != lifecycle.get("skill_id"):
        raise SkillRegistryError("claim index skill_id does not match lifecycle")
    if claim_index.get("skill_version") != lifecycle.get("skill_version"):
        raise SkillRegistryError("claim index skill_version does not match lifecycle")
    if claim_index.get("knowledge_id") != lifecycle.get("knowledge_id"):
        raise SkillRegistryError("claim index knowledge_id does not match lifecycle")
    if claim_index.get("candidate_sha256") != lifecycle.get("candidate_sha256"):
        raise SkillRegistryError("claim index candidate_sha256 does not match lifecycle")
    if claim_index.get("source_ids") != lifecycle.get("source_ids"):
        raise SkillRegistryError("claim index source_ids do not match lifecycle")

    index_claims = _claim_digest_map(claim_index)
    lifecycle_claims = _validated_claim_digests(lifecycle)
    if index_claims != lifecycle_claims:
        raise SkillRegistryError(
            "claim index semantics do not match validated lifecycle claim digests"
        )
    return digest


def _candidate_from_claim_index(index: Mapping[str, Any]) -> dict[str, Any]:
    """Project durable structured claim truth into the B1 scan contract."""

    return {
        "knowledge_id": index["knowledge_id"],
        "compiler": {
            "compiler_profile_id": index["compiler_profile_id"],
        },
        "units": [
            {
                "unit_id": item["unit_id"],
                "kind": item["kind"],
                "claim": deepcopy(item["claim"]),
                "source_bindings": deepcopy(item["source_bindings"]),
            }
            for item in index["claims"]
        ],
    }


def _scan_indexes(indexes: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(indexes) < 2:
        return []
    candidates: list[dict[str, Any]] = []
    for index in indexes:
        try:
            validate_claim_index(index)
        except ClaimIndexError as exc:
            raise SkillRegistryError(f"claim index invalid during scan: {exc}") from exc
        candidates.append(_candidate_from_claim_index(index))
    try:
        return scan_conflict_sets(candidates)
    except ConflictSetError as exc:
        raise SkillRegistryError(f"cross-skill conflict scan failed: {exc}") from exc


def _current_claim_indexes(registry: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    skills = registry.get("skills")
    if not isinstance(skills, Mapping):
        return []
    indexes: list[Mapping[str, Any]] = []
    for skill_id in sorted(skills):
        record = skills[skill_id]
        if not isinstance(record, Mapping):
            continue
        current_version = record.get("current_version")
        versions = record.get("versions")
        if not isinstance(versions, Mapping):
            continue
        entry = versions.get(str(current_version))
        if not isinstance(entry, Mapping):
            continue
        if entry.get("state") != PROMOTED:
            continue
        index = entry.get("claim_index")
        if isinstance(index, Mapping):
            indexes.append(index)
    return indexes


def _conflict_structure(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "conflict_set_id": value.get("conflict_set_id"),
        "subject": value.get("subject"),
        "predicate": value.get("predicate"),
        "members": deepcopy(value.get("members")),
        "relations": deepcopy(value.get("relations")),
    }


def _expected_open_conflicts(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    return _scan_indexes(_current_claim_indexes(registry))


def _refresh_conflict_sets(registry: dict[str, Any]) -> None:
    previous = registry.get("conflict_sets")
    previous_by_id = {
        item.get("conflict_set_id"): item
        for item in previous
        if isinstance(previous, list)
        and isinstance(item, Mapping)
        and isinstance(item.get("conflict_set_id"), str)
    } if isinstance(previous, list) else {}

    refreshed: list[dict[str, Any]] = []
    for open_set in _expected_open_conflicts(registry):
        prior = previous_by_id.get(open_set["conflict_set_id"])
        if isinstance(prior, Mapping):
            try:
                validate_conflict_set(prior)
            except ConflictSetError:
                prior = None
        if (
            isinstance(prior, Mapping)
            and _conflict_structure(prior) == _conflict_structure(open_set)
            and prior.get("state") == "RESOLVED"
        ):
            refreshed.append(deepcopy(dict(prior)))
        else:
            refreshed.append(open_set)
    refreshed.sort(key=lambda item: item["conflict_set_id"])
    registry["conflict_sets"] = refreshed


def create_skill_registry(*, created_at: str) -> dict[str, Any]:
    timestamp = _nonempty(created_at, "created_at")
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "created_at": timestamp,
        "updated_at": timestamp,
        "skills": {},
        "conflict_sets": [],
    }


def _entry_from_promoted(
    manifest: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    claim_index: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    *,
    registered_at: str,
) -> dict[str, Any]:
    report = validate_lifecycle_record(lifecycle)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot register invalid lifecycle record: " + "; ".join(report["errors"])
        )
    if lifecycle.get("state") != PROMOTED:
        raise SkillRegistryError("registry accepts PROMOTED lifecycle state only")

    skill_id = _nonempty(manifest.get("skill_id"), "manifest skill_id")
    skill_version = _positive_int(manifest.get("skill_version"), "manifest skill_version")
    if manifest.get("authority") != AUTHORITY:
        raise SkillRegistryError("registry accepts KNOWLEDGE_ONLY packages only")
    if lifecycle.get("skill_id") != skill_id:
        raise SkillRegistryError("manifest skill_id does not match lifecycle")
    if lifecycle.get("skill_version") != skill_version:
        raise SkillRegistryError("manifest skill_version does not match lifecycle")

    knowledge_id = _nonempty(manifest.get("knowledge_id"), "manifest knowledge_id")
    candidate_digest = _sha256_ref(
        manifest.get("candidate_sha256"), "manifest candidate_sha256"
    )
    if lifecycle.get("knowledge_id") != knowledge_id:
        raise SkillRegistryError("manifest knowledge_id does not match lifecycle")
    if lifecycle.get("candidate_sha256") != candidate_digest:
        raise SkillRegistryError("manifest candidate_sha256 does not match lifecycle")

    manifest_units = manifest.get("units")
    if not isinstance(manifest_units, list) or not manifest_units:
        raise SkillRegistryError("manifest requires units")
    manifest_unit_ids = [
        _nonempty(item.get("unit_id"), "manifest unit_id")
        for item in manifest_units
        if isinstance(item, Mapping)
    ]
    if len(manifest_unit_ids) != len(manifest_units):
        raise SkillRegistryError("manifest unit must be a mapping")
    if lifecycle.get("unit_ids") != manifest_unit_ids:
        raise SkillRegistryError("manifest unit_ids do not match lifecycle")

    package_receipt_sha256 = _sha256_ref(
        lifecycle.get("package_receipt_sha256"), "package_receipt_sha256"
    )
    events = lifecycle.get("events")
    if not isinstance(events, list) or not events:
        raise SkillRegistryError("promoted lifecycle requires event history")
    lifecycle_head_sha256 = _sha256_ref(
        events[-1].get("event_sha256"), "lifecycle head event_sha256"
    )

    source_ids = manifest.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        raise SkillRegistryError("manifest requires non-empty source_ids")
    normalized_source_ids = [
        _sha256_ref(source_id, "source_id") for source_id in source_ids
    ]
    if lifecycle.get("source_ids") != normalized_source_ids:
        raise SkillRegistryError("manifest source_ids do not match lifecycle")

    claim_index_sha256 = _bind_claim_index(
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
    )

    return {
        "skill_id": skill_id,
        "skill_version": skill_version,
        "state": PROMOTED,
        "authority": AUTHORITY,
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "compiler_profile_id": _sha256_ref(
            claim_index.get("compiler_profile_id"),
            "claim index compiler_profile_id",
        ),
        "title": _nonempty(manifest.get("title"), "manifest title"),
        "purpose": _nonempty(manifest.get("purpose"), "manifest purpose"),
        "source_ids": list(dict.fromkeys(normalized_source_ids)),
        "claim_index_sha256": claim_index_sha256,
        "claim_index": deepcopy(dict(claim_index)),
        "source_receipt": deepcopy(dict(source_receipt)),
        "package_receipt_sha256": package_receipt_sha256,
        "package_ref": f"receipt:{package_receipt_sha256}",
        "lifecycle_head_sha256": lifecycle_head_sha256,
        "registered_at": _nonempty(registered_at, "registered_at"),
        "lifecycle_record": deepcopy(dict(lifecycle)),
    }


def _validate_registry_entry(
    *,
    skill_id: str,
    version_key: str,
    entry: Mapping[str, Any],
    current_version: int,
) -> list[str]:
    errors: list[str] = []
    label = f"{skill_id}@{version_key}"

    expected_fields = {
        "skill_id",
        "skill_version",
        "state",
        "authority",
        "knowledge_id",
        "candidate_sha256",
        "compiler_profile_id",
        "title",
        "purpose",
        "source_ids",
        "claim_index_sha256",
        "claim_index",
        "source_receipt",
        "package_receipt_sha256",
        "package_ref",
        "lifecycle_head_sha256",
        "registered_at",
        "lifecycle_record",
    }
    if set(entry) != expected_fields:
        errors.append(f"registry entry fields mismatch: {label}")

    if entry.get("skill_id") != skill_id:
        errors.append(f"registry skill_id mismatch: {label}")
    if str(entry.get("skill_version")) != version_key:
        errors.append(f"registry version key mismatch: {label}")

    is_current = version_key == str(current_version)
    entry_state = entry.get("state")
    allowed_states = CURRENT_STATES if is_current else HISTORICAL_STATES
    if entry_state not in allowed_states:
        allowed_text = ", ".join(sorted(allowed_states))
        errors.append(
            f"registry {'current' if is_current else 'historical'} entry "
            f"state must be one of {allowed_text}: {label}"
        )
    if entry.get("authority") != AUTHORITY:
        errors.append(f"registry contains non-knowledge authority: {label}")

    lifecycle = entry.get("lifecycle_record")
    if not isinstance(lifecycle, Mapping):
        errors.append(f"registry entry missing lifecycle_record: {label}")
        return errors

    lifecycle_report = validate_lifecycle_record(lifecycle)
    if not lifecycle_report["valid"]:
        errors.append(f"registry lifecycle invalid: {label}")
        return errors
    if lifecycle.get("state") != entry.get("state"):
        errors.append(f"registry lifecycle state mismatch: {label}")
    if lifecycle.get("skill_id") != skill_id:
        errors.append(f"registry lifecycle skill_id mismatch: {label}")
    if lifecycle.get("skill_version") != entry.get("skill_version"):
        errors.append(f"registry lifecycle version mismatch: {label}")
    if lifecycle.get("knowledge_id") != entry.get("knowledge_id"):
        errors.append(f"registry lifecycle knowledge_id mismatch: {label}")
    if lifecycle.get("candidate_sha256") != entry.get("candidate_sha256"):
        errors.append(f"registry lifecycle candidate mismatch: {label}")
    if lifecycle.get("source_ids") != entry.get("source_ids"):
        errors.append(f"registry lifecycle source_ids mismatch: {label}")
    if lifecycle.get("package_receipt_sha256") != entry.get(
        "package_receipt_sha256"
    ):
        errors.append(f"registry lifecycle package mismatch: {label}")

    events = lifecycle.get("events")
    if (
        not isinstance(events, list)
        or not events
        or events[-1].get("event_sha256")
        != entry.get("lifecycle_head_sha256")
    ):
        errors.append(f"registry lifecycle head mismatch: {label}")

    try:
        package_digest = _sha256_ref(
            entry.get("package_receipt_sha256"), "package_receipt_sha256"
        )
        _sha256_ref(entry.get("lifecycle_head_sha256"), "lifecycle_head_sha256")
        _sha256_ref(entry.get("candidate_sha256"), "candidate_sha256")
        _sha256_ref(entry.get("compiler_profile_id"), "compiler_profile_id")
        _sha256_ref(entry.get("claim_index_sha256"), "claim_index_sha256")
    except SkillRegistryError:
        errors.append(f"registry entry contains invalid digest: {label}")
        package_digest = entry.get("package_receipt_sha256")

    expected_ref = f"receipt:{package_digest}"
    if entry.get("package_ref") != expected_ref:
        errors.append(f"registry package_ref mismatch: {label}")

    source_receipt = entry.get("source_receipt")
    if not isinstance(source_receipt, Mapping):
        errors.append(f"registry entry missing source_receipt: {label}")
    else:
        try:
            receipt_digest = _source_receipt_sha256(source_receipt)
            if receipt_digest != lifecycle.get("package_receipt_sha256"):
                errors.append(f"registry source_receipt package mismatch: {label}")
            for field in (
                "skill_id",
                "skill_version",
                "knowledge_id",
                "candidate_sha256",
                "source_ids",
            ):
                if source_receipt.get(field) != lifecycle.get(field):
                    errors.append(
                        f"registry source_receipt {field} mismatch: {label}"
                    )
        except SkillRegistryError:
            errors.append(f"registry source_receipt invalid: {label}")

    index = entry.get("claim_index")
    if not isinstance(index, Mapping):
        errors.append(f"registry entry missing claim_index: {label}")
        return errors
    try:
        validate_claim_index(index)
    except ClaimIndexError:
        errors.append(f"registry claim_index invalid: {label}")
        return errors

    if index.get("skill_id") != skill_id:
        errors.append(f"registry claim_index skill_id mismatch: {label}")
    if index.get("skill_version") != entry.get("skill_version"):
        errors.append(f"registry claim_index version mismatch: {label}")
    if index.get("knowledge_id") != entry.get("knowledge_id"):
        errors.append(f"registry claim_index knowledge_id mismatch: {label}")
    if index.get("candidate_sha256") != entry.get("candidate_sha256"):
        errors.append(f"registry claim_index candidate mismatch: {label}")
    if index.get("compiler_profile_id") != entry.get("compiler_profile_id"):
        errors.append(f"registry claim_index compiler profile mismatch: {label}")
    if index.get("source_ids") != entry.get("source_ids"):
        errors.append(f"registry claim_index source_ids mismatch: {label}")
    try:
        index_digest = claim_index_artifact_sha256(index)
        if index_digest != entry.get("claim_index_sha256"):
            errors.append(f"registry claim_index sha256 mismatch: {label}")
        if isinstance(source_receipt, Mapping):
            artifact_hashes = source_receipt.get("artifact_hashes")
            if (
                not isinstance(artifact_hashes, Mapping)
                or artifact_hashes.get("claim-index.json") != index_digest
            ):
                errors.append(
                    f"registry source_receipt claim-index artifact mismatch: {label}"
                )
    except ClaimIndexError:
        errors.append(f"registry claim_index digest invalid: {label}")

    try:
        if _claim_digest_map(index) != _validated_claim_digests(lifecycle):
            errors.append(
                f"registry claim_index semantics do not match validated lifecycle: {label}"
            )
    except SkillRegistryError:
        errors.append(
            f"registry claim_index semantic binding invalid: {label}"
        )

    return errors


def validate_skill_registry(registry: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(registry, Mapping):
        return {"valid": False, "errors": ["registry must be a mapping"]}

    expected_top = {
        "schema_version",
        "created_at",
        "updated_at",
        "skills",
        "conflict_sets",
    }
    if set(registry) != expected_top:
        errors.append("registry fields must match exact contract")
    if registry.get("schema_version") != REGISTRY_SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    for field in ("created_at", "updated_at"):
        if not isinstance(registry.get(field), str) or not registry[field].strip():
            errors.append(f"{field} must be a non-empty string")

    skills = registry.get("skills")
    if not isinstance(skills, Mapping):
        errors.append("registry skills must be a mapping")
        return {"valid": False, "errors": errors}

    for skill_id, record in skills.items():
        if not isinstance(skill_id, str) or not skill_id:
            errors.append("registry contains invalid skill_id")
            continue
        if not isinstance(record, Mapping):
            errors.append(f"registry record must be a mapping: {skill_id}")
            continue
        if set(record) != {"current_version", "versions"}:
            errors.append(f"registry skill record fields mismatch: {skill_id}")

        current_version = record.get("current_version")
        versions = record.get("versions")
        if (
            not isinstance(current_version, int)
            or isinstance(current_version, bool)
            or current_version < 1
        ):
            errors.append(f"invalid current_version: {skill_id}")
        if not isinstance(versions, Mapping) or not versions:
            errors.append(f"registry versions must be a non-empty mapping: {skill_id}")
            continue
        if str(current_version) not in versions:
            errors.append(f"current_version missing from versions: {skill_id}")

        for version_key, entry in versions.items():
            if not isinstance(entry, Mapping):
                errors.append(
                    f"registry version entry must be a mapping: {skill_id}@{version_key}"
                )
                continue
            errors.extend(
                _validate_registry_entry(
                    skill_id=skill_id,
                    version_key=version_key,
                    entry=entry,
                    current_version=current_version,
                )
            )

    stored_conflicts = registry.get("conflict_sets")
    if not isinstance(stored_conflicts, list):
        errors.append("registry conflict_sets must be a list")
        return {"valid": False, "errors": errors}

    stored_ids: list[str] = []
    valid_stored: dict[str, Mapping[str, Any]] = {}
    for item in stored_conflicts:
        if not isinstance(item, Mapping):
            errors.append("registry conflict_set must be a mapping")
            continue
        conflict_id = item.get("conflict_set_id")
        if not isinstance(conflict_id, str) or not conflict_id:
            errors.append("registry conflict_set requires conflict_set_id")
            continue
        stored_ids.append(conflict_id)
        try:
            validate_conflict_set(item)
        except ConflictSetError:
            errors.append(f"registry conflict_set invalid: {conflict_id}")
            continue
        valid_stored[conflict_id] = item

    if len(set(stored_ids)) != len(stored_ids):
        errors.append("registry conflict_set ids must be unique")
    if stored_ids != sorted(stored_ids):
        errors.append("registry conflict_sets must be sorted by conflict_set_id")

    if not any(error.startswith("registry claim_index") for error in errors):
        try:
            expected_conflicts = _expected_open_conflicts(registry)
        except SkillRegistryError as exc:
            errors.append(f"registry conflict scan failed: {exc}")
            expected_conflicts = []

        expected_by_id = {
            item["conflict_set_id"]: item for item in expected_conflicts
        }
        if set(valid_stored) != set(expected_by_id):
            errors.append("registry conflict_sets do not match current claim truth")
        else:
            for conflict_id, expected in expected_by_id.items():
                stored = valid_stored[conflict_id]
                if _conflict_structure(stored) != _conflict_structure(expected):
                    errors.append(
                        f"registry conflict_set structure mismatch: {conflict_id}"
                    )

    return {"valid": not errors, "errors": errors}


def register_promoted_skill(
    registry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    claim_index: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    *,
    registered_at: str,
) -> dict[str, Any]:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot update invalid registry: " + "; ".join(report["errors"])
        )

    entry = _entry_from_promoted(
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
        registered_at=registered_at,
    )
    updated = deepcopy(dict(registry))
    skill_id = entry["skill_id"]
    version_key = str(entry["skill_version"])

    existing = updated["skills"].get(skill_id)
    if existing is not None:
        if version_key in existing["versions"]:
            raise SkillRegistryError(
                f"skill version already registered: {skill_id}@{version_key}"
            )
        raise SkillRegistryError(
            "skill already has a current version; P2C versioned update is required"
        )

    updated["skills"][skill_id] = {
        "current_version": entry["skill_version"],
        "versions": {version_key: entry},
    }
    updated["updated_at"] = entry["registered_at"]
    _refresh_conflict_sets(updated)

    final_report = validate_skill_registry(updated)
    if not final_report["valid"]:
        raise SkillRegistryError(
            "registration produced invalid registry: "
            + "; ".join(final_report["errors"])
        )
    return updated


def fold_in_current_incident_lifecycle(
    registry: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    *,
    updated_at: str,
) -> dict[str, Any]:
    # Fold one exact current lifecycle incident/restore event into registry truth.

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot update invalid registry: " + "; ".join(report["errors"])
        )

    timestamp = _nonempty(updated_at, "updated_at")
    lifecycle_report = validate_lifecycle_record(lifecycle)
    if not lifecycle_report["valid"]:
        raise SkillRegistryError(
            "incident lifecycle is invalid: " + "; ".join(lifecycle_report["errors"])
        )

    skill_id = _nonempty(lifecycle.get("skill_id"), "incident lifecycle skill_id")
    skill_record = registry["skills"].get(skill_id)
    if skill_record is None:
        raise SkillRegistryError(f"skill is not registered: {skill_id}")

    current_version = skill_record["current_version"]
    if lifecycle.get("skill_version") != current_version:
        raise SkillRegistryError(
            "incident lifecycle must target the exact current_version"
        )

    current_entry = skill_record["versions"][str(current_version)]
    if lifecycle.get("package_receipt_sha256") != current_entry.get(
        "package_receipt_sha256"
    ):
        raise SkillRegistryError(
            "incident lifecycle package does not match current registry entry"
        )

    registered_lifecycle = current_entry.get("lifecycle_record")
    registered_events = (
        registered_lifecycle.get("events")
        if isinstance(registered_lifecycle, Mapping)
        else None
    )
    incident_events = lifecycle.get("events")
    if (
        not isinstance(registered_events, list)
        or not isinstance(incident_events, list)
        or len(incident_events) != len(registered_events) + 1
        or incident_events[:-1] != registered_events
    ):
        raise SkillRegistryError(
            "incident lifecycle must extend the exact current registry lifecycle by one event"
        )

    source_state = current_entry.get("state")
    target_state = lifecycle.get("state")
    if source_state not in CURRENT_STATES:
        raise SkillRegistryError("current registry entry is not incident-foldable")
    if target_state not in INCIDENT_FOLD_STATES:
        raise SkillRegistryError(
            "incident fold target must be PROMOTED, SUSPENDED, DISPUTED, or REVOKED"
        )
    if target_state == source_state:
        raise SkillRegistryError("incident fold must change current lifecycle state")

    final_event = incident_events[-1]
    if final_event.get("from_state") != source_state:
        raise SkillRegistryError(
            "incident lifecycle does not extend the registry entry state"
        )
    if final_event.get("to_state") != target_state:
        raise SkillRegistryError(
            "incident lifecycle final event does not match target state"
        )

    updated = deepcopy(dict(registry))
    entry = updated["skills"][skill_id]["versions"][str(current_version)]
    entry["state"] = target_state
    entry["lifecycle_record"] = deepcopy(dict(lifecycle))
    entry["lifecycle_head_sha256"] = _sha256_ref(
        final_event.get("event_sha256"),
        "incident lifecycle head event_sha256",
    )
    updated["updated_at"] = timestamp
    _refresh_conflict_sets(updated)

    final_report = validate_skill_registry(updated)
    if not final_report["valid"]:
        raise SkillRegistryError(
            "incident fold produced invalid registry: "
            + "; ".join(final_report["errors"])
        )
    return updated


def fold_in_promoted_version(
    registry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    promoted_lifecycle: Mapping[str, Any],
    claim_index: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    superseded_current_lifecycle: Mapping[str, Any] | None,
    *,
    updated_at: str,
) -> dict[str, Any]:
    """Atomically hand current truth from one promoted version to a newer one."""

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot update invalid registry: " + "; ".join(report["errors"])
        )

    timestamp = _nonempty(updated_at, "updated_at")
    replacement = _entry_from_promoted(
        manifest,
        promoted_lifecycle,
        claim_index,
        source_receipt,
        registered_at=timestamp,
    )
    skill_id = replacement["skill_id"]

    current_record = registry["skills"].get(skill_id)
    if current_record is None:
        raise SkillRegistryError(
            f"skill is not registered; use initial registration first: {skill_id}"
        )

    current_version = current_record["current_version"]
    replacement_version = replacement["skill_version"]
    if replacement_version <= current_version:
        raise SkillRegistryError("replacement version must be newer than current_version")
    if str(replacement_version) in current_record["versions"]:
        raise SkillRegistryError(
            f"skill version already registered: {skill_id}@{replacement_version}"
        )

    current_entry = current_record["versions"][str(current_version)]
    current_state = current_entry.get("state")

    if current_state == REVOKED:
        if superseded_current_lifecycle is not None:
            raise SkillRegistryError(
                "revoked current version is terminal; replacement handoff requires no supersession event"
            )
        historical = deepcopy(dict(current_entry))
    else:
        if not isinstance(superseded_current_lifecycle, Mapping):
            raise SkillRegistryError(
                "non-revoked current version requires a SUPERSEDED lifecycle extension"
            )
        supersession_report = validate_lifecycle_record(superseded_current_lifecycle)
        if not supersession_report["valid"]:
            raise SkillRegistryError(
                "superseded current lifecycle is invalid: "
                + "; ".join(supersession_report["errors"])
            )
        if superseded_current_lifecycle.get("state") != SUPERSEDED:
            raise SkillRegistryError("current lifecycle must reach SUPERSEDED")
        if superseded_current_lifecycle.get("skill_id") != skill_id:
            raise SkillRegistryError(
                "superseded lifecycle skill_id does not match replacement"
            )
        if superseded_current_lifecycle.get("skill_version") != current_version:
            raise SkillRegistryError(
                "superseded lifecycle does not target current_version"
            )
        if superseded_current_lifecycle.get(
            "package_receipt_sha256"
        ) != current_entry.get("package_receipt_sha256"):
            raise SkillRegistryError(
                "superseded lifecycle package does not match current entry"
            )

        registered_events = current_entry["lifecycle_record"].get("events")
        superseded_events = superseded_current_lifecycle.get("events")
        if (
            not isinstance(registered_events, list)
            or not isinstance(superseded_events, list)
            or len(superseded_events) != len(registered_events) + 1
            or superseded_events[:-1] != registered_events
        ):
            raise SkillRegistryError(
                "superseded lifecycle must extend the exact current registry lifecycle"
            )

        final_event = superseded_events[-1]
        evidence = final_event.get("evidence")
        if not isinstance(evidence, Mapping):
            raise SkillRegistryError("SUPERSEDED event requires replacement evidence")
        if evidence.get("replacement_skill_id") != skill_id:
            raise SkillRegistryError("SUPERSEDED replacement_skill_id mismatch")
        if evidence.get("replacement_skill_version") != replacement_version:
            raise SkillRegistryError("SUPERSEDED replacement_skill_version mismatch")

        historical = deepcopy(dict(current_entry))
        historical["state"] = SUPERSEDED
        historical["lifecycle_record"] = deepcopy(
            dict(superseded_current_lifecycle)
        )
        historical["lifecycle_head_sha256"] = _sha256_ref(
            final_event.get("event_sha256"),
            "superseded lifecycle head event_sha256",
        )

    updated = deepcopy(dict(registry))
    versions = updated["skills"][skill_id]["versions"]
    versions[str(current_version)] = historical
    versions[str(replacement_version)] = replacement
    updated["skills"][skill_id]["current_version"] = replacement_version
    updated["updated_at"] = timestamp
    _refresh_conflict_sets(updated)

    final_report = validate_skill_registry(updated)
    if not final_report["valid"]:
        raise SkillRegistryError(
            "version handoff produced invalid registry: "
            + "; ".join(final_report["errors"])
        )
    return updated


def resolve_registry_conflict(
    registry: Mapping[str, Any],
    conflict_set_id: str,
    *,
    applicable_claim_refs: list[str],
    reason: str,
    resolved_at: str,
    resolver_ref: str,
) -> dict[str, Any]:
    """Record applicability while preserving all immutable conflict evidence."""

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot update invalid registry: " + "; ".join(report["errors"])
        )
    conflict_id = _nonempty(conflict_set_id, "conflict_set_id")
    updated = deepcopy(dict(registry))
    target_index = None
    for index, conflict_set in enumerate(updated["conflict_sets"]):
        if conflict_set.get("conflict_set_id") == conflict_id:
            target_index = index
            break
    if target_index is None:
        raise SkillRegistryError(f"conflict_set is not registered: {conflict_id}")

    try:
        resolved = record_conflict_resolution(
            updated["conflict_sets"][target_index],
            applicable_claim_refs=applicable_claim_refs,
            reason=reason,
            resolved_at=resolved_at,
            resolver_ref=resolver_ref,
        )
    except ConflictSetError as exc:
        raise SkillRegistryError(f"cannot resolve conflict_set: {exc}") from exc

    updated["conflict_sets"][target_index] = resolved
    updated["updated_at"] = _nonempty(resolved_at, "resolved_at")

    final_report = validate_skill_registry(updated)
    if not final_report["valid"]:
        raise SkillRegistryError(
            "conflict resolution produced invalid registry: "
            + "; ".join(final_report["errors"])
        )
    return updated


def discover_conflict_sets(
    registry: Mapping[str, Any],
    *,
    skill_id: str | None = None,
) -> list[dict[str, Any]]:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot discover from invalid registry: " + "; ".join(report["errors"])
        )
    if skill_id is None:
        return deepcopy(list(registry["conflict_sets"]))
    skill_key = _nonempty(skill_id, "skill_id")
    return [
        deepcopy(dict(item))
        for item in registry["conflict_sets"]
        if any(
            member.get("skill_id") == skill_key
            for member in item.get("members", [])
            if isinstance(member, Mapping)
        )
    ]


def discover_skill(
    registry: Mapping[str, Any],
    skill_id: str,
    *,
    version: int | None = None,
) -> dict[str, Any]:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot discover from invalid registry: " + "; ".join(report["errors"])
        )

    skill_key = _nonempty(skill_id, "skill_id")
    record = registry["skills"].get(skill_key)
    if record is None:
        raise SkillRegistryError(f"skill is not registered: {skill_key}")

    selected_version = (
        record["current_version"]
        if version is None
        else _positive_int(version, "version")
    )
    entry = record["versions"].get(str(selected_version))
    if entry is None:
        raise SkillRegistryError(
            f"skill version is not registered: {skill_key}@{selected_version}"
        )
    if version is None and entry.get("state") != PROMOTED:
        raise SkillRegistryError(
            f"current skill is not usable: {skill_key}@{selected_version} "
            f"state={entry.get('state')}"
        )
    return deepcopy(dict(entry))


def registry_bytes(registry: Mapping[str, Any]) -> bytes:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise SkillRegistryError(
            "cannot serialize invalid registry: " + "; ".join(report["errors"])
        )
    return (
        json.dumps(registry, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def save_skill_registry(registry: Mapping[str, Any], path: str | Path) -> None:
    target = Path(path)
    payload = registry_bytes(registry)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_skill_registry(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillRegistryError(f"cannot read registry: {target}") from exc
    if not isinstance(value, dict):
        raise SkillRegistryError("registry JSON must be an object")

    report = validate_skill_registry(value)
    if not report["valid"]:
        raise SkillRegistryError("invalid registry: " + "; ".join(report["errors"]))
    return value
