"""ConflictSet construction for preserving cross-skill disagreement.

P2D-B1 is deliberately conservative:
- same subject + predicate + different values are compared;
- exact applicability equality => CONTRADICTION;
- scope/qualifier/validity differences => VARIANCE with explicit dimensions;
- no averaging, no latest-wins, and no automatic resolution.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import itertools
import json
from typing import Any, Mapping

from .claims import ClaimError, normalize_claim, unit_id_for
from .provenance import (
    ProvenanceError,
    normalize_knowledge_id,
    skill_id_for,
)

CONFLICT_SET_SCHEMA_VERSION = "0.1"
OPEN = "OPEN"
RESOLVED = "RESOLVED"
CONTRADICTION = "CONTRADICTION"
VARIANCE = "VARIANCE"
VARIANCE_DIMENSIONS = ("scope", "qualifiers", "validity")


class ConflictSetError(ValueError):
    """Raised when conflict evidence is incomplete, ambiguous, or tampered."""


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
        raise ConflictSetError("conflict value is not canonical JSON data") from exc


def _sha256(value: Any) -> str:
    return f"sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def claim_sha256(claim: Mapping[str, Any]) -> str:
    try:
        normalized = normalize_claim(claim)
    except ClaimError as exc:
        raise ConflictSetError(str(exc)) from exc
    return _sha256(normalized)


def _compiler_profile_id(candidate: Mapping[str, Any]) -> str:
    compiler = candidate.get("compiler")
    if not isinstance(compiler, Mapping):
        raise ConflictSetError("candidate requires compiler identity")
    value = compiler.get("compiler_profile_id")
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise ConflictSetError("candidate requires compiler_profile_id")
    return value


def _source_bindings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ConflictSetError("conflict member requires source_bindings")
    normalized: list[dict[str, Any]] = []
    for binding in value:
        if not isinstance(binding, Mapping):
            raise ConflictSetError("source binding must be a mapping")
        if set(binding) != {"source_id", "section_ids"}:
            raise ConflictSetError("source binding fields must match exact contract")
        source_id = binding.get("source_id")
        section_ids = binding.get("section_ids")
        if (
            not isinstance(source_id, str)
            or not source_id.startswith("sha256:")
            or len(source_id) != 71
            or any(ch not in "0123456789abcdef" for ch in source_id[7:])
        ):
            raise ConflictSetError("source binding requires source_id")
        if (
            not isinstance(section_ids, list)
            or not section_ids
            or any(not isinstance(item, str) or not item for item in section_ids)
        ):
            raise ConflictSetError("source binding requires section_ids")
        normalized.append({
            "source_id": source_id,
            "section_ids": list(dict.fromkeys(section_ids)),
        })
    return normalized


def _candidate_members(candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(candidate, Mapping):
        raise ConflictSetError("candidate must be a mapping")
    try:
        knowledge_id = normalize_knowledge_id(candidate.get("knowledge_id"))
    except ProvenanceError as exc:
        raise ConflictSetError(str(exc)) from exc
    profile_id = _compiler_profile_id(candidate)
    try:
        skill_id = skill_id_for(knowledge_id, profile_id)
    except ProvenanceError as exc:
        raise ConflictSetError(str(exc)) from exc

    units = candidate.get("units")
    if not isinstance(units, list) or not units:
        raise ConflictSetError("candidate requires units")

    members: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for unit in units:
        if not isinstance(unit, Mapping):
            raise ConflictSetError("candidate unit must be a mapping")
        try:
            claim = normalize_claim(unit.get("claim"))
        except ClaimError as exc:
            raise ConflictSetError(str(exc)) from exc
        unit_id = unit_id_for(knowledge_id, claim["claim_key"])
        if unit.get("unit_id") != unit_id:
            raise ConflictSetError("candidate unit_id does not match claim identity")
        claim_ref = f"{skill_id}#{unit_id}"
        if claim_ref in seen_refs:
            raise ConflictSetError("duplicate conflict claim_ref")
        seen_refs.add(claim_ref)
        members.append({
            "claim_ref": claim_ref,
            "skill_id": skill_id,
            "knowledge_id": knowledge_id,
            "compiler_profile_id": profile_id,
            "unit_id": unit_id,
            "claim_key": claim["claim_key"],
            "claim_sha256": claim_sha256(claim),
            "claim": claim,
            "source_bindings": _source_bindings(unit.get("source_bindings")),
        })
    return members


def _relation(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> dict[str, Any] | None:
    if left["skill_id"] == right["skill_id"]:
        return None

    left_claim = left["claim"]
    right_claim = right["claim"]
    if left_claim["value"] == right_claim["value"]:
        return None

    dimensions = [
        field
        for field in VARIANCE_DIMENSIONS
        if left_claim[field] != right_claim[field]
    ]
    classification = CONTRADICTION if not dimensions else VARIANCE

    refs = sorted([left["claim_ref"], right["claim_ref"]])
    return {
        "left_claim_ref": refs[0],
        "right_claim_ref": refs[1],
        "classification": classification,
        "variance_dimensions": dimensions,
    }


def _identity_payload(conflict_set: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "subject": conflict_set["subject"],
        "predicate": conflict_set["predicate"],
        "members": [
            {
                "claim_ref": member["claim_ref"],
                "claim_sha256": member["claim_sha256"],
            }
            for member in conflict_set["members"]
        ],
        "relations": conflict_set["relations"],
    }


def _conflict_set_id(conflict_set: Mapping[str, Any]) -> str:
    return f"conflict-{sha256(_canonical_bytes(_identity_payload(conflict_set))).hexdigest()}"


def scan_conflict_sets(
    candidates: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Scan candidate claims without choosing a winner or averaging values."""

    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ConflictSetError("cross-skill conflict scan requires at least two candidates")

    members: list[dict[str, Any]] = []
    for candidate in candidates:
        members.extend(_candidate_members(candidate))

    refs = [member["claim_ref"] for member in members]
    if len(set(refs)) != len(refs):
        raise ConflictSetError("duplicate claim_ref across scanned candidates")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for member in members:
        claim = member["claim"]
        groups.setdefault(
            (claim["subject"], claim["predicate"]),
            [],
        ).append(member)

    result: list[dict[str, Any]] = []
    for (subject, predicate), group in sorted(groups.items()):
        if len(group) < 2:
            continue
        ordered_members = sorted(group, key=lambda item: item["claim_ref"])
        relations: list[dict[str, Any]] = []
        for left, right in itertools.combinations(ordered_members, 2):
            relation = _relation(left, right)
            if relation is not None:
                relations.append(relation)
        if not relations:
            continue
        relations.sort(
            key=lambda item: (
                item["left_claim_ref"],
                item["right_claim_ref"],
                item["classification"],
                item["variance_dimensions"],
            )
        )
        participating_refs = {
            ref
            for relation in relations
            for ref in (
                relation["left_claim_ref"],
                relation["right_claim_ref"],
            )
        }
        conflict_members = [
            member
            for member in ordered_members
            if member["claim_ref"] in participating_refs
        ]
        conflict_set = {
            "schema_version": CONFLICT_SET_SCHEMA_VERSION,
            "conflict_set_id": "",
            "subject": subject,
            "predicate": predicate,
            "state": OPEN,
            "members": deepcopy(conflict_members),
            "relations": relations,
            "resolution": None,
        }
        conflict_set["conflict_set_id"] = _conflict_set_id(conflict_set)
        validate_conflict_set(conflict_set)
        result.append(conflict_set)

    result.sort(key=lambda item: item["conflict_set_id"])
    return result


def _exact_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    if set(value) != expected:
        raise ConflictSetError(f"{name} fields must match exact contract")


def validate_conflict_set(conflict_set: Mapping[str, Any]) -> dict[str, Any]:
    """Validate ConflictSet independently of its producer."""

    if not isinstance(conflict_set, Mapping):
        raise ConflictSetError("conflict_set must be a mapping")
    _exact_keys(
        conflict_set,
        {
            "schema_version",
            "conflict_set_id",
            "subject",
            "predicate",
            "state",
            "members",
            "relations",
            "resolution",
        },
        "conflict_set",
    )
    if conflict_set.get("schema_version") != CONFLICT_SET_SCHEMA_VERSION:
        raise ConflictSetError("conflict_set schema_version mismatch")
    subject = conflict_set.get("subject")
    predicate = conflict_set.get("predicate")
    if not isinstance(subject, str) or not subject:
        raise ConflictSetError("conflict_set requires subject")
    if not isinstance(predicate, str) or not predicate:
        raise ConflictSetError("conflict_set requires predicate")
    if conflict_set.get("state") not in {OPEN, RESOLVED}:
        raise ConflictSetError("conflict_set state is invalid")

    raw_members = conflict_set.get("members")
    if not isinstance(raw_members, list) or len(raw_members) < 2:
        raise ConflictSetError("conflict_set requires at least two members")
    members: dict[str, dict[str, Any]] = {}
    normalized_member_list: list[dict[str, Any]] = []
    for member in raw_members:
        if not isinstance(member, Mapping):
            raise ConflictSetError("conflict member must be a mapping")
        _exact_keys(
            member,
            {
                "claim_ref",
                "skill_id",
                "knowledge_id",
                "compiler_profile_id",
                "unit_id",
                "claim_key",
                "claim_sha256",
                "claim",
                "source_bindings",
            },
            "conflict member",
        )
        try:
            knowledge_id = normalize_knowledge_id(member.get("knowledge_id"))
        except ProvenanceError as exc:
            raise ConflictSetError(str(exc)) from exc
        profile_id = member.get("compiler_profile_id")
        try:
            expected_skill_id = skill_id_for(knowledge_id, profile_id)
        except ProvenanceError as exc:
            raise ConflictSetError(str(exc)) from exc
        if member.get("skill_id") != expected_skill_id:
            raise ConflictSetError("conflict member skill_id mismatch")
        try:
            claim = normalize_claim(member.get("claim"))
        except ClaimError as exc:
            raise ConflictSetError(str(exc)) from exc
        if claim["subject"] != subject or claim["predicate"] != predicate:
            raise ConflictSetError("conflict member locator mismatch")
        unit_id = unit_id_for(knowledge_id, claim["claim_key"])
        if member.get("unit_id") != unit_id:
            raise ConflictSetError("conflict member unit_id mismatch")
        if member.get("claim_key") != claim["claim_key"]:
            raise ConflictSetError("conflict member claim_key mismatch")
        if member.get("claim_sha256") != claim_sha256(claim):
            raise ConflictSetError("conflict member claim_sha256 mismatch")
        expected_ref = f"{expected_skill_id}#{unit_id}"
        if member.get("claim_ref") != expected_ref:
            raise ConflictSetError("conflict member claim_ref mismatch")
        if expected_ref in members:
            raise ConflictSetError("duplicate conflict member claim_ref")
        bindings = _source_bindings(member.get("source_bindings"))
        normalized = {
            **deepcopy(dict(member)),
            "claim": claim,
            "source_bindings": bindings,
        }
        members[expected_ref] = normalized
        normalized_member_list.append(normalized)

    if [member["claim_ref"] for member in normalized_member_list] != sorted(members):
        raise ConflictSetError("conflict members must be sorted by claim_ref")

    raw_relations = conflict_set.get("relations")
    if not isinstance(raw_relations, list) or not raw_relations:
        raise ConflictSetError("conflict_set requires relations")
    normalized_relations: list[dict[str, Any]] = []
    pair_keys: set[tuple[str, str]] = set()
    for relation in raw_relations:
        if not isinstance(relation, Mapping):
            raise ConflictSetError("conflict relation must be a mapping")
        _exact_keys(
            relation,
            {
                "left_claim_ref",
                "right_claim_ref",
                "classification",
                "variance_dimensions",
            },
            "conflict relation",
        )
        left_ref = relation.get("left_claim_ref")
        right_ref = relation.get("right_claim_ref")
        if (
            not isinstance(left_ref, str)
            or not isinstance(right_ref, str)
            or left_ref >= right_ref
            or left_ref not in members
            or right_ref not in members
        ):
            raise ConflictSetError("conflict relation references are invalid")
        pair = (left_ref, right_ref)
        if pair in pair_keys:
            raise ConflictSetError("duplicate conflict relation pair")
        pair_keys.add(pair)
        expected = _relation(members[left_ref], members[right_ref])
        if expected is None:
            raise ConflictSetError("equal-value claims cannot form conflict relation")
        if dict(relation) != expected:
            raise ConflictSetError("conflict relation classification mismatch")
        normalized_relations.append(expected)

    if normalized_relations != sorted(
        normalized_relations,
        key=lambda item: (
            item["left_claim_ref"],
            item["right_claim_ref"],
            item["classification"],
            item["variance_dimensions"],
        ),
    ):
        raise ConflictSetError("conflict relations must be deterministically sorted")

    resolution = conflict_set.get("resolution")
    state = conflict_set.get("state")
    if state == OPEN:
        if resolution is not None:
            raise ConflictSetError("OPEN conflict_set cannot carry resolution")
    else:
        if not isinstance(resolution, Mapping):
            raise ConflictSetError("RESOLVED conflict_set requires resolution")
        _exact_keys(
            resolution,
            {
                "applicable_claim_refs",
                "reason",
                "resolved_at",
                "resolver_ref",
            },
            "conflict resolution",
        )
        refs = resolution.get("applicable_claim_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or len(set(refs)) != len(refs)
            or refs != sorted(refs)
            or any(ref not in members for ref in refs)
        ):
            raise ConflictSetError("resolution applicable_claim_refs are invalid")
        for field in ("reason", "resolved_at", "resolver_ref"):
            value = resolution.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ConflictSetError(f"resolution {field} must be non-empty")

        selected = set(refs)
        for relation in normalized_relations:
            if relation["classification"] != CONTRADICTION:
                continue
            if (
                relation["left_claim_ref"] in selected
                and relation["right_claim_ref"] in selected
            ):
                raise ConflictSetError(
                    "resolution cannot mark both sides of an exact contradiction applicable"
                )

    expected_id = _conflict_set_id(conflict_set)
    if conflict_set.get("conflict_set_id") != expected_id:
        raise ConflictSetError("conflict_set_id mismatch")

    return {"valid": True, "errors": []}


def record_conflict_resolution(
    conflict_set: Mapping[str, Any],
    *,
    applicable_claim_refs: list[str],
    reason: str,
    resolved_at: str,
    resolver_ref: str,
) -> dict[str, Any]:
    """Record applicability without deleting or rewriting any disagreeing claim."""

    validate_conflict_set(conflict_set)
    if conflict_set.get("state") != OPEN:
        raise ConflictSetError("only OPEN conflict sets may be resolved")
    if (
        not isinstance(applicable_claim_refs, list)
        or not applicable_claim_refs
        or len(set(applicable_claim_refs)) != len(applicable_claim_refs)
    ):
        raise ConflictSetError("applicable_claim_refs must be a non-empty unique list")
    member_refs = {member["claim_ref"] for member in conflict_set["members"]}
    if any(ref not in member_refs for ref in applicable_claim_refs):
        raise ConflictSetError("resolution references unknown conflict member")
    for value, name in (
        (reason, "reason"),
        (resolved_at, "resolved_at"),
        (resolver_ref, "resolver_ref"),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ConflictSetError(f"{name} must be non-empty")

    updated = deepcopy(dict(conflict_set))
    original_members = deepcopy(updated["members"])
    original_relations = deepcopy(updated["relations"])
    updated["state"] = RESOLVED
    updated["resolution"] = {
        "applicable_claim_refs": sorted(applicable_claim_refs),
        "reason": reason.strip(),
        "resolved_at": resolved_at.strip(),
        "resolver_ref": resolver_ref.strip(),
    }
    if updated["members"] != original_members or updated["relations"] != original_relations:
        raise ConflictSetError("resolution must not mutate conflict evidence")
    validate_conflict_set(updated)
    return updated
