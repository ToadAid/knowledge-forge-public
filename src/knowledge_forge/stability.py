"""Deterministic unchanged-section carry-forward planning for recompilation."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping


class StabilityError(ValueError):
    """Raised when recompile stability inputs are incomplete or ambiguous."""


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
        raise StabilityError("value is not canonical JSON data") from exc


def semantic_unit_sha256(unit: Mapping[str, Any]) -> str:
    """Hash knowledge semantics while excluding mutable provenance coordinates."""

    if not isinstance(unit, Mapping):
        raise StabilityError("unit must be a mapping")
    payload = {
        key: deepcopy(unit[key])
        for key in ("unit_id", "kind", "title", "body", "claim", "conflicts_with")
        if key in unit
    }
    if not all(key in payload for key in ("unit_id", "kind", "title", "body", "claim")):
        raise StabilityError("unit is missing semantic fields")
    return f"sha256:{sha256(_canonical_bytes(payload)).hexdigest()}"


def _source_map(sources: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(sources, list) or not sources:
        raise StabilityError("normalized sources must be a non-empty list")
    result: dict[str, Mapping[str, Any]] = {}
    for source in sources:
        if not isinstance(source, Mapping):
            raise StabilityError("normalized source must be a mapping")
        source_id = source.get("source_id")
        sections = source.get("sections")
        if not isinstance(source_id, str) or not isinstance(sections, list):
            raise StabilityError("normalized source identity/sections are invalid")
        if source_id in result:
            raise StabilityError("duplicate normalized source_id")
        result[source_id] = source
    return result


def _section(source: Mapping[str, Any], section_id: str) -> Mapping[str, Any]:
    matches = [
        section
        for section in source.get("sections", [])
        if isinstance(section, Mapping) and section.get("section_id") == section_id
    ]
    if len(matches) != 1:
        raise StabilityError("bound section_id must resolve exactly once")
    return matches[0]


def _match_section(
    old_source: Mapping[str, Any],
    new_source: Mapping[str, Any],
    old_section_id: str,
) -> tuple[str, dict[str, Any] | None]:
    old_section = _section(old_source, old_section_id)
    old_hash = old_section.get("section_sha256")
    if not isinstance(old_hash, str) or len(old_hash) != 64:
        raise StabilityError("old section requires section_sha256")

    if old_source.get("source_id") == new_source.get("source_id"):
        direct = [
            section
            for section in new_source.get("sections", [])
            if isinstance(section, Mapping)
            and section.get("section_id") == old_section_id
            and section.get("section_sha256") == old_hash
        ]
        if len(direct) == 1:
            return "UNCHANGED", {
                "old_source_id": old_source["source_id"],
                "old_section_id": old_section_id,
                "new_source_id": new_source["source_id"],
                "new_section_id": old_section_id,
                "section_sha256": old_hash,
            }

    matches = [
        section
        for section in new_source.get("sections", [])
        if isinstance(section, Mapping) and section.get("section_sha256") == old_hash
    ]
    if len(matches) == 1:
        return "UNCHANGED", {
            "old_source_id": old_source["source_id"],
            "old_section_id": old_section_id,
            "new_source_id": new_source["source_id"],
            "new_section_id": matches[0]["section_id"],
            "section_sha256": old_hash,
        }
    if len(matches) > 1:
        return "AMBIGUOUS", None
    return "CHANGED", None


def plan_recompile(
    previous_candidate: Mapping[str, Any],
    previous_sources: list[Mapping[str, Any]],
    current_sources: list[Mapping[str, Any]],
    *,
    source_replacements: Mapping[str, str],
    current_compiler_profile_id: str,
) -> dict[str, Any]:
    """Plan carry-forward only for uniquely unchanged bound sections.

    Source replacement identity is explicit input; the forge never guesses which
    new document version replaces which old source.
    """

    if not isinstance(previous_candidate, Mapping):
        raise StabilityError("previous_candidate must be a mapping")
    compiler = previous_candidate.get("compiler")
    units = previous_candidate.get("units")
    source_ids = previous_candidate.get("source_ids")
    if not isinstance(compiler, Mapping) or not isinstance(units, list) or not units:
        raise StabilityError("previous candidate is incomplete")
    if not isinstance(source_ids, list) or not source_ids:
        raise StabilityError("previous candidate requires source_ids")
    previous_profile = compiler.get("compiler_profile_id")
    if not isinstance(current_compiler_profile_id, str) or not current_compiler_profile_id:
        raise StabilityError("current_compiler_profile_id must be non-empty")

    old_sources = _source_map(previous_sources)
    new_sources = _source_map(current_sources)
    if set(source_ids) != set(old_sources):
        raise StabilityError("previous source set must match candidate source_ids")
    if not isinstance(source_replacements, Mapping):
        raise StabilityError("source_replacements must be a mapping")

    decisions: list[dict[str, Any]] = []
    compiler_changed = previous_profile != current_compiler_profile_id

    for unit in units:
        if not isinstance(unit, Mapping):
            raise StabilityError("candidate unit must be a mapping")
        unit_id = unit.get("unit_id")
        bindings = unit.get("source_bindings")
        if not isinstance(unit_id, str) or not isinstance(bindings, list) or not bindings:
            raise StabilityError("candidate unit identity/bindings are invalid")
        semantic_hash = semantic_unit_sha256(unit)
        if compiler_changed:
            decisions.append({
                "unit_id": unit_id,
                "action": "RECOMPILE",
                "reason": "COMPILER_CHANGED",
                "semantic_unit_sha256": semantic_hash,
                "section_map": [],
            })
            continue

        section_map: list[dict[str, Any]] = []
        statuses: list[str] = []
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise StabilityError("source binding must be a mapping")
            old_source_id = binding.get("source_id")
            section_ids = binding.get("section_ids")
            new_source_id = source_replacements.get(old_source_id)
            if old_source_id not in old_sources or new_source_id not in new_sources:
                statuses.append("CHANGED")
                continue
            if not isinstance(section_ids, list) or not section_ids:
                raise StabilityError("source binding section_ids are invalid")
            for section_id in section_ids:
                status, mapping = _match_section(
                    old_sources[old_source_id],
                    new_sources[new_source_id],
                    section_id,
                )
                statuses.append(status)
                if mapping is not None:
                    section_map.append(mapping)

        if statuses and all(status == "UNCHANGED" for status in statuses):
            action = "CARRY_FORWARD"
            reason = "UNCHANGED_SUPPORT"
        elif "AMBIGUOUS" in statuses:
            action = "RECOMPILE"
            reason = "AMBIGUOUS_SECTION_MATCH"
        else:
            action = "RECOMPILE"
            reason = "SOURCE_SECTION_CHANGED"

        decisions.append({
            "unit_id": unit_id,
            "action": action,
            "reason": reason,
            "semantic_unit_sha256": semantic_hash,
            "section_map": section_map if action == "CARRY_FORWARD" else [],
        })

    new_sections: list[dict[str, str]] = []
    for old_source_id, new_source_id in sorted(source_replacements.items()):
        old_source = old_sources.get(old_source_id)
        new_source = new_sources.get(new_source_id)
        if old_source is None or new_source is None:
            continue
        old_hashes = {
            section.get("section_sha256")
            for section in old_source.get("sections", [])
            if isinstance(section, Mapping)
        }
        for section in new_source.get("sections", []):
            if isinstance(section, Mapping) and section.get("section_sha256") not in old_hashes:
                new_sections.append({
                    "source_id": new_source_id,
                    "section_id": section["section_id"],
                    "section_sha256": section["section_sha256"],
                })

    return {
        "compiler_profile_changed": compiler_changed,
        "previous_compiler_profile_id": previous_profile,
        "current_compiler_profile_id": current_compiler_profile_id,
        "decisions": decisions,
        "new_sections": new_sections,
    }


def carry_forward_unit(
    unit: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebind provenance coordinates while preserving semantic unit bytes."""

    if decision.get("action") != "CARRY_FORWARD":
        raise StabilityError("only CARRY_FORWARD decisions may reuse a unit")
    if decision.get("unit_id") != unit.get("unit_id"):
        raise StabilityError("decision unit_id does not match unit")
    before = semantic_unit_sha256(unit)
    if decision.get("semantic_unit_sha256") != before:
        raise StabilityError("decision semantic hash does not match unit")

    mappings = decision.get("section_map")
    if not isinstance(mappings, list) or not mappings:
        raise StabilityError("carry-forward decision requires section_map")
    lookup = {
        (item["old_source_id"], item["old_section_id"]): item
        for item in mappings
        if isinstance(item, Mapping)
    }

    updated = deepcopy(dict(unit))
    rebound: list[dict[str, Any]] = []
    for binding in unit.get("source_bindings", []):
        old_source_id = binding["source_id"]
        grouped: dict[str, list[str]] = {}
        for old_section_id in binding["section_ids"]:
            mapping = lookup.get((old_source_id, old_section_id))
            if mapping is None:
                raise StabilityError("carry-forward mapping is incomplete")
            grouped.setdefault(mapping["new_source_id"], []).append(mapping["new_section_id"])
        for new_source_id, section_ids in grouped.items():
            rebound.append({"source_id": new_source_id, "section_ids": section_ids})
    updated["source_bindings"] = rebound

    if "support" in updated:
        new_support: list[dict[str, Any]] = []
        for evidence in updated["support"]:
            mapping = lookup.get((evidence["source_id"], evidence["section_id"]))
            if mapping is None:
                raise StabilityError("support mapping is incomplete")
            item = deepcopy(evidence)
            item["source_id"] = mapping["new_source_id"]
            item["section_id"] = mapping["new_section_id"]
            item["section_sha256"] = mapping["section_sha256"]
            new_support.append(item)
        updated["support"] = new_support

    if semantic_unit_sha256(updated) != before:
        raise StabilityError("carry-forward changed semantic unit payload")
    return updated
