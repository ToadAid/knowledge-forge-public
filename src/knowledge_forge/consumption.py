"""Governed consumption receipts and retroactive knowledge-taint queries.

P2E-D records which externally identified answers, plans, or decisions loaded
which exact registered knowledge units. Receipts bind package, claim-index,
lifecycle-at-consumption, structured claim digests, and source provenance.

Later registry incidents or explicit Time-layer staleness can be projected back
onto those historical receipts without rewriting the receipts themselves.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from .lifecycle import DISPUTED, PROMOTED, REVOKED, SUSPENDED
from .registry import SkillRegistryError, validate_skill_registry
from .staleness import (
    CURRENT,
    NOT_YET_VALID,
    STALE,
    UNEVALUABLE,
    StalenessError,
    evaluate_claim_index_staleness,
)

CONSUMPTION_RECEIPT_SCHEMA_VERSION = "0.1"
CONSUMPTION_LEDGER_SCHEMA_VERSION = "0.1"
AUTHORITY = "KNOWLEDGE_ONLY"
OUTCOME_KINDS = frozenset({"ANSWER", "PLAN", "DECISION"})
INCIDENT_TAINT_STATES = frozenset({SUSPENDED, DISPUTED, REVOKED})


class ConsumptionError(ValueError):
    """Raised when governed consumption evidence is invalid."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConsumptionError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ConsumptionError(f"{name} must be a positive integer")
    return value


def _sha256_ref(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if not text.startswith("sha256:"):
        raise ConsumptionError(f"{name} must be a sha256 reference")
    digest = text[7:]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ConsumptionError(f"{name} must be a sha256 reference")
    return text


def _canonical_time(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if "T" not in text:
        raise ConsumptionError(f"{name} must be a timezone-aware RFC3339 timestamp")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ConsumptionError(
            f"{name} must be a timezone-aware RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ConsumptionError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ConsumptionError("consumption evidence must be canonical JSON data") from exc


def _consumption_id(payload: Mapping[str, Any]) -> str:
    return f"consumption:{sha256(_json_bytes(payload)).hexdigest()}"


def _entry(
    registry: Mapping[str, Any],
    skill_id: str,
    skill_version: int,
) -> Mapping[str, Any]:
    skills = registry.get("skills")
    if not isinstance(skills, Mapping):
        raise ConsumptionError("registry skills must be a mapping")
    record = skills.get(skill_id)
    if not isinstance(record, Mapping):
        raise ConsumptionError(f"skill is not registered: {skill_id}")
    versions = record.get("versions")
    if not isinstance(versions, Mapping):
        raise ConsumptionError(f"registry versions missing: {skill_id}")
    entry = versions.get(str(skill_version))
    if not isinstance(entry, Mapping):
        raise ConsumptionError(
            f"skill version is not registered: {skill_id}@{skill_version}"
        )
    return entry


def _validate_registry(registry: Mapping[str, Any]) -> None:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise ConsumptionError(
            "registry is invalid: " + "; ".join(report["errors"])
        )


def _claim_map(entry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    index = entry.get("claim_index")
    claims = index.get("claims") if isinstance(index, Mapping) else None
    if not isinstance(claims, list) or not claims:
        raise ConsumptionError("registry entry requires claim_index claims")
    result: dict[str, Mapping[str, Any]] = {}
    for item in claims:
        if not isinstance(item, Mapping):
            raise ConsumptionError("claim_index claim must be a mapping")
        unit_id = _nonempty(item.get("unit_id"), "claim_index unit_id")
        if unit_id in result:
            raise ConsumptionError("claim_index unit_ids must be unique")
        result[unit_id] = item
    return result


def create_consumption_receipt(
    registry: Mapping[str, Any],
    *,
    skill_id: str,
    loaded_unit_ids: list[str],
    outcome_kind: str,
    outcome_ref: str,
    consumed_at: str,
) -> dict[str, Any]:
    """Bind one output to exact current PROMOTED knowledge dependencies."""

    _validate_registry(registry)
    skill_key = _nonempty(skill_id, "skill_id")
    kind = _nonempty(outcome_kind, "outcome_kind")
    if kind not in OUTCOME_KINDS:
        raise ConsumptionError(
            "outcome_kind must be ANSWER, PLAN, or DECISION"
        )
    output_ref = _nonempty(outcome_ref, "outcome_ref")
    timestamp = _canonical_time(consumed_at, "consumed_at")

    record = registry["skills"].get(skill_key)
    if not isinstance(record, Mapping):
        raise ConsumptionError(f"skill is not registered: {skill_key}")
    version = _positive_int(record.get("current_version"), "current_version")
    entry = _entry(registry, skill_key, version)
    if entry.get("state") != PROMOTED:
        raise ConsumptionError(
            f"current skill is not consumable: {skill_key}@{version} "
            f"state={entry.get('state')}"
        )
    if entry.get("authority") != AUTHORITY:
        raise ConsumptionError("consumption accepts KNOWLEDGE_ONLY entries only")

    if (
        not isinstance(loaded_unit_ids, list)
        or not loaded_unit_ids
        or any(not isinstance(item, str) or not item for item in loaded_unit_ids)
    ):
        raise ConsumptionError("loaded_unit_ids must be a non-empty list")
    if len(set(loaded_unit_ids)) != len(loaded_unit_ids):
        raise ConsumptionError("loaded_unit_ids must be unique")

    claims = _claim_map(entry)
    loaded_units: list[dict[str, Any]] = []
    for unit_id in loaded_unit_ids:
        claim = claims.get(unit_id)
        if claim is None:
            raise ConsumptionError(
                f"loaded unit is not in registered claim_index: {unit_id}"
            )
        loaded_units.append(
            {
                "claim_ref": _nonempty(claim.get("claim_ref"), "claim_ref"),
                "unit_id": unit_id,
                "claim_key": _nonempty(claim.get("claim_key"), "claim_key"),
                "claim_sha256": _sha256_ref(
                    claim.get("claim_sha256"),
                    "claim_sha256",
                ),
                "source_bindings": deepcopy(claim.get("source_bindings")),
            }
        )
    loaded_units.sort(key=lambda item: item["claim_ref"])

    payload = {
        "schema_version": CONSUMPTION_RECEIPT_SCHEMA_VERSION,
        "consumed_at": timestamp,
        "outcome_kind": kind,
        "outcome_ref": output_ref,
        "skill_id": skill_key,
        "skill_version": version,
        "knowledge_id": _nonempty(entry.get("knowledge_id"), "knowledge_id"),
        "package_receipt_sha256": _sha256_ref(
            entry.get("package_receipt_sha256"),
            "package_receipt_sha256",
        ),
        "claim_index_sha256": _sha256_ref(
            entry.get("claim_index_sha256"),
            "claim_index_sha256",
        ),
        "lifecycle_head_sha256": _sha256_ref(
            entry.get("lifecycle_head_sha256"),
            "lifecycle_head_sha256",
        ),
        "loaded_units": loaded_units,
        "authority": AUTHORITY,
    }
    return {
        **payload,
        "consumption_id": _consumption_id(payload),
    }


def validate_consumption_receipt(
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(receipt, Mapping):
        return {
            "valid": False,
            "errors": ["consumption receipt must be a mapping"],
        }

    expected = {
        "schema_version",
        "consumption_id",
        "consumed_at",
        "outcome_kind",
        "outcome_ref",
        "skill_id",
        "skill_version",
        "knowledge_id",
        "package_receipt_sha256",
        "claim_index_sha256",
        "lifecycle_head_sha256",
        "loaded_units",
        "authority",
    }
    if set(receipt) != expected:
        errors.append("consumption receipt fields must match exact contract")
    if receipt.get("schema_version") != CONSUMPTION_RECEIPT_SCHEMA_VERSION:
        errors.append("consumption receipt schema_version mismatch")
    if receipt.get("authority") != AUTHORITY:
        errors.append("consumption receipt authority must remain KNOWLEDGE_ONLY")

    try:
        timestamp = _canonical_time(receipt.get("consumed_at"), "consumed_at")
        if timestamp != receipt.get("consumed_at"):
            errors.append("consumed_at must use canonical UTC RFC3339")
        _nonempty(receipt.get("outcome_ref"), "outcome_ref")
        _nonempty(receipt.get("skill_id"), "skill_id")
        _positive_int(receipt.get("skill_version"), "skill_version")
        _nonempty(receipt.get("knowledge_id"), "knowledge_id")
        for field in (
            "package_receipt_sha256",
            "claim_index_sha256",
            "lifecycle_head_sha256",
        ):
            _sha256_ref(receipt.get(field), field)
    except ConsumptionError as exc:
        errors.append(str(exc))

    if receipt.get("outcome_kind") not in OUTCOME_KINDS:
        errors.append("outcome_kind must be ANSWER, PLAN, or DECISION")

    loaded = receipt.get("loaded_units")
    normalized_loaded: list[dict[str, Any]] = []
    if not isinstance(loaded, list) or not loaded:
        errors.append("consumption receipt requires loaded_units")
    else:
        refs: list[str] = []
        unit_ids: list[str] = []
        for item in loaded:
            if not isinstance(item, Mapping):
                errors.append("loaded unit must be a mapping")
                continue
            expected_unit = {
                "claim_ref",
                "unit_id",
                "claim_key",
                "claim_sha256",
                "source_bindings",
            }
            if set(item) != expected_unit:
                errors.append("loaded unit fields must match exact contract")
                continue
            try:
                normalized = {
                    "claim_ref": _nonempty(item.get("claim_ref"), "claim_ref"),
                    "unit_id": _nonempty(item.get("unit_id"), "unit_id"),
                    "claim_key": _nonempty(item.get("claim_key"), "claim_key"),
                    "claim_sha256": _sha256_ref(
                        item.get("claim_sha256"),
                        "claim_sha256",
                    ),
                    "source_bindings": deepcopy(item.get("source_bindings")),
                }
            except ConsumptionError as exc:
                errors.append(str(exc))
                continue
            if not isinstance(normalized["source_bindings"], list) or not normalized[
                "source_bindings"
            ]:
                errors.append("loaded unit requires source_bindings")
            refs.append(normalized["claim_ref"])
            unit_ids.append(normalized["unit_id"])
            normalized_loaded.append(normalized)
        if len(set(refs)) != len(refs):
            errors.append("loaded claim_refs must be unique")
        if len(set(unit_ids)) != len(unit_ids):
            errors.append("loaded unit_ids must be unique")
        if refs != sorted(refs):
            errors.append("loaded_units must be sorted by claim_ref")

    if not errors:
        payload = {
            key: deepcopy(value)
            for key, value in receipt.items()
            if key != "consumption_id"
        }
        expected_id = _consumption_id(payload)
        if receipt.get("consumption_id") != expected_id:
            errors.append("consumption_id mismatch")

    return {"valid": not errors, "errors": errors}


def create_consumption_ledger(*, created_at: str) -> dict[str, Any]:
    return {
        "schema_version": CONSUMPTION_LEDGER_SCHEMA_VERSION,
        "created_at": _canonical_time(created_at, "created_at"),
        "receipts": [],
    }


def validate_consumption_ledger(
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(ledger, Mapping):
        return {"valid": False, "errors": ["consumption ledger must be a mapping"]}
    if set(ledger) != {"schema_version", "created_at", "receipts"}:
        errors.append("consumption ledger fields must match exact contract")
    if ledger.get("schema_version") != CONSUMPTION_LEDGER_SCHEMA_VERSION:
        errors.append("consumption ledger schema_version mismatch")
    try:
        created = _canonical_time(ledger.get("created_at"), "created_at")
        if created != ledger.get("created_at"):
            errors.append("created_at must use canonical UTC RFC3339")
    except ConsumptionError as exc:
        errors.append(str(exc))

    receipts = ledger.get("receipts")
    ids: list[str] = []
    if not isinstance(receipts, list):
        errors.append("consumption ledger receipts must be a list")
    else:
        for receipt in receipts:
            report = validate_consumption_receipt(receipt)
            if not report["valid"]:
                errors.append(
                    "invalid consumption receipt: " + "; ".join(report["errors"])
                )
                continue
            ids.append(receipt["consumption_id"])
        if len(set(ids)) != len(ids):
            errors.append("consumption ledger receipt ids must be unique")

    return {"valid": not errors, "errors": errors}


def append_consumption_receipt(
    ledger: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    ledger_report = validate_consumption_ledger(ledger)
    if not ledger_report["valid"]:
        raise ConsumptionError(
            "cannot append to invalid consumption ledger: "
            + "; ".join(ledger_report["errors"])
        )
    receipt_report = validate_consumption_receipt(receipt)
    if not receipt_report["valid"]:
        raise ConsumptionError(
            "cannot append invalid consumption receipt: "
            + "; ".join(receipt_report["errors"])
        )
    updated = deepcopy(dict(ledger))
    if any(
        item["consumption_id"] == receipt["consumption_id"]
        for item in updated["receipts"]
    ):
        raise ConsumptionError("consumption receipt already exists")
    updated["receipts"].append(deepcopy(dict(receipt)))
    return updated


def save_consumption_ledger(
    ledger: Mapping[str, Any],
    path: str | Path,
) -> None:
    report = validate_consumption_ledger(ledger)
    if not report["valid"]:
        raise ConsumptionError(
            "cannot save invalid consumption ledger: "
            + "; ".join(report["errors"])
        )
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            ledger,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_consumption_ledger(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConsumptionError("consumption ledger is not readable JSON") from exc
    if not isinstance(value, dict):
        raise ConsumptionError("consumption ledger must be a JSON object")
    report = validate_consumption_ledger(value)
    if not report["valid"]:
        raise ConsumptionError(
            "consumption ledger invalid: " + "; ".join(report["errors"])
        )
    return value


def query_consumptions(
    ledger: Mapping[str, Any],
    *,
    skill_id: str | None = None,
    skill_version: int | None = None,
    unit_id: str | None = None,
    outcome_kind: str | None = None,
) -> list[dict[str, Any]]:
    report = validate_consumption_ledger(ledger)
    if not report["valid"]:
        raise ConsumptionError(
            "consumption ledger invalid: " + "; ".join(report["errors"])
        )
    if outcome_kind is not None and outcome_kind not in OUTCOME_KINDS:
        raise ConsumptionError("outcome_kind must be ANSWER, PLAN, or DECISION")

    matches: list[dict[str, Any]] = []
    for receipt in ledger["receipts"]:
        if skill_id is not None and receipt["skill_id"] != skill_id:
            continue
        if skill_version is not None and receipt["skill_version"] != skill_version:
            continue
        if outcome_kind is not None and receipt["outcome_kind"] != outcome_kind:
            continue
        if unit_id is not None and all(
            item["unit_id"] != unit_id for item in receipt["loaded_units"]
        ):
            continue
        matches.append(deepcopy(receipt))
    return matches


def evaluate_registered_knowledge_taint(
    registry: Mapping[str, Any],
    *,
    skill_id: str,
    skill_version: int,
    as_of: str,
    unit_id: str | None = None,
) -> dict[str, Any]:
    _validate_registry(registry)
    skill_key = _nonempty(skill_id, "skill_id")
    version = _positive_int(skill_version, "skill_version")
    entry = _entry(registry, skill_key, version)

    try:
        temporal = evaluate_claim_index_staleness(
            entry["claim_index"],
            as_of=as_of,
        )
    except (KeyError, StalenessError) as exc:
        raise ConsumptionError(f"temporal taint evaluation failed: {exc}") from exc

    if unit_id is None:
        temporal_condition = temporal["condition"]
        temporally_applicable = temporal["temporally_applicable"]
        target = {
            "kind": "PACKAGE",
            "skill_id": skill_key,
            "skill_version": version,
        }
    else:
        unit_key = _nonempty(unit_id, "unit_id")
        unit_result = next(
            (
                item
                for item in temporal["unit_results"]
                if item["unit_id"] == unit_key
            ),
            None,
        )
        if unit_result is None:
            raise ConsumptionError(
                f"unit is not in registered claim_index: {unit_key}"
            )
        temporal_condition = unit_result["condition"]
        temporally_applicable = unit_result["temporally_applicable"]
        target = {
            "kind": "UNIT",
            "skill_id": skill_key,
            "skill_version": version,
            "unit_id": unit_key,
        }

    state = entry.get("state")
    reasons: list[str] = []
    if state in INCIDENT_TAINT_STATES:
        reasons.append(f"LIFECYCLE_{state}")
    if temporal_condition == STALE:
        reasons.append("TEMPORAL_STALE")
    elif temporal_condition == NOT_YET_VALID:
        reasons.append("TEMPORAL_NOT_YET_VALID")
    elif temporal_condition == UNEVALUABLE:
        reasons.append("TEMPORAL_UNEVALUABLE")

    return {
        "target": target,
        "lifecycle_state": state,
        "temporal_condition": temporal_condition,
        "temporally_applicable": temporally_applicable,
        "tainted": bool(reasons),
        "taint_reasons": reasons,
        "as_of": temporal["as_of"],
        "claim_index_sha256": temporal["claim_index_sha256"],
    }


def _bind_receipt_to_registry(
    registry: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> Mapping[str, Any]:
    report = validate_consumption_receipt(receipt)
    if not report["valid"]:
        raise ConsumptionError(
            "consumption receipt invalid: " + "; ".join(report["errors"])
        )

    entry = _entry(
        registry,
        receipt["skill_id"],
        receipt["skill_version"],
    )
    for field in (
        "knowledge_id",
        "package_receipt_sha256",
        "claim_index_sha256",
    ):
        if receipt[field] != entry.get(field):
            raise ConsumptionError(
                f"consumption receipt {field} no longer binds registry entry"
            )

    lifecycle = entry.get("lifecycle_record")
    events = lifecycle.get("events") if isinstance(lifecycle, Mapping) else None
    matching_event = None
    if isinstance(events, list):
        matching_event = next(
            (
                event
                for event in events
                if isinstance(event, Mapping)
                and event.get("event_sha256") == receipt["lifecycle_head_sha256"]
            ),
            None,
        )
    if matching_event is None:
        raise ConsumptionError(
            "consumption lifecycle head is not preserved in registry history"
        )
    if matching_event.get("to_state") != PROMOTED:
        raise ConsumptionError(
            "consumption lifecycle head was not PROMOTED"
        )

    claims = _claim_map(entry)
    for loaded in receipt["loaded_units"]:
        current = claims.get(loaded["unit_id"])
        if current is None:
            raise ConsumptionError(
                f"consumed unit missing from registry claim_index: {loaded['unit_id']}"
            )
        for field in (
            "claim_ref",
            "claim_key",
            "claim_sha256",
            "source_bindings",
        ):
            if loaded[field] != current.get(field):
                raise ConsumptionError(
                    f"consumed unit {field} no longer binds registry claim"
                )
    return entry


def assess_consumption_taint(
    registry: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    _validate_registry(registry)
    entry = _bind_receipt_to_registry(registry, receipt)

    package_taint = evaluate_registered_knowledge_taint(
        registry,
        skill_id=receipt["skill_id"],
        skill_version=receipt["skill_version"],
        as_of=as_of,
    )
    loaded_unit_taints = [
        evaluate_registered_knowledge_taint(
            registry,
            skill_id=receipt["skill_id"],
            skill_version=receipt["skill_version"],
            unit_id=item["unit_id"],
            as_of=as_of,
        )
        for item in receipt["loaded_units"]
    ]

    lifecycle_tainted = entry.get("state") in INCIDENT_TAINT_STATES
    temporal_loaded_tainted = any(
        item["temporal_condition"] != CURRENT
        for item in loaded_unit_taints
    )

    # Output-level taint reasons reflect only dependencies the output actually
    # consumed. Package temporal condition remains visible below as context,
    # but a stale/unready unit that was never loaded cannot taint this output.
    reasons: list[str] = []
    if lifecycle_tainted:
        reasons.append(f"LIFECYCLE_{entry.get('state')}")
    for item in loaded_unit_taints:
        for reason in item["taint_reasons"]:
            if reason.startswith("TEMPORAL_") and reason not in reasons:
                reasons.append(reason)

    return {
        "consumption_id": receipt["consumption_id"],
        "outcome_kind": receipt["outcome_kind"],
        "outcome_ref": receipt["outcome_ref"],
        "skill_id": receipt["skill_id"],
        "skill_version": receipt["skill_version"],
        "tainted": lifecycle_tainted or temporal_loaded_tainted,
        "taint_reasons": reasons,
        "lifecycle_state": entry.get("state"),
        "package_temporal_condition": package_taint["temporal_condition"],
        "loaded_unit_taints": loaded_unit_taints,
        "as_of": package_taint["as_of"],
    }


def query_tainted_consumptions(
    registry: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    as_of: str,
) -> list[dict[str, Any]]:
    _validate_registry(registry)
    report = validate_consumption_ledger(ledger)
    if not report["valid"]:
        raise ConsumptionError(
            "consumption ledger invalid: " + "; ".join(report["errors"])
        )

    tainted: list[dict[str, Any]] = []
    for receipt in ledger["receipts"]:
        assessment = assess_consumption_taint(
            registry,
            receipt,
            as_of=as_of,
        )
        if assessment["tainted"]:
            tainted.append(assessment)
    return tainted
