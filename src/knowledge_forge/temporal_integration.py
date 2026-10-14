"""Runtime temporal integration for governed knowledge.

P4E composes the P4C load-time trust wall with the existing P2E-C Time layer.

World time is supplied explicitly by the caller and is authoritative for
temporal applicability. An agent-local clock may be supplied for diagnostics,
but it cannot make stale or not-yet-valid knowledge current.

Optional source re-verification evidence is kept separate from claim validity.
A source proven CHANGED or UNAVAILABLE blocks current use. Missing
re-verification never becomes a false "verified current" assertion.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .claim_index import claim_index_artifact_sha256
from .staleness import (
    CURRENT,
    StalenessError,
    evaluate_claim_validity,
)
from .trust_wall import (
    AUTHORITY,
    EXECUTION_AUTHORITY,
    INSTRUCTION_AUTHORITY,
    TRUST_CLASS,
    TrustWallError,
    load_promoted_skill_context,
)

TEMPORAL_INTEGRATION_SCHEMA_VERSION = "0.1"

SOURCE_UNCHANGED = "UNCHANGED"
SOURCE_CHANGED = "CHANGED"
SOURCE_UNAVAILABLE = "UNAVAILABLE"
SOURCE_NOT_REVERIFIED = "NOT_REVERIFIED"
SOURCE_STATUSES = frozenset({
    SOURCE_UNCHANGED,
    SOURCE_CHANGED,
    SOURCE_UNAVAILABLE,
})

CLOCK_MATCH = "MATCH"
CLOCK_AGENT_BEHIND = "AGENT_BEHIND"
CLOCK_AGENT_AHEAD = "AGENT_AHEAD"
CLOCK_AGENT_UNAVAILABLE = "AGENT_UNAVAILABLE"


class TemporalIntegrationError(ValueError):
    """Raised when knowledge is not safe to present as current truth."""


def _parse_time(value: Any, name: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.strip():
        raise TemporalIntegrationError(
            f"{name} must be a non-empty timezone-aware RFC3339 timestamp"
        )
    raw = value.strip()
    if "T" not in raw:
        raise TemporalIntegrationError(
            f"{name} must be a timezone-aware RFC3339 timestamp"
        )
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise TemporalIntegrationError(
            f"{name} must be a timezone-aware RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TemporalIntegrationError(
            f"{name} must be timezone-aware"
        )
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _clock_evidence(
    *,
    world_as_of: str,
    agent_as_of: str | None,
) -> dict[str, Any]:
    world_dt, world = _parse_time(world_as_of, "world_as_of")
    if agent_as_of is None:
        return {
            "world_as_of": world,
            "agent_as_of": None,
            "clock_relation": CLOCK_AGENT_UNAVAILABLE,
            "agent_offset_seconds": None,
            "authority": "WORLD_CLOCK",
        }

    agent_dt, agent = _parse_time(agent_as_of, "agent_as_of")
    offset = (agent_dt - world_dt).total_seconds()
    if offset == 0:
        relation = CLOCK_MATCH
    elif offset < 0:
        relation = CLOCK_AGENT_BEHIND
    else:
        relation = CLOCK_AGENT_AHEAD
    return {
        "world_as_of": world,
        "agent_as_of": agent,
        "clock_relation": relation,
        "agent_offset_seconds": offset,
        "authority": "WORLD_CLOCK",
    }


def _current_registry_claim_index(
    registry: Mapping[str, Any],
    load_result: Mapping[str, Any],
) -> Mapping[str, Any]:
    evidence = load_result.get("load_evidence")
    if not isinstance(evidence, Mapping):
        raise TemporalIntegrationError(
            "trust-wall load_evidence is required"
        )
    skill_id = evidence.get("skill_id")
    version = evidence.get("skill_version")
    if not isinstance(skill_id, str) or not skill_id:
        raise TemporalIntegrationError(
            "load_evidence skill_id is invalid"
        )
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise TemporalIntegrationError(
            "load_evidence skill_version is invalid"
        )

    skills = registry.get("skills")
    record = skills.get(skill_id) if isinstance(skills, Mapping) else None
    if not isinstance(record, Mapping):
        raise TemporalIntegrationError(
            "loaded skill is absent from registry snapshot"
        )
    if record.get("current_version") != version:
        raise TemporalIntegrationError(
            "loaded skill is no longer registry current version"
        )
    versions = record.get("versions")
    entry = versions.get(str(version)) if isinstance(versions, Mapping) else None
    if not isinstance(entry, Mapping):
        raise TemporalIntegrationError(
            "loaded skill version is absent from registry snapshot"
        )
    claim_index = entry.get("claim_index")
    if not isinstance(claim_index, Mapping):
        raise TemporalIntegrationError(
            "registry entry requires claim_index"
        )
    try:
        digest = claim_index_artifact_sha256(claim_index)
    except Exception as exc:
        raise TemporalIntegrationError(
            f"registry claim index cannot be hashed: {exc}"
        ) from exc
    if digest != evidence.get("claim_index_sha256"):
        raise TemporalIntegrationError(
            "registry claim index no longer matches trust-wall load"
        )
    return claim_index


def _source_check(
    source_id: str,
    *,
    world_dt: datetime,
    source_reverification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if source_reverification is None or source_id not in source_reverification:
        return {
            "source_id": source_id,
            "status": SOURCE_NOT_REVERIFIED,
            "checked_at": None,
            "evidence_ref": None,
            "blocks_current_use": False,
        }

    raw = source_reverification[source_id]
    if not isinstance(raw, Mapping):
        raise TemporalIntegrationError(
            f"source re-verification must be a mapping: {source_id}"
        )
    allowed = {"status", "checked_at", "evidence_ref"}
    unknown = set(raw) - allowed
    if unknown:
        raise TemporalIntegrationError(
            "source re-verification contains unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    status = raw.get("status")
    if status not in SOURCE_STATUSES:
        raise TemporalIntegrationError(
            f"source re-verification status invalid: {source_id}"
        )
    checked_dt, checked_at = _parse_time(
        raw.get("checked_at"),
        f"source_reverification[{source_id}].checked_at",
    )
    if checked_dt > world_dt:
        raise TemporalIntegrationError(
            "source re-verification checked_at cannot be after world_as_of"
        )
    evidence_ref = raw.get("evidence_ref")
    if evidence_ref is not None and (
        not isinstance(evidence_ref, str)
        or not evidence_ref.strip()
    ):
        raise TemporalIntegrationError(
            "source re-verification evidence_ref must be non-empty when present"
        )

    return {
        "source_id": source_id,
        "status": status,
        "checked_at": checked_at,
        "evidence_ref": (
            evidence_ref.strip()
            if isinstance(evidence_ref, str)
            else None
        ),
        "blocks_current_use": status in {
            SOURCE_CHANGED,
            SOURCE_UNAVAILABLE,
        },
    }


def evaluate_loaded_temporal_truth(
    load_result: Mapping[str, Any],
    registry: Mapping[str, Any],
    *,
    world_as_of: str,
    agent_as_of: str | None = None,
    source_reverification: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate only units actually loaded by P4C against trusted world time."""

    if not isinstance(load_result, Mapping):
        raise TemporalIntegrationError(
            "trust-wall load result must be a mapping"
        )
    if load_result.get("trust_class") != TRUST_CLASS:
        raise TemporalIntegrationError(
            "temporal integration requires P4C untrusted knowledge data"
        )
    if load_result.get("authority") != AUTHORITY:
        raise TemporalIntegrationError(
            "temporal integration requires KNOWLEDGE_ONLY authority"
        )
    if load_result.get("instruction_authority") != INSTRUCTION_AUTHORITY:
        raise TemporalIntegrationError(
            "temporal integration requires zero instruction authority"
        )
    if load_result.get("execution_authority") != EXECUTION_AUTHORITY:
        raise TemporalIntegrationError(
            "temporal integration requires zero execution authority"
        )

    clock = _clock_evidence(
        world_as_of=world_as_of,
        agent_as_of=agent_as_of,
    )
    world_dt, _ = _parse_time(clock["world_as_of"], "world_as_of")
    claim_index = _current_registry_claim_index(
        registry,
        load_result,
    )

    claims = claim_index.get("claims")
    if not isinstance(claims, list):
        raise TemporalIntegrationError(
            "registry claim index requires claims"
        )
    claims_by_id = {
        item.get("unit_id"): item
        for item in claims
        if isinstance(item, Mapping)
    }

    evidence = load_result["load_evidence"]
    loaded_ids = evidence.get("loaded_unit_ids")
    if (
        not isinstance(loaded_ids, list)
        or not loaded_ids
        or any(not isinstance(item, str) or not item for item in loaded_ids)
    ):
        raise TemporalIntegrationError(
            "trust-wall load requires loaded_unit_ids"
        )

    context = load_result.get("reasoning_context")
    if not isinstance(context, list):
        raise TemporalIntegrationError(
            "trust-wall reasoning_context must be a list"
        )
    context_ids = [
        item.get("unit_id")
        for item in context
        if isinstance(item, Mapping)
    ]
    if context_ids != loaded_ids:
        raise TemporalIntegrationError(
            "reasoning context does not match loaded unit evidence"
        )
    for item in context:
        if not isinstance(item, Mapping):
            raise TemporalIntegrationError(
                "reasoning context entry must be a mapping"
            )
        if item.get("trust_class") != TRUST_CLASS:
            raise TemporalIntegrationError(
                "reasoning context trust class mismatch"
            )
        if item.get("instruction_authority") != INSTRUCTION_AUTHORITY:
            raise TemporalIntegrationError(
                "reasoning context instruction authority mismatch"
            )
        if item.get("execution_authority") != EXECUTION_AUTHORITY:
            raise TemporalIntegrationError(
                "reasoning context execution authority mismatch"
            )
        if item.get("authority") != AUTHORITY:
            raise TemporalIntegrationError(
                "reasoning context authority mismatch"
            )
        if item.get("executable") is not False:
            raise TemporalIntegrationError(
                "reasoning context must remain non-executable"
            )

    unit_results: list[dict[str, Any]] = []
    temporal_blockers: list[str] = []
    source_blockers: list[dict[str, str]] = []
    all_source_ids: set[str] = set()
    verified_unchanged_ids: set[str] = set()

    for unit_id in loaded_ids:
        claim_item = claims_by_id.get(unit_id)
        if not isinstance(claim_item, Mapping):
            raise TemporalIntegrationError(
                f"loaded unit missing from current claim index: {unit_id}"
            )
        claim = claim_item.get("claim")
        if not isinstance(claim, Mapping):
            raise TemporalIntegrationError(
                f"loaded unit requires structured claim: {unit_id}"
            )
        try:
            temporal = evaluate_claim_validity(
                claim,
                as_of=clock["world_as_of"],
            )
        except StalenessError as exc:
            raise TemporalIntegrationError(
                f"temporal evaluation failed: {exc}"
            ) from exc

        if temporal["condition"] != CURRENT:
            temporal_blockers.append(
                f"{unit_id}:{temporal['condition']}"
            )

        bindings = claim_item.get("source_bindings")
        if not isinstance(bindings, list) or not bindings:
            raise TemporalIntegrationError(
                f"loaded unit requires source bindings: {unit_id}"
            )
        source_checks: list[dict[str, Any]] = []
        for binding in bindings:
            if not isinstance(binding, Mapping):
                raise TemporalIntegrationError(
                    f"source binding invalid: {unit_id}"
                )
            source_id = binding.get("source_id")
            if not isinstance(source_id, str) or not source_id:
                raise TemporalIntegrationError(
                    f"source binding source_id invalid: {unit_id}"
                )
            all_source_ids.add(source_id)
            check = _source_check(
                source_id,
                world_dt=world_dt,
                source_reverification=source_reverification,
            )
            source_checks.append(check)
            if check["status"] == SOURCE_UNCHANGED:
                verified_unchanged_ids.add(source_id)
            if check["blocks_current_use"]:
                source_blockers.append({
                    "source_id": source_id,
                    "status": check["status"],
                })

        source_checks.sort(key=lambda item: item["source_id"])
        unit_results.append({
            "unit_id": unit_id,
            "claim_key": claim_item.get("claim_key"),
            "claim_sha256": claim_item.get("claim_sha256"),
            "temporal": temporal,
            "source_reverification": source_checks,
        })

    return {
        "schema_version": TEMPORAL_INTEGRATION_SCHEMA_VERSION,
        **clock,
        "conditions": [
            item["temporal"]["condition"]
            for item in unit_results
        ],
        "all_selected_temporally_current": not temporal_blockers,
        "temporal_blockers": temporal_blockers,
        "source_blockers": source_blockers,
        "source_reverification_complete": (
            bool(all_source_ids)
            and all_source_ids == verified_unchanged_ids
        ),
        "current_use_allowed": (
            not temporal_blockers
            and not source_blockers
        ),
        "unit_results": unit_results,
    }


def load_current_skill_context(
    skill_root: str,
    registry: Mapping[str, Any],
    *,
    skill_id: str,
    question: str,
    runtime_tools: Iterable[str],
    world_as_of: str,
    agent_as_of: str | None = None,
    source_reverification: Mapping[str, Any] | None = None,
    skill_requested_tools: Iterable[str] | None = None,
    max_units: int = 1,
) -> dict[str, Any]:
    """Return P4C context only when selected knowledge is current at world time."""

    registry_snapshot = deepcopy(registry)
    try:
        loaded = load_promoted_skill_context(
            skill_root,
            registry_snapshot,
            skill_id=skill_id,
            question=question,
            runtime_tools=runtime_tools,
            skill_requested_tools=skill_requested_tools,
            max_units=max_units,
        )
    except TrustWallError as exc:
        raise TemporalIntegrationError(
            f"trust wall refused load: {exc}"
        ) from exc

    temporal = evaluate_loaded_temporal_truth(
        loaded,
        registry_snapshot,
        world_as_of=world_as_of,
        agent_as_of=agent_as_of,
        source_reverification=source_reverification,
    )

    if temporal["temporal_blockers"]:
        raise TemporalIntegrationError(
            "selected knowledge is not current at world clock: "
            + ", ".join(temporal["temporal_blockers"])
        )
    if temporal["source_blockers"]:
        detail = ", ".join(
            f"{item['source_id']}:{item['status']}"
            for item in temporal["source_blockers"]
        )
        raise TemporalIntegrationError(
            "source re-verification blocks current use: " + detail
        )

    result = deepcopy(loaded)
    temporal_by_unit = {
        item["unit_id"]: item
        for item in temporal["unit_results"]
    }
    for item in result["reasoning_context"]:
        unit = temporal_by_unit[item["unit_id"]]
        item["temporal_condition"] = unit["temporal"]["condition"]
        item["world_as_of"] = temporal["world_as_of"]
        item["source_reverification"] = deepcopy(
            unit["source_reverification"]
        )

    result["temporal_evidence"] = temporal
    result["load_evidence"]["world_as_of"] = temporal["world_as_of"]
    result["load_evidence"]["agent_as_of"] = temporal["agent_as_of"]
    result["load_evidence"]["clock_relation"] = temporal["clock_relation"]
    result["load_evidence"]["temporal_conditions_by_unit"] = {
        item["unit_id"]: item["temporal"]["condition"]
        for item in temporal["unit_results"]
    }
    result["load_evidence"]["source_reverification_complete"] = (
        temporal["source_reverification_complete"]
    )
    return result
