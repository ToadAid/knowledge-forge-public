"""P3B-P2C-P3-P0: deterministic UNTRUSTED single-use ledger rehearsal.

NO durable storage, interprocess atomicity, host authentication, trusted clock,
challenge issuance, approval, promotion, or operator integration exists here.
This data may be forged or rolled back by an agent. Never pass it as authority.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from .host_auth_boundary_plan import validate_untrusted_host_auth_requirements

SCHEMA_VERSION = "0.1"
KIND = "UNTRUSTED_SINGLE_USE_LEDGER_REHEARSAL_NOT_AUTHORIZATION"
STATES = frozenset({
    "PENDING", "PREPARED_SIMULATION", "CONSUMED_SIMULATION", "CANCELLED",
    "EXPIRED", "RECONCILIATION_REQUIRED", "CLOSED_NO_EFFECT",
})
TRANSITIONS = {
    ("PENDING", "PREPARE_SIMULATION"): "PREPARED_SIMULATION",
    ("PENDING", "CANCEL_SIMULATION"): "CANCELLED",
    ("PREPARED_SIMULATION", "CANCEL_SIMULATION"): "CANCELLED",
    ("PREPARED_SIMULATION", "CONSUME_SIMULATION"): "CONSUMED_SIMULATION",
    ("PENDING", "EXPIRE_SIMULATION"): "EXPIRED",
    ("PREPARED_SIMULATION", "EXPIRE_SIMULATION"): "EXPIRED",
    ("PREPARED_SIMULATION", "RESTART_SIMULATION"): "RECONCILIATION_REQUIRED",
    ("CONSUMED_SIMULATION", "RESTART_SIMULATION"): "RECONCILIATION_REQUIRED",
    ("RECONCILIATION_REQUIRED", "CLOSE_NO_EFFECT_SIMULATION"): "CLOSED_NO_EFFECT",
}
MAX_EVENTS = 12
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA = re.compile(r"^sha256:[a-f0-9]{64}$")
_FIELDS = frozenset({
    "schema_version", "kind", "requirements_sha256", "request_id", "request_sha256",
    "target_skill_id", "package_receipt_sha256", "validated_lifecycle_head_sha256",
    "expected_registry_head_sha256", "expires_at", "created_at", "state", "revision",
    "events", "revision_sha256", "execution_authority", "tool_grants",
    "human_authenticated", "authorization_issued", "promotion_authorized",
    "operator_invoked", "registry_mutated", "atomic_consumption_verified",
    "durable_storage_verified", "host_time_verified", "replay_protection_verified",
})
_FALSE = (
    "human_authenticated", "authorization_issued", "promotion_authorized",
    "operator_invoked", "registry_mutated", "atomic_consumption_verified",
    "durable_storage_verified", "host_time_verified", "replay_protection_verified",
)
_EVENT_FIELDS = frozenset({"revision", "action", "occurred_at", "prev_revision_sha256", "event_sha256"})


class LedgerSimulationError(ValueError):
    """Inconsistent synthetic event log, never an authorization denial."""


def _clock(value: Any) -> datetime:
    if not isinstance(value, str) or not _TIME.fullmatch(value):
        raise LedgerSimulationError("time must be canonical UTC seconds")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise LedgerSimulationError("invalid UTC time") from exc


def _sha(value: Any) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(data).hexdigest()


def _digest_snapshot(state: dict[str, Any]) -> str:
    return _sha({k: v for k, v in state.items() if k != "revision_sha256"})


def _trusted_none() -> dict[str, Any]:
    return dict.fromkeys(_FALSE, False)


def create_rehearsal(requirements: Any, review_plan: Any, *, at: str) -> dict[str, Any]:
    """Create untrusted, rollbackable demo state; never a challenge/token."""
    try:
        binding = validate_untrusted_host_auth_requirements(requirements, review_plan)
    except (TypeError, ValueError, KeyError) as exc:
        raise LedgerSimulationError("upstream untrusted requirements mismatch") from exc
    now = _clock(at)
    observed, expires = _clock(binding["observed_at"]), _clock(binding["expires_at"])
    if not observed <= now < expires:
        raise LedgerSimulationError("start outside untrusted review interval")
    state = {
        "schema_version": SCHEMA_VERSION, "kind": KIND,
        "requirements_sha256": _sha(binding),
        "request_id": binding["request_id"], "request_sha256": binding["request_sha256"],
        "target_skill_id": binding["target_skill_id"],
        "package_receipt_sha256": binding["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": binding["validated_lifecycle_head_sha256"],
        "expected_registry_head_sha256": binding["expected_registry_head_sha256"],
        "expires_at": binding["expires_at"], "created_at": at,
        "state": "PENDING", "revision": 0, "events": [],
        "execution_authority": "NONE", "tool_grants": [], **_trusted_none(),
    }
    state["revision_sha256"] = _digest_snapshot(state)
    return state


def _event(parent: dict[str, Any], action: str, at: str) -> dict[str, Any]:
    body = {
        "revision": parent["revision"] + 1,
        "action": action, "occurred_at": at,
        "prev_revision_sha256": parent["revision_sha256"],
    }
    return {**body, "event_sha256": _sha(body)}


def _advance_verified(state: dict[str, Any], action: str, at: str) -> dict[str, Any]:
    if not isinstance(action, str) or (state["state"], action) not in TRANSITIONS:
        raise LedgerSimulationError("invalid, duplicate or terminal simulation transition")
    now, last, expires = _clock(at), _clock(
        state["events"][-1]["occurred_at"] if state["events"] else state["created_at"]
    ), _clock(state["expires_at"])
    if now < last:
        raise LedgerSimulationError("caller time moved backwards")
    if action == "EXPIRE_SIMULATION":
        if now < expires:
            raise LedgerSimulationError("not expired")
    elif action != "RESTART_SIMULATION" and not (now < expires):
        # A crash/restart is observable even after expiry; all other transitions refuse.
        raise LedgerSimulationError("request expired before transition")
    if len(state["events"]) >= MAX_EVENTS:
        raise LedgerSimulationError("event limit exceeded")
    result = deepcopy(state)
    result["events"].append(_event(state, action, at))
    result["revision"] += 1
    result["state"] = TRANSITIONS[(state["state"], action)]
    result["revision_sha256"] = _digest_snapshot(result)
    return result


def validate_rehearsal(value: Any, requirements: Any, review_plan: Any) -> dict[str, Any]:
    """Full replay from caller-controlled genesis. Digest checks are NOT signatures."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise LedgerSimulationError("rehearsal must contain exact fields")
    if value.get("kind") != KIND or value.get("schema_version") != SCHEMA_VERSION:
        raise LedgerSimulationError("invalid rehearsal kind/version")
    if value.get("execution_authority") != "NONE" or value.get("tool_grants") != []:
        raise LedgerSimulationError("rehearsal cannot grant tools or authority")
    if any(value.get(k) is not False for k in _FALSE):
        raise LedgerSimulationError("rehearsal cannot claim authentication, ledger or authority")
    if not isinstance(value.get("events"), list) or len(value["events"]) > MAX_EVENTS:
        raise LedgerSimulationError("invalid bounded event history")
    if type(value.get("revision")) is not int or value["revision"] != len(value["events"]):
        raise LedgerSimulationError("noncanonical event revision")
    if not isinstance(value.get("revision_sha256"), str) or not _SHA.fullmatch(value["revision_sha256"]):
        raise LedgerSimulationError("invalid revision hash")
    start = create_rehearsal(requirements, review_plan, at=value.get("created_at"))
    for item in value["events"]:
        if not isinstance(item, dict) or set(item) != _EVENT_FIELDS:
            raise LedgerSimulationError("invalid event fields")
        action, at = item.get("action"), item.get("occurred_at")
        expected = _event(start, action, at)
        if item != expected:
            raise LedgerSimulationError("event digest/parent mismatch")
        start = _advance_verified(start, action, at)
    if value != start:
        raise LedgerSimulationError("ledger replay mismatch")
    return start


def rehearse_transition(value: Any, requirements: Any, review_plan: Any, *,
                        action: str, at: str, expected_revision_sha256: str) -> dict[str, Any]:
    """Rehearse a CAS check in one caller-owned snapshot. NOT atomic across processes."""
    state = validate_rehearsal(value, requirements, review_plan)
    if not isinstance(expected_revision_sha256, str) or not _SHA.fullmatch(expected_revision_sha256):
        raise LedgerSimulationError("invalid expected revision")
    if state["revision_sha256"] != expected_revision_sha256:
        raise LedgerSimulationError("stale snapshot / revision mismatch")
    return _advance_verified(state, action, at)
