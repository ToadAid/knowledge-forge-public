"""Typed provider-neutral knowledge candidate model."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from .claims import ClaimError, normalize_claim, unit_id_for
from .provenance import (
    ProvenanceError,
    normalize_compiler_profile,
    normalize_knowledge_id,
)

KNOWLEDGE_SCHEMA_VERSION = "0.3"
CANDIDATE_STATE = "COMPILED_CANDIDATE"
AUTHORITY = "KNOWLEDGE_ONLY"
UNIT_TYPES = frozenset({
    "CONCEPT", "FRAMEWORK", "PROCEDURE", "DECISION_RULE", "TRADEOFF",
    "ANTI_PATTERN", "DEFINITION", "EXAMPLE",
})
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class KnowledgeCandidateError(ValueError):
    """Raised when semantic output cannot cross the typed knowledge boundary."""


def _valid_source_id(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(ch in "0123456789abcdef" for ch in digest)


def _normalize_binding(binding: Mapping[str, Any], allowed_sources: set[str]) -> dict[str, Any]:
    source_id = binding.get("source_id")
    section_ids = binding.get("section_ids")
    if not _valid_source_id(source_id) or source_id not in allowed_sources:
        raise KnowledgeCandidateError("knowledge unit references an unbound source_id")
    if not isinstance(section_ids, list) or not section_ids:
        raise KnowledgeCandidateError("source binding requires non-empty section_ids")
    if any(not isinstance(section_id, str) or not section_id for section_id in section_ids):
        raise KnowledgeCandidateError("section_ids must contain non-empty strings")
    return {"source_id": source_id, "section_ids": list(dict.fromkeys(section_ids))}


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _normalize_support(
    evidence: Mapping[str, Any],
    *,
    allowed_sources: set[str],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    allowed_keys = {
        "source_id",
        "section_id",
        "section_sha256",
        "coordinate_space",
        "start_byte",
        "end_byte",
        "span_sha256",
    }
    unknown = set(evidence) - allowed_keys
    if unknown:
        raise KnowledgeCandidateError(
            "support evidence contains unknown fields: " + ", ".join(sorted(unknown))
        )
    source_id = evidence.get("source_id")
    section_id = evidence.get("section_id")
    start = evidence.get("start_byte")
    end = evidence.get("end_byte")
    if not _valid_source_id(source_id) or source_id not in allowed_sources:
        raise KnowledgeCandidateError("support evidence references an unbound source_id")
    if not isinstance(section_id, str) or not section_id:
        raise KnowledgeCandidateError("support evidence requires section_id")
    if evidence.get("coordinate_space") != "normalized_section_utf8_bytes":
        raise KnowledgeCandidateError("support evidence coordinate_space is invalid")
    if not _hex64(evidence.get("section_sha256")) or not _hex64(evidence.get("span_sha256")):
        raise KnowledgeCandidateError("support evidence requires sha256 digests")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
    ):
        raise KnowledgeCandidateError("support evidence byte span is invalid")
    if not any(
        binding["source_id"] == source_id and section_id in binding["section_ids"]
        for binding in bindings
    ):
        raise KnowledgeCandidateError("support evidence is outside source_bindings")
    return {
        "source_id": source_id,
        "section_id": section_id,
        "section_sha256": evidence["section_sha256"],
        "coordinate_space": "normalized_section_utf8_bytes",
        "start_byte": start,
        "end_byte": end,
        "span_sha256": evidence["span_sha256"],
    }


def _knowledge_id(value: Any) -> str:
    try:
        return normalize_knowledge_id(value)
    except ProvenanceError as exc:
        raise KnowledgeCandidateError(str(exc)) from exc


def _profile(value: Any) -> dict[str, Any]:
    try:
        return normalize_compiler_profile(value)
    except ProvenanceError as exc:
        raise KnowledgeCandidateError(str(exc)) from exc


def _claim(value: Any) -> dict[str, Any]:
    try:
        return normalize_claim(value)
    except ClaimError as exc:
        raise KnowledgeCandidateError(str(exc)) from exc


def canonicalize_compiler_result(
    compiler_result: Mapping[str, Any],
    *,
    skill_slug: str,
    purpose: str = "reference",
) -> dict[str, Any]:
    """Convert an adapter result into the canonical typed knowledge candidate."""

    if compiler_result.get("authority") != AUTHORITY:
        raise KnowledgeCandidateError("compiler result must remain KNOWLEDGE_ONLY")
    if compiler_result.get("contract_version") != "0.2":
        raise KnowledgeCandidateError("compiler result contract_version must be 0.2")
    adapter_id = compiler_result.get("adapter_id")
    source_ids = compiler_result.get("request_source_ids")
    raw_candidate = compiler_result.get("candidate")
    knowledge_id = _knowledge_id(compiler_result.get("knowledge_id"))
    compiler_profile = _profile(compiler_result.get("compiler_profile"))

    if not isinstance(adapter_id, str) or not adapter_id.strip():
        raise KnowledgeCandidateError("compiler result requires adapter_id")
    if not isinstance(source_ids, list) or not source_ids or any(not _valid_source_id(v) for v in source_ids):
        raise KnowledgeCandidateError("compiler result requires valid request_source_ids")
    if not isinstance(raw_candidate, Mapping):
        raise KnowledgeCandidateError("compiler result candidate must be a mapping")
    if not isinstance(skill_slug, str) or not _ID_RE.fullmatch(skill_slug):
        raise KnowledgeCandidateError("skill_slug is not a safe identifier")
    if purpose not in {"reference", "study"}:
        raise KnowledgeCandidateError("purpose must be 'reference' or 'study'")

    title = raw_candidate.get("title")
    raw_units = raw_candidate.get("units")
    if not isinstance(title, str) or not title.strip():
        raise KnowledgeCandidateError("candidate requires a non-empty title")
    if not isinstance(raw_units, list) or not raw_units:
        raise KnowledgeCandidateError("candidate requires at least one knowledge unit")

    allowed_sources = set(source_ids)
    units: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            raise KnowledgeCandidateError("knowledge units must be mappings")
        kind = raw_unit.get("kind")
        unit_title = raw_unit.get("title")
        body = raw_unit.get("body")
        bindings = raw_unit.get("source_bindings")
        claim = _claim(raw_unit.get("claim"))
        unit_id = unit_id_for(knowledge_id, claim["claim_key"])
        supplied_unit_id = raw_unit.get("unit_id")
        if supplied_unit_id is not None and supplied_unit_id != unit_id:
            raise KnowledgeCandidateError(
                "unit_id must equal deterministic knowledge_id + claim_key identity"
            )
        if unit_id in seen_ids:
            raise KnowledgeCandidateError(f"duplicate claim identity: {claim['claim_key']}")
        if kind not in UNIT_TYPES:
            raise KnowledgeCandidateError(f"unsupported knowledge unit kind: {kind!r}")
        if not isinstance(unit_title, str) or not unit_title.strip():
            raise KnowledgeCandidateError("knowledge unit requires a title")
        if not isinstance(body, str) or not body.strip():
            raise KnowledgeCandidateError("knowledge unit requires a body")
        if not isinstance(bindings, list) or not bindings:
            raise KnowledgeCandidateError("knowledge unit requires source_bindings")

        normalized_bindings = [
            _normalize_binding(binding, allowed_sources)
            for binding in bindings
            if isinstance(binding, Mapping)
        ]
        if len(normalized_bindings) != len(bindings):
            raise KnowledgeCandidateError("source_bindings must contain mappings")
        normalized: dict[str, Any] = {
            "unit_id": unit_id,
            "kind": kind,
            "title": unit_title.strip(),
            "body": body.strip(),
            "claim": claim,
            "source_bindings": normalized_bindings,
        }

        raw_support = raw_unit.get("support")
        if raw_support is not None:
            if not isinstance(raw_support, list) or not raw_support:
                raise KnowledgeCandidateError("support must be a non-empty list when supplied")
            normalized_support = [
                _normalize_support(
                    evidence,
                    allowed_sources=allowed_sources,
                    bindings=normalized_bindings,
                )
                for evidence in raw_support
                if isinstance(evidence, Mapping)
            ]
            if len(normalized_support) != len(raw_support):
                raise KnowledgeCandidateError("support must contain mappings")
            normalized["support"] = normalized_support

        conflict_keys = raw_unit.get("conflicts_with_claim_keys")
        if conflict_keys is not None:
            if not isinstance(conflict_keys, list) or any(
                not isinstance(other, str) or not other for other in conflict_keys
            ):
                raise KnowledgeCandidateError(
                    "conflicts_with_claim_keys must contain claim keys"
                )
            normalized["conflicts_with_claim_keys"] = list(dict.fromkeys(conflict_keys))

        seen_ids.add(unit_id)
        units.append(normalized)

    claim_to_unit = {unit["claim"]["claim_key"]: unit["unit_id"] for unit in units}
    if len(claim_to_unit) != len(units):
        raise KnowledgeCandidateError("duplicate claim identity")
    for unit in units:
        conflict_keys = unit.pop("conflicts_with_claim_keys", [])
        if conflict_keys:
            conflict_ids: list[str] = []
            for other_key in conflict_keys:
                other_id = claim_to_unit.get(other_key)
                if other_id is None or other_id == unit["unit_id"]:
                    raise KnowledgeCandidateError(
                        "conflicts_with_claim_keys must reference another emitted claim"
                    )
                if other_id not in conflict_ids:
                    conflict_ids.append(other_id)
            unit["conflicts_with"] = conflict_ids

    return {
        "schema_version": KNOWLEDGE_SCHEMA_VERSION,
        "state": CANDIDATE_STATE,
        "authority": AUTHORITY,
        "knowledge_id": knowledge_id,
        "skill": {"slug": skill_slug, "title": title.strip(), "purpose": purpose},
        "compiler": {
            "contract_version": compiler_result.get("contract_version"),
            "adapter_id": adapter_id.strip(),
            **compiler_profile,
        },
        "source_ids": list(dict.fromkeys(source_ids)),
        "units": units,
    }
