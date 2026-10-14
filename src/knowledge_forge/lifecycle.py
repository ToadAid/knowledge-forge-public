"""Governed, append-only skill lifecycle state transitions.

This module records lifecycle decisions and evidence. Actor identifiers and
decision references are provenance labels; authorization remains external to
Knowledge Forge and is never inferred from a string in a lifecycle receipt.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping

LIFECYCLE_SCHEMA_VERSION = "0.2"
COMPILED_CANDIDATE = "COMPILED_CANDIDATE"
VALIDATED = "VALIDATED"
PROMOTED = "PROMOTED"
SUPERSEDED = "SUPERSEDED"
SUSPENDED = "SUSPENDED"
DISPUTED = "DISPUTED"
REVOKED = "REVOKED"
INCIDENT_STATES = frozenset({SUSPENDED, DISPUTED, REVOKED})
STATES = frozenset({
    COMPILED_CANDIDATE,
    VALIDATED,
    PROMOTED,
    SUPERSEDED,
    SUSPENDED,
    DISPUTED,
    REVOKED,
})

_ALLOWED = {
    COMPILED_CANDIDATE: frozenset({VALIDATED}),
    VALIDATED: frozenset({PROMOTED}),
    PROMOTED: frozenset({SUPERSEDED, SUSPENDED, DISPUTED, REVOKED}),
    SUSPENDED: frozenset({PROMOTED, DISPUTED, REVOKED, SUPERSEDED}),
    DISPUTED: frozenset({PROMOTED, SUSPENDED, REVOKED, SUPERSEDED}),
    SUPERSEDED: frozenset({REVOKED}),
    REVOKED: frozenset(),
}


class LifecycleError(ValueError):
    """Raised when lifecycle truth is incomplete, invalid, or attempts a forbidden transition."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _digest(value: Mapping[str, Any]) -> str:
    return f"sha256:{sha256(_canonical_bytes(value)).hexdigest()}"


def evidence_sha256(value: Mapping[str, Any]) -> str:
    if not isinstance(value, Mapping):
        raise LifecycleError("evidence value must be a mapping")
    return _digest(dict(value))


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LifecycleError(f"{name} must be a non-empty string")
    return value.strip()


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(ch in "0123456789abcdef" for ch in value)
    )


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LifecycleError(f"{name} must be a positive integer")
    return value


def _package_binding(manifest: Mapping[str, Any], source_receipt: Mapping[str, Any]) -> dict[str, Any]:
    skill_id = _nonempty(manifest.get("skill_id"), "manifest skill_id")
    skill_version = _positive_int(manifest.get("skill_version"), "manifest skill_version")
    if manifest.get("state") != COMPILED_CANDIDATE:
        raise LifecycleError("lifecycle must begin from a COMPILED_CANDIDATE package")
    if manifest.get("authority") != "KNOWLEDGE_ONLY":
        raise LifecycleError("lifecycle package must remain KNOWLEDGE_ONLY")
    if source_receipt.get("skill_id") != skill_id:
        raise LifecycleError("source receipt skill_id does not match manifest")
    if source_receipt.get("skill_version") != skill_version:
        raise LifecycleError("source receipt skill_version does not match manifest")
    knowledge_id = _nonempty(manifest.get("knowledge_id"), "manifest knowledge_id")
    source_ids = manifest.get("source_ids")
    candidate_digest = _nonempty(manifest.get("candidate_sha256"), "manifest candidate_sha256")
    manifest_units = manifest.get("units")
    if not isinstance(source_ids, list) or not source_ids or any(not isinstance(item, str) for item in source_ids):
        raise LifecycleError("manifest source_ids must be a non-empty string list")
    if not isinstance(manifest_units, list) or not manifest_units:
        raise LifecycleError("manifest units must be a non-empty list")
    unit_ids: list[str] = []
    for unit in manifest_units:
        if not isinstance(unit, Mapping):
            raise LifecycleError("manifest unit must be a mapping")
        unit_id = _nonempty(unit.get("unit_id"), "manifest unit_id")
        if unit_id in unit_ids:
            raise LifecycleError("manifest unit_ids must be unique")
        unit_ids.append(unit_id)
    if source_receipt.get("knowledge_id") != knowledge_id:
        raise LifecycleError("source receipt knowledge_id does not match manifest")
    if source_receipt.get("source_ids") != source_ids:
        raise LifecycleError("source receipt source_ids do not match manifest")
    if source_receipt.get("candidate_sha256") != candidate_digest:
        raise LifecycleError("source receipt candidate_sha256 does not match manifest")
    artifact_hashes = source_receipt.get("artifact_hashes")
    if not isinstance(artifact_hashes, Mapping) or not artifact_hashes:
        raise LifecycleError("source receipt requires artifact_hashes")
    return {
        "skill_id": skill_id,
        "skill_version": skill_version,
        "knowledge_id": knowledge_id,
        "source_ids": list(source_ids),
        "unit_ids": unit_ids,
        "candidate_sha256": candidate_digest,
        "package_receipt_sha256": f"sha256:{sha256(_canonical_bytes(dict(source_receipt))).hexdigest()}",
    }


def create_lifecycle_record(
    manifest: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    *,
    created_at: str,
) -> dict[str, Any]:
    """Bind an immutable skill package to its governed lifecycle history."""

    binding = _package_binding(manifest, source_receipt)
    timestamp = _nonempty(created_at, "created_at")
    genesis = {
        "sequence": 0,
        "from_state": None,
        "to_state": COMPILED_CANDIDATE,
        "occurred_at": timestamp,
        "actor": "knowledge-forge",
        "evidence": deepcopy(binding),
        "previous_event_sha256": None,
    }
    genesis["event_sha256"] = _digest(genesis)
    return {
        "schema_version": LIFECYCLE_SCHEMA_VERSION,
        **binding,
        "state": COMPILED_CANDIDATE,
        "events": [genesis],
    }


def _validate_evidence(
    record: Mapping[str, Any],
    source_state: str,
    target_state: str,
    evidence: Mapping[str, Any],
    *,
    previous_event: Mapping[str, Any] | None = None,
) -> None:
    if target_state == VALIDATED:
        if evidence.get("package_receipt_sha256") != record.get("package_receipt_sha256"):
            raise LifecycleError("VALIDATED evidence is not bound to this package receipt")
        report = evidence.get("validation_report")
        if not isinstance(report, Mapping):
            raise LifecycleError("VALIDATED requires validation_report evidence")
        if evidence.get("validation_report_sha256") != evidence_sha256(report):
            raise LifecycleError("VALIDATED validation_report hash mismatch")
        if report.get("valid") is not True:
            raise LifecycleError("VALIDATED requires a valid validation_report")
        if report.get("errors") not in ([], None):
            raise LifecycleError("VALIDATED refuses validation_report errors")
        if report.get("security_findings") not in ([], None):
            raise LifecycleError("VALIDATED refuses security findings")
        validation_claims = report.get("claim_sha256_by_unit")
        if not isinstance(validation_claims, Mapping):
            raise LifecycleError(
                "VALIDATED validation_report requires claim_sha256_by_unit"
            )
        expected_unit_ids = set(record.get("unit_ids", []))
        if set(validation_claims) != expected_unit_ids:
            raise LifecycleError(
                "VALIDATED validation claim digests must cover exact package unit set"
            )
        for unit_id, digest in validation_claims.items():
            if (
                not isinstance(digest, str)
                or not digest.startswith("sha256:")
                or not _hex64(digest.removeprefix("sha256:"))
            ):
                raise LifecycleError(
                    f"VALIDATED validation claim digest is invalid: {unit_id}"
                )

        faithfulness = evidence.get("faithfulness_report")
        if not isinstance(faithfulness, Mapping):
            raise LifecycleError("VALIDATED requires faithfulness_report evidence")
        if evidence.get("faithfulness_report_sha256") != evidence_sha256(faithfulness):
            raise LifecycleError("VALIDATED faithfulness_report hash mismatch")
        if faithfulness.get("schema_version") != "0.1":
            raise LifecycleError("VALIDATED requires faithfulness schema_version 0.1")
        if faithfulness.get("valid") is not True or faithfulness.get("state") != "VALIDATED":
            raise LifecycleError("VALIDATED requires a valid faithfulness_report")
        if faithfulness.get("errors") not in ([], None):
            raise LifecycleError("VALIDATED refuses faithfulness_report errors")
        if faithfulness.get("candidate_sha256") != record.get("candidate_sha256"):
            raise LifecycleError("faithfulness report is not bound to lifecycle candidate")
        if faithfulness.get("knowledge_id") != record.get("knowledge_id"):
            raise LifecycleError("faithfulness report knowledge_id mismatch")
        if faithfulness.get("source_ids") != record.get("source_ids"):
            raise LifecycleError("faithfulness report source_ids mismatch")
        unit_results = faithfulness.get("unit_results")
        if not isinstance(unit_results, list) or not unit_results:
            raise LifecycleError("faithfulness report requires unit_results")

        result_unit_ids: list[str] = []
        for unit_result in unit_results:
            if not isinstance(unit_result, Mapping):
                raise LifecycleError("faithfulness unit_result must be a mapping")
            result_unit_id = _nonempty(
                unit_result.get("unit_id"),
                "faithfulness unit_result unit_id",
            )
            result_unit_ids.append(result_unit_id)
            result_claim_sha256 = _nonempty(
                unit_result.get("claim_sha256"),
                "faithfulness unit_result claim_sha256",
            )
            if (
                not result_claim_sha256.startswith("sha256:")
                or not _hex64(result_claim_sha256.removeprefix("sha256:"))
            ):
                raise LifecycleError(
                    "faithfulness unit_result claim_sha256 must be a sha256 reference"
                )
            if validation_claims.get(result_unit_id) != result_claim_sha256:
                raise LifecycleError(
                    "faithfulness claim digest does not match validated package claim"
                )
            if (
                unit_result.get("entailment") != "SUPPORTED"
                or unit_result.get("qualifiers_preserved") is not True
            ):
                raise LifecycleError(
                    "VALIDATED requires supported qualifier-preserving unit results"
                )

            support = unit_result.get("support")
            if not isinstance(support, list) or not support:
                raise LifecycleError(
                    "VALIDATED requires non-empty verified support for every unit"
                )
            for evidence_item in support:
                if not isinstance(evidence_item, Mapping):
                    raise LifecycleError("faithfulness support item must be a mapping")
                if evidence_item.get("source_id") not in record.get("source_ids", []):
                    raise LifecycleError(
                        "faithfulness support source_id is outside lifecycle source_ids"
                    )
                _nonempty(evidence_item.get("section_id"), "faithfulness support section_id")
                section_hash = evidence_item.get("section_sha256")
                span_hash = evidence_item.get("span_sha256")
                if not _hex64(section_hash) or not _hex64(span_hash):
                    raise LifecycleError("faithfulness support requires sha256 digests")
                start = evidence_item.get("start_byte")
                end = evidence_item.get("end_byte")
                if (
                    not isinstance(start, int)
                    or isinstance(start, bool)
                    or not isinstance(end, int)
                    or isinstance(end, bool)
                    or start < 0
                    or end <= start
                ):
                    raise LifecycleError("faithfulness support byte span is invalid")

        if len(set(result_unit_ids)) != len(result_unit_ids):
            raise LifecycleError("faithfulness report contains duplicate unit results")
        if set(result_unit_ids) != set(record.get("unit_ids", [])):
            raise LifecycleError(
                "faithfulness report must cover the exact lifecycle package unit set"
            )

        _nonempty(faithfulness.get("judge_id"), "faithfulness judge_id")
        _nonempty(evidence.get("validator_id"), "validator_id")
    elif target_state == PROMOTED:
        _nonempty(evidence.get("decision_ref"), "decision_ref")
        if source_state in {SUSPENDED, DISPUTED}:
            incident_ref = _nonempty(evidence.get("incident_ref"), "incident_ref")
            _nonempty(evidence.get("resolution_ref"), "resolution_ref")
            if not isinstance(previous_event, Mapping):
                raise LifecycleError("incident restoration requires previous lifecycle event")
            previous_evidence = previous_event.get("evidence")
            if (
                not isinstance(previous_evidence, Mapping)
                or previous_evidence.get("incident_ref") != incident_ref
            ):
                raise LifecycleError(
                    "incident restoration must reference the active incident_ref"
                )
    elif target_state == SUPERSEDED:
        _nonempty(evidence.get("replacement_skill_id"), "replacement_skill_id")
        _positive_int(
            evidence.get("replacement_skill_version"),
            "replacement_skill_version",
        )
    elif target_state in {SUSPENDED, DISPUTED}:
        incident_ref = _nonempty(evidence.get("incident_ref"), "incident_ref")
        _nonempty(evidence.get("reason"), "reason")
        if source_state in {SUSPENDED, DISPUTED}:
            if not isinstance(previous_event, Mapping):
                raise LifecycleError(
                    "incident transition requires previous lifecycle event"
                )
            previous_evidence = previous_event.get("evidence")
            if (
                not isinstance(previous_evidence, Mapping)
                or previous_evidence.get("incident_ref") != incident_ref
            ):
                raise LifecycleError(
                    "incident transition must preserve the active incident_ref"
                )
    elif target_state == REVOKED:
        incident_ref = _nonempty(evidence.get("incident_ref"), "incident_ref")
        _nonempty(evidence.get("reason"), "reason")
        _nonempty(evidence.get("decision_ref"), "decision_ref")
        if source_state in {SUSPENDED, DISPUTED}:
            if not isinstance(previous_event, Mapping):
                raise LifecycleError(
                    "incident revocation requires previous lifecycle event"
                )
            previous_evidence = previous_event.get("evidence")
            if (
                not isinstance(previous_evidence, Mapping)
                or previous_evidence.get("incident_ref") != incident_ref
            ):
                raise LifecycleError(
                    "incident revocation must reference the active incident_ref"
                )


def validate_lifecycle_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Verify state legality, package binding, and the append-only event hash chain."""

    errors: list[str] = []
    if record.get("schema_version") != LIFECYCLE_SCHEMA_VERSION:
        errors.append("schema_version mismatch")
    state = record.get("state")
    if state not in STATES:
        errors.append("unknown lifecycle state")
    events = record.get("events")
    if not isinstance(events, list) or not events:
        errors.append("lifecycle requires event history")
        return {"valid": False, "errors": errors}

    previous_digest = None
    current_state = None
    for index, event in enumerate(events):
        if not isinstance(event, Mapping):
            errors.append("lifecycle event must be a mapping")
            break
        if event.get("sequence") != index:
            errors.append("lifecycle sequence mismatch")
        if event.get("previous_event_sha256") != previous_digest:
            errors.append("lifecycle event hash chain mismatch")
        supplied_digest = event.get("event_sha256")
        payload = {key: deepcopy(value) for key, value in event.items() if key != "event_sha256"}
        calculated = _digest(payload)
        if supplied_digest != calculated:
            errors.append("lifecycle event digest mismatch")
        from_state = event.get("from_state")
        to_state = event.get("to_state")
        if index == 0:
            if from_state is not None or to_state != COMPILED_CANDIDATE:
                errors.append("invalid lifecycle genesis")
            genesis_evidence = event.get("evidence")
            if not isinstance(genesis_evidence, Mapping):
                errors.append("lifecycle genesis requires package binding evidence")
            else:
                for field in ("skill_id", "skill_version", "knowledge_id", "source_ids", "unit_ids", "candidate_sha256", "package_receipt_sha256"):
                    if genesis_evidence.get(field) != record.get(field):
                        errors.append(f"lifecycle package binding mismatch: {field}")
        else:
            if from_state != current_state:
                errors.append("lifecycle from_state mismatch")
            if to_state not in _ALLOWED.get(current_state, frozenset()):
                errors.append("illegal lifecycle transition")

            transition_evidence = event.get("evidence")
            if not isinstance(transition_evidence, Mapping):
                errors.append("lifecycle transition requires evidence")
            else:
                try:
                    _validate_evidence(
                        record,
                        from_state,
                        to_state,
                        transition_evidence,
                        previous_event=events[index - 1],
                    )
                    if to_state == SUPERSEDED:
                        if (
                            transition_evidence["replacement_skill_id"]
                            == record["skill_id"]
                            and transition_evidence["replacement_skill_version"]
                            <= record["skill_version"]
                        ):
                            raise LifecycleError(
                                "SUPERSEDED requires a different or newer replacement skill"
                            )
                except LifecycleError as exc:
                    errors.append(f"lifecycle transition evidence invalid: {exc}")
        current_state = to_state
        previous_digest = supplied_digest

    if current_state != state:
        errors.append("record state does not match final event")
    return {"valid": not errors, "errors": errors}


def transition_lifecycle(
    record: Mapping[str, Any],
    target_state: str,
    *,
    occurred_at: str,
    actor: str,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Append one evidence-bound lifecycle transition; never mutate the caller."""

    report = validate_lifecycle_record(record)
    if not report["valid"]:
        raise LifecycleError("cannot transition an invalid lifecycle record: " + "; ".join(report["errors"]))

    current_state = record["state"]
    if target_state not in STATES:
        raise LifecycleError(f"unknown lifecycle target state: {target_state!r}")
    if target_state not in _ALLOWED[current_state]:
        raise LifecycleError(f"illegal lifecycle transition: {current_state} -> {target_state}")
    timestamp = _nonempty(occurred_at, "occurred_at")
    actor_id = _nonempty(actor, "actor")
    if not isinstance(evidence, Mapping):
        raise LifecycleError("evidence must be a mapping")
    _validate_evidence(
        record,
        current_state,
        target_state,
        evidence,
        previous_event=record["events"][-1],
    )

    if target_state == SUPERSEDED:
        if (
            evidence["replacement_skill_id"] == record["skill_id"]
            and evidence["replacement_skill_version"] <= record["skill_version"]
        ):
            raise LifecycleError("SUPERSEDED requires a different or newer replacement skill")

    updated = deepcopy(dict(record))
    previous = updated["events"][-1]["event_sha256"]
    event = {
        "sequence": len(updated["events"]),
        "from_state": current_state,
        "to_state": target_state,
        "occurred_at": timestamp,
        "actor": actor_id,
        "evidence": deepcopy(dict(evidence)),
        "previous_event_sha256": previous,
    }
    event["event_sha256"] = _digest(event)
    updated["events"].append(event)
    updated["state"] = target_state
    return updated
