"""Deterministic structured-claim diffing with explicit split/merge lineage.

P2D-A deliberately does not infer SPLIT or MERGED from text similarity.
Without explicit lineage, disappearing and appearing claim identities remain
REMOVED and ADDED.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

from .claims import ClaimError, normalize_claim, normalize_claim_key, unit_id_for
from .faithfulness import candidate_sha256
from .provenance import ProvenanceError, normalize_knowledge_id

DIFF_SCHEMA_VERSION = "0.1"
DIFF_KINDS = frozenset({
    "ADDED",
    "REMOVED",
    "CHANGED",
    "UNCHANGED",
    "SPLIT",
    "MERGED",
})
LINEAGE_KINDS = frozenset({"SPLIT", "MERGED"})
_KIND_ORDER = {
    "UNCHANGED": 0,
    "CHANGED": 1,
    "REMOVED": 2,
    "ADDED": 3,
    "SPLIT": 4,
    "MERGED": 5,
}


class KnowledgeDiffError(ValueError):
    """Raised when two candidates cannot be diffed without guessing."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise KnowledgeDiffError("diff value is not canonical JSON data") from exc


def _sha256(value: Any) -> str:
    return f"sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def _knowledge_id(candidate: Mapping[str, Any]) -> str:
    if not isinstance(candidate, Mapping):
        raise KnowledgeDiffError("candidate must be a mapping")
    try:
        return normalize_knowledge_id(candidate.get("knowledge_id"))
    except ProvenanceError as exc:
        raise KnowledgeDiffError(str(exc)) from exc


def _semantic_payload(unit: Mapping[str, Any]) -> dict[str, Any]:
    """Structured knowledge semantics; excludes prose and provenance coordinates."""

    if not isinstance(unit, Mapping):
        raise KnowledgeDiffError("candidate unit must be a mapping")
    try:
        claim = normalize_claim(unit.get("claim"))
    except ClaimError as exc:
        raise KnowledgeDiffError(str(exc)) from exc

    kind = unit.get("kind")
    if not isinstance(kind, str) or not kind:
        raise KnowledgeDiffError("candidate unit requires kind")

    conflicts = unit.get("conflicts_with", [])
    if not isinstance(conflicts, list) or any(
        not isinstance(item, str) or not item for item in conflicts
    ):
        raise KnowledgeDiffError("conflicts_with must be a string list")

    return {
        "kind": kind,
        "claim": claim,
        "conflicts_with": sorted(dict.fromkeys(conflicts)),
    }


def claim_semantic_sha256(unit: Mapping[str, Any]) -> str:
    """Hash semantic claim content, not title/body/support/source coordinates."""

    return _sha256(_semantic_payload(unit))


def _unit_map(candidate: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    knowledge_id = _knowledge_id(candidate)
    units = candidate.get("units")
    if not isinstance(units, list) or not units:
        raise KnowledgeDiffError("candidate requires non-empty units")

    result: dict[str, Mapping[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, Mapping):
            raise KnowledgeDiffError("candidate unit must be a mapping")
        try:
            claim = normalize_claim(unit.get("claim"))
        except ClaimError as exc:
            raise KnowledgeDiffError(str(exc)) from exc
        claim_key = claim["claim_key"]
        expected_unit_id = unit_id_for(knowledge_id, claim_key)
        if unit.get("unit_id") != expected_unit_id:
            raise KnowledgeDiffError(
                "candidate unit_id must match knowledge_id + claim_key"
            )
        if claim_key in result:
            raise KnowledgeDiffError(f"duplicate claim identity: {claim_key}")
        result[claim_key] = unit
    return result


def _compiler_profile(candidate: Mapping[str, Any]) -> str | None:
    compiler = candidate.get("compiler")
    if not isinstance(compiler, Mapping):
        return None
    value = compiler.get("compiler_profile_id")
    return value if isinstance(value, str) and value else None


def _changed_fields(
    old_unit: Mapping[str, Any],
    new_unit: Mapping[str, Any],
) -> list[str]:
    old_payload = _semantic_payload(old_unit)
    new_payload = _semantic_payload(new_unit)
    fields: list[str] = []

    if old_payload["kind"] != new_payload["kind"]:
        fields.append("kind")

    old_claim = old_payload["claim"]
    new_claim = new_payload["claim"]
    for field in (
        "subject",
        "predicate",
        "value",
        "qualifiers",
        "scope",
        "validity",
        "executable",
    ):
        if old_claim[field] != new_claim[field]:
            fields.append(f"claim.{field}")

    if old_payload["conflicts_with"] != new_payload["conflicts_with"]:
        fields.append("conflicts_with")
    return fields


def _normalize_lineage(
    lineage: list[Mapping[str, Any]] | None,
    *,
    removed: set[str],
    added: set[str],
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    if lineage is None:
        return [], set(), set()
    if not isinstance(lineage, list):
        raise KnowledgeDiffError("lineage must be a list")

    normalized: list[dict[str, Any]] = []
    used_old: set[str] = set()
    used_new: set[str] = set()

    for relation in lineage:
        if not isinstance(relation, Mapping):
            raise KnowledgeDiffError("lineage relation must be a mapping")
        kind = relation.get("kind")
        if kind not in LINEAGE_KINDS:
            raise KnowledgeDiffError("lineage kind must be SPLIT or MERGED")

        raw_from = relation.get("from_claim_keys")
        raw_to = relation.get("to_claim_keys")
        reason = relation.get("reason")
        if (
            not isinstance(raw_from, list)
            or not raw_from
            or not isinstance(raw_to, list)
            or not raw_to
        ):
            raise KnowledgeDiffError("lineage relation requires from/to claim keys")
        if not isinstance(reason, str) or not reason.strip():
            raise KnowledgeDiffError("lineage relation requires non-empty reason")

        try:
            from_keys = [normalize_claim_key(item) for item in raw_from]
            to_keys = [normalize_claim_key(item) for item in raw_to]
        except ClaimError as exc:
            raise KnowledgeDiffError(str(exc)) from exc

        if len(set(from_keys)) != len(from_keys) or len(set(to_keys)) != len(to_keys):
            raise KnowledgeDiffError("lineage relation cannot repeat claim keys")
        if kind == "SPLIT" and not (len(from_keys) == 1 and len(to_keys) >= 2):
            raise KnowledgeDiffError("SPLIT requires one old claim and two or more new claims")
        if kind == "MERGED" and not (len(from_keys) >= 2 and len(to_keys) == 1):
            raise KnowledgeDiffError("MERGED requires two or more old claims and one new claim")

        if any(key not in removed for key in from_keys):
            raise KnowledgeDiffError(
                "lineage from_claim_keys must reference REMOVED identities"
            )
        if any(key not in added for key in to_keys):
            raise KnowledgeDiffError(
                "lineage to_claim_keys must reference ADDED identities"
            )
        if used_old.intersection(from_keys) or used_new.intersection(to_keys):
            raise KnowledgeDiffError(
                "a claim identity may participate in only one lineage relation"
            )

        used_old.update(from_keys)
        used_new.update(to_keys)
        normalized.append({
            "kind": kind,
            "from_claim_keys": sorted(from_keys),
            "to_claim_keys": sorted(to_keys),
            "reason": reason.strip(),
        })

    normalized.sort(
        key=lambda item: (
            _KIND_ORDER[item["kind"]],
            item["from_claim_keys"],
            item["to_claim_keys"],
        )
    )
    return normalized, used_old, used_new


def diff_candidates(
    old_candidate: Mapping[str, Any],
    new_candidate: Mapping[str, Any],
    *,
    lineage: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Diff two versions of the same knowledge identity without fuzzy inference."""

    old_knowledge = _knowledge_id(old_candidate)
    new_knowledge = _knowledge_id(new_candidate)
    if old_knowledge != new_knowledge:
        raise KnowledgeDiffError("knowledge_id mismatch; cross-identity diff is forbidden")

    old_units = _unit_map(old_candidate)
    new_units = _unit_map(new_candidate)
    old_keys = set(old_units)
    new_keys = set(new_units)

    shared = old_keys & new_keys
    removed = old_keys - new_keys
    added = new_keys - old_keys

    normalized_lineage, lineage_old, lineage_new = _normalize_lineage(
        lineage,
        removed=removed,
        added=added,
    )

    changes: list[dict[str, Any]] = []

    for claim_key in sorted(shared):
        old_unit = old_units[claim_key]
        new_unit = new_units[claim_key]
        old_hash = claim_semantic_sha256(old_unit)
        new_hash = claim_semantic_sha256(new_unit)
        if old_hash == new_hash:
            changes.append({
                "kind": "UNCHANGED",
                "claim_key": claim_key,
                "unit_id": old_unit["unit_id"],
                "semantic_sha256": old_hash,
            })
        else:
            changes.append({
                "kind": "CHANGED",
                "claim_key": claim_key,
                "unit_id": old_unit["unit_id"],
                "old_semantic_sha256": old_hash,
                "new_semantic_sha256": new_hash,
                "changed_fields": _changed_fields(old_unit, new_unit),
            })

    for claim_key in sorted(removed - lineage_old):
        unit = old_units[claim_key]
        changes.append({
            "kind": "REMOVED",
            "claim_key": claim_key,
            "unit_id": unit["unit_id"],
            "old_semantic_sha256": claim_semantic_sha256(unit),
        })

    for claim_key in sorted(added - lineage_new):
        unit = new_units[claim_key]
        changes.append({
            "kind": "ADDED",
            "claim_key": claim_key,
            "unit_id": unit["unit_id"],
            "new_semantic_sha256": claim_semantic_sha256(unit),
        })

    for relation in normalized_lineage:
        old_hashes = {
            key: claim_semantic_sha256(old_units[key])
            for key in relation["from_claim_keys"]
        }
        new_hashes = {
            key: claim_semantic_sha256(new_units[key])
            for key in relation["to_claim_keys"]
        }
        changes.append({
            **deepcopy(relation),
            "old_semantic_sha256": old_hashes,
            "new_semantic_sha256": new_hashes,
        })

    def sort_key(change: Mapping[str, Any]) -> tuple[Any, ...]:
        identity = change.get("claim_key")
        if isinstance(identity, str):
            keys = (identity,)
        else:
            keys = tuple(change.get("from_claim_keys", [])) + tuple(
                change.get("to_claim_keys", [])
            )
        return (_KIND_ORDER[change["kind"]], keys)

    changes.sort(key=sort_key)

    summary = {
        kind: sum(1 for item in changes if item["kind"] == kind)
        for kind in (
            "ADDED",
            "REMOVED",
            "CHANGED",
            "UNCHANGED",
            "SPLIT",
            "MERGED",
        )
    }

    old_profile = _compiler_profile(old_candidate)
    new_profile = _compiler_profile(new_candidate)

    return {
        "schema_version": DIFF_SCHEMA_VERSION,
        "knowledge_id": old_knowledge,
        "old_candidate_sha256": candidate_sha256(old_candidate),
        "new_candidate_sha256": candidate_sha256(new_candidate),
        "compiler_profile_changed": old_profile != new_profile,
        "old_compiler_profile_id": old_profile,
        "new_compiler_profile_id": new_profile,
        "source_ids_changed": old_candidate.get("source_ids") != new_candidate.get("source_ids"),
        "summary": summary,
        "changes": changes,
    }


def _require_sha256_ref(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise KnowledgeDiffError(f"{name} must be a sha256 reference")
    return value


def _require_unit_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("claim-")
        or len(value) != 70
        or any(ch not in "0123456789abcdef" for ch in value[6:])
    ):
        raise KnowledgeDiffError("unit_id must be a stable claim id")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if extra:
            detail.append("extra=" + ",".join(extra))
        raise KnowledgeDiffError(
            f"{name} fields must match exact contract"
            + (": " + "; ".join(detail) if detail else "")
        )


def _validate_hash_map(
    value: Any,
    *,
    expected_keys: list[str],
    name: str,
) -> None:
    if not isinstance(value, Mapping):
        raise KnowledgeDiffError(f"{name} must be a mapping")
    if set(value) != set(expected_keys):
        raise KnowledgeDiffError(f"{name} keys must match lineage claim keys")
    for key, digest in value.items():
        normalize_claim_key(key)
        _require_sha256_ref(digest, f"{name}.{key}")


def validate_knowledge_diff(report: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a durable KnowledgeDiff artifact independently of its producer."""

    if not isinstance(report, Mapping):
        raise KnowledgeDiffError("knowledge diff report must be a mapping")

    required = {
        "schema_version",
        "knowledge_id",
        "old_candidate_sha256",
        "new_candidate_sha256",
        "compiler_profile_changed",
        "old_compiler_profile_id",
        "new_compiler_profile_id",
        "source_ids_changed",
        "summary",
        "changes",
    }
    _exact_keys(report, required, "knowledge diff report")

    if report.get("schema_version") != DIFF_SCHEMA_VERSION:
        raise KnowledgeDiffError("knowledge diff schema_version mismatch")
    try:
        normalize_knowledge_id(report.get("knowledge_id"))
    except ProvenanceError as exc:
        raise KnowledgeDiffError(str(exc)) from exc

    _require_sha256_ref(report.get("old_candidate_sha256"), "old_candidate_sha256")
    _require_sha256_ref(report.get("new_candidate_sha256"), "new_candidate_sha256")

    for field in ("compiler_profile_changed", "source_ids_changed"):
        if not isinstance(report.get(field), bool):
            raise KnowledgeDiffError(f"{field} must be boolean")

    for field in ("old_compiler_profile_id", "new_compiler_profile_id"):
        value = report.get(field)
        if value is not None:
            _require_sha256_ref(value, field)

    summary = report.get("summary")
    summary_keys = {"ADDED", "REMOVED", "CHANGED", "UNCHANGED", "SPLIT", "MERGED"}
    if not isinstance(summary, Mapping):
        raise KnowledgeDiffError("summary must be a mapping")
    _exact_keys(summary, summary_keys, "summary")
    for kind in summary_keys:
        count = summary[kind]
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise KnowledgeDiffError(f"summary.{kind} must be a non-negative integer")

    changes = report.get("changes")
    if not isinstance(changes, list):
        raise KnowledgeDiffError("changes must be a list")

    observed_counts = {kind: 0 for kind in summary_keys}
    participated: set[str] = set()
    changed_field_names = {
        "kind",
        "claim.subject",
        "claim.predicate",
        "claim.value",
        "claim.qualifiers",
        "claim.scope",
        "claim.validity",
        "claim.executable",
        "conflicts_with",
    }

    for change in changes:
        if not isinstance(change, Mapping):
            raise KnowledgeDiffError("change record must be a mapping")
        kind = change.get("kind")
        if kind not in DIFF_KINDS:
            raise KnowledgeDiffError("change record has unknown kind")
        observed_counts[kind] += 1

        if kind == "UNCHANGED":
            _exact_keys(
                change,
                {"kind", "claim_key", "unit_id", "semantic_sha256"},
                "UNCHANGED change",
            )
            claim_key = normalize_claim_key(change.get("claim_key"))
            _require_unit_id(change.get("unit_id"))
            _require_sha256_ref(change.get("semantic_sha256"), "semantic_sha256")
            participants = [claim_key]

        elif kind == "CHANGED":
            _exact_keys(
                change,
                {
                    "kind",
                    "claim_key",
                    "unit_id",
                    "old_semantic_sha256",
                    "new_semantic_sha256",
                    "changed_fields",
                },
                "CHANGED change",
            )
            claim_key = normalize_claim_key(change.get("claim_key"))
            _require_unit_id(change.get("unit_id"))
            old_hash = _require_sha256_ref(
                change.get("old_semantic_sha256"),
                "old_semantic_sha256",
            )
            new_hash = _require_sha256_ref(
                change.get("new_semantic_sha256"),
                "new_semantic_sha256",
            )
            if old_hash == new_hash:
                raise KnowledgeDiffError("CHANGED hashes must differ")
            fields = change.get("changed_fields")
            if (
                not isinstance(fields, list)
                or not fields
                or any(field not in changed_field_names for field in fields)
                or len(set(fields)) != len(fields)
            ):
                raise KnowledgeDiffError(
                    "CHANGED changed_fields must be unique known semantic fields"
                )
            participants = [claim_key]

        elif kind == "REMOVED":
            _exact_keys(
                change,
                {"kind", "claim_key", "unit_id", "old_semantic_sha256"},
                "REMOVED change",
            )
            claim_key = normalize_claim_key(change.get("claim_key"))
            _require_unit_id(change.get("unit_id"))
            _require_sha256_ref(
                change.get("old_semantic_sha256"),
                "old_semantic_sha256",
            )
            participants = [claim_key]

        elif kind == "ADDED":
            _exact_keys(
                change,
                {"kind", "claim_key", "unit_id", "new_semantic_sha256"},
                "ADDED change",
            )
            claim_key = normalize_claim_key(change.get("claim_key"))
            _require_unit_id(change.get("unit_id"))
            _require_sha256_ref(
                change.get("new_semantic_sha256"),
                "new_semantic_sha256",
            )
            participants = [claim_key]

        else:
            _exact_keys(
                change,
                {
                    "kind",
                    "from_claim_keys",
                    "to_claim_keys",
                    "reason",
                    "old_semantic_sha256",
                    "new_semantic_sha256",
                },
                f"{kind} change",
            )
            raw_from = change.get("from_claim_keys")
            raw_to = change.get("to_claim_keys")
            reason = change.get("reason")
            if not isinstance(raw_from, list) or not isinstance(raw_to, list):
                raise KnowledgeDiffError(f"{kind} claim-key lists are required")
            from_keys = [normalize_claim_key(item) for item in raw_from]
            to_keys = [normalize_claim_key(item) for item in raw_to]
            if len(set(from_keys)) != len(from_keys) or len(set(to_keys)) != len(to_keys):
                raise KnowledgeDiffError(f"{kind} cannot repeat claim keys")
            if kind == "SPLIT" and not (len(from_keys) == 1 and len(to_keys) >= 2):
                raise KnowledgeDiffError(
                    "SPLIT requires one old claim and two or more new claims"
                )
            if kind == "MERGED" and not (len(from_keys) >= 2 and len(to_keys) == 1):
                raise KnowledgeDiffError(
                    "MERGED requires two or more old claims and one new claim"
                )
            if not isinstance(reason, str) or not reason.strip():
                raise KnowledgeDiffError(f"{kind} requires non-empty reason")
            _validate_hash_map(
                change.get("old_semantic_sha256"),
                expected_keys=from_keys,
                name="old_semantic_sha256",
            )
            _validate_hash_map(
                change.get("new_semantic_sha256"),
                expected_keys=to_keys,
                name="new_semantic_sha256",
            )
            participants = from_keys + to_keys

        overlap = participated.intersection(participants)
        if overlap:
            raise KnowledgeDiffError(
                "claim identity appears in more than one diff change: "
                + ", ".join(sorted(overlap))
            )
        participated.update(participants)

    if dict(summary) != observed_counts:
        raise KnowledgeDiffError("summary counts do not match change records")

    return {"valid": True, "errors": []}
