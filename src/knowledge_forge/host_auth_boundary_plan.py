"""P3B-P2C-P2: untrusted host-authentication boundary review, not a verifier.

This module must remain portable plain data. It has no host/identity/secret
access, cannot authenticate a human, and cannot authorize an operator.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any

from .protected_review_plan import validate_untrusted_operator_review_plan


SCHEMA_VERSION = "0.1"
KIND = "UNTRUSTED_LINUX_HOST_AUTH_REQUIREMENTS_NOT_ATTESTATION"
STATUS = "HUMAN_CONTROLLED_HOST_IMPLEMENTATION_REQUIRED"
HOST_FAMILY = "linux"
PROOF_IDS = (
    "SEPARATE_ADMIN_SESSION_UNREACHABLE_FROM_AGENT",
    "INDEPENDENT_HUMAN_AUTHENTICATION",
    "HUMAN_CONFIRMATION_OF_EXACT_REQUEST_AND_CANDIDATE",
    "SEPARATE_AGENT_AND_OPERATOR_UIDS",
    "EFFECTIVE_SUDO_POLKIT_CONTAINER_DENIAL",
    "AGENT_TOOL_FD_SOCKET_AND_CREDENTIAL_DENIAL",
    "TRUSTED_PINNED_ROOT_OWNED_OPERATOR_CODE",
    "PROTECTED_OPERATOR_STATE_AND_LEDGER",
    "INDEPENDENT_VALIDATED_CANDIDATE_RECHECK",
    "INDEPENDENT_CURRENT_REGISTRY_HEAD_RECHECK",
    "TRUSTED_HOST_CLOCK_AND_REQUEST_EXPIRY",
    "TRUSTED_SINGLE_USE_DECISION_CONSUMPTION",
    "PROTECTED_LAUNCH_AND_AUDIT_RECEIPT",
)
EXPECTED_LAYOUT = {
    "agent_identity": "kf-agent",
    "operator_identity": "kf-operator",
    "operator_code_root": "/opt/toadaid-kf-operator",
    "operator_state_root": "/var/lib/toadaid-kf-operator",
}
REFUSAL_FLAGS = (
    "host_identity_attested", "host_clock_attested", "agent_isolated",
    "human_authenticated", "human_confirmation_recorded", "operator_code_verified",
    "candidate_independently_rechecked", "registry_independently_rechecked",
    "single_use_ledger_ready", "authorization_issued", "operator_invoked",
    "promotion_authorized", "registry_mutated",
)
_SHA = re.compile(r"^sha256:[a-f0-9]{64}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class HostAuthBoundaryPlanError(ValueError):
    """An invalid untrusted review proposal, never a host authorization error."""


def _clock(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise HostAuthBoundaryPlanError("observed_at must be canonical UTC seconds")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise HostAuthBoundaryPlanError("invalid UTC observation") from exc


def _canonical_digest(value: dict[str, Any]) -> str:
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    return "sha256:" + sha256(payload).hexdigest()


def build_untrusted_host_auth_requirements(
    review_plan: Any, *, observed_at: str, host_family: str = HOST_FAMILY,
) -> dict[str, Any]:
    """Bind proposed proof *questions* to an untrusted P2C-P1 plan.

    The caller can forge all inputs. The returned SHA is an integrity label
    for caller-controlled bytes, not a signature or root-host attestation.
    """
    if host_family != HOST_FAMILY or type(host_family) is not str:
        raise HostAuthBoundaryPlanError("P2C-P2 TNG review supports exact linux label only")
    try:
        plan = validate_untrusted_operator_review_plan(review_plan)
    except (TypeError, ValueError, KeyError) as exc:
        raise HostAuthBoundaryPlanError("invalid untrusted P2C-P1 plan") from exc
    now = _clock(observed_at)
    previous = _clock(plan["observed_at"])
    expiry = _clock(plan["expires_at"])
    if not previous <= now < expiry:
        raise HostAuthBoundaryPlanError("observation outside untrusted review interval")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": STATUS,
        "host_family": HOST_FAMILY,
        "host_name_expected": "TNG",
        "review_plan_sha256": _canonical_digest(plan),
        "request_id": plan["request_id"],
        "request_sha256": plan["request_sha256"],
        "target_skill_id": plan["target_skill_id"],
        "target_skill_version": plan["target_skill_version"],
        "package_receipt_sha256": plan["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": plan["validated_lifecycle_head_sha256"],
        "evidence_digest": plan["evidence_digest"],
        "expected_registry_head_sha256": plan["expected_registry_head_sha256"],
        "observed_at": observed_at,
        "expires_at": plan["expires_at"],
        "proof_questions": list(PROOF_IDS),
        "expected_layout": dict(EXPECTED_LAYOUT),
        "inputs_trust": "AGENT_SUPPLIED_UNTRUSTED",
        "security_verdict": "UNVERIFIED_NO_HOST_ATTESTATION",
        "execution_authority": "NONE",
        "tool_grants": [],
        **{flag: False for flag in REFUSAL_FLAGS},
    }


def validate_untrusted_host_auth_requirements(
    value: Any, review_plan: Any,
) -> dict[str, Any]:
    """Strictly compare bytes with the proposal regenerated from the plan.

    No amount of matching input data can make this a host attestation.
    """
    if not isinstance(value, dict):
        raise HostAuthBoundaryPlanError("requirements must be a mapping")
    if value.get("status") != STATUS or value.get("security_verdict") != "UNVERIFIED_NO_HOST_ATTESTATION":
        raise HostAuthBoundaryPlanError("host-auth output may not attest security")
    if value.get("execution_authority") != "NONE" or value.get("tool_grants") != []:
        raise HostAuthBoundaryPlanError("host-auth output may not grant tools")
    for flag in REFUSAL_FLAGS:
        if value.get(flag) is not False:
            raise HostAuthBoundaryPlanError(f"forged host proof claim: {flag}")
    if not isinstance(value.get("review_plan_sha256"), str) or not _SHA.fullmatch(value["review_plan_sha256"]):
        raise HostAuthBoundaryPlanError("noncanonical untrusted plan sha")
    expected = build_untrusted_host_auth_requirements(
        review_plan, observed_at=value.get("observed_at"),
        host_family=value.get("host_family"),
    )
    if value != expected:
        raise HostAuthBoundaryPlanError("untrusted host requirement envelope mismatch")
    return expected
