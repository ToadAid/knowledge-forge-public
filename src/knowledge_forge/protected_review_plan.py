"""P3B-P2C-P1: untrusted protected-host review inputs, never authorization.

This portable plan may be forged by an agent. It is useful only to tell an
independent operator what to re-read and revalidate from protected storage.
It issues no host attestations, challenges, credentials or permission grants.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from .approval import (
    approval_request_sha256,
    bind_request_evidence,
    require_current_candidate,
)

SCHEMA_VERSION = "0.1"
KIND = "UNTRUSTED_OPERATOR_REVIEW_PLAN_NOT_AUTHORIZATION"
MAX_REQUEST_AGE = timedelta(minutes=15)
_SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
_CLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_FALSE_FLAGS = (
    "independent_host_attestation_verified",
    "independent_candidate_recheck_verified",
    "independent_registry_recheck_verified",
    "human_authentication_verified",
    "protected_ledger_write_performed",
    "authorization_issued",
    "operator_invoked",
    "promotion_authorized",
    "registry_mutated",
)
_FIELDS = frozenset({
    "schema_version", "kind", "request_id", "request_sha256",
    "target_skill_id", "target_skill_version", "package_receipt_sha256",
    "validated_lifecycle_head_sha256", "evidence_digest",
    "expected_registry_head_sha256", "observed_at", "expires_at",
    "candidate_binding_source", "registry_head_source", "execution_authority",
    "tool_grants", *_FALSE_FLAGS,
})


class ProtectedReviewPlanError(ValueError):
    """Unsafe or noncanonical *untrusted plan* input."""


def _utc(text: Any, label: str) -> datetime:
    if not isinstance(text, str) or not _CLOCK.fullmatch(text):
        raise ProtectedReviewPlanError(f"{label} requires canonical UTC seconds")
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ProtectedReviewPlanError(f"invalid {label}") from exc


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ProtectedReviewPlanError(f"{label} must be lowercase sha256:hex")
    return value


def build_untrusted_operator_review_plan(
    request_value: Any,
    evidence_value: Any,
    candidate_binding_value: Any,
    *,
    expected_registry_head_sha256: str,
    observed_at: str,
) -> dict[str, Any]:
    """Bundle exact comparison targets, without verifying their source.

    All arguments can originate in model-controlled files. Passing the plan
    does not attest the actual registry/candidate, authenticate a human, or
    establish one-time state. A future protected host MUST re-read independently.
    """
    request, _evidence = bind_request_evidence(request_value, evidence_value)
    require_current_candidate(request, candidate_binding_value)
    registry_head = _digest(expected_registry_head_sha256, "expected registry head")
    observed = _utc(observed_at, "observed_at")
    requested = _utc(request["requested_at"], "requested_at")
    expires = _utc(request["expires_at"], "expires_at")
    if not requested <= observed < expires:
        raise ProtectedReviewPlanError("review must be within the request interval")
    if not timedelta(0) < expires - requested <= MAX_REQUEST_AGE:
        raise ProtectedReviewPlanError("review request lifetime exceeds 15 minutes")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "request_id": request["request_id"],
        "request_sha256": approval_request_sha256(request),
        "target_skill_id": request["target_skill_id"],
        "target_skill_version": request["target_skill_version"],
        "package_receipt_sha256": request["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": request["validated_lifecycle_head_sha256"],
        "evidence_digest": request["evidence_digest"],
        "expected_registry_head_sha256": registry_head,
        "observed_at": observed_at,
        "expires_at": request["expires_at"],
        "candidate_binding_source": "CALLER_SUPPLIED_UNTRUSTED",
        "registry_head_source": "CALLER_SUPPLIED_UNTRUSTED",
        "execution_authority": "NONE",
        "tool_grants": [],
        **{flag: False for flag in _FALSE_FLAGS},
    }


def validate_untrusted_operator_review_plan(value: Any) -> dict[str, Any]:
    """Strict syntax/refusal check. A valid plan is STILL NOT trusted."""
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise ProtectedReviewPlanError("review plan must have exact fields")
    if value["schema_version"] != SCHEMA_VERSION or value["kind"] != KIND:
        raise ProtectedReviewPlanError("unsupported review plan")
    if value["execution_authority"] != "NONE" or value["tool_grants"] != []:
        raise ProtectedReviewPlanError("review plan cannot grant tools or execution")
    for flag in _FALSE_FLAGS:
        if value[flag] is not False:
            raise ProtectedReviewPlanError(f"review plan cannot claim {flag}")
    if value["candidate_binding_source"] != "CALLER_SUPPLIED_UNTRUSTED":
        raise ProtectedReviewPlanError("caller binding cannot attest host identity")
    if value["registry_head_source"] != "CALLER_SUPPLIED_UNTRUSTED":
        raise ProtectedReviewPlanError("caller registry head cannot attest host truth")
    for name in (
        "request_sha256", "package_receipt_sha256",
        "validated_lifecycle_head_sha256", "evidence_digest",
        "expected_registry_head_sha256",
    ):
        _digest(value[name], name)
    if not isinstance(value["request_id"], str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}", value["request_id"]
    ):
        raise ProtectedReviewPlanError("invalid request id")
    if not isinstance(value["target_skill_id"], str) or not re.fullmatch(
        r"knowledge-forge:[a-z0-9][a-z0-9._:-]{0,255}@sha256:[0-9a-f]{64}",
        value["target_skill_id"],
    ):
        raise ProtectedReviewPlanError("invalid canonical skill id")
    if type(value["target_skill_version"]) is not int or not 0 < value["target_skill_version"] <= 2**31:
        raise ProtectedReviewPlanError("invalid skill version")
    observed = _utc(value["observed_at"], "observed_at")
    expires = _utc(value["expires_at"], "expires_at")
    if observed >= expires:
        raise ProtectedReviewPlanError("review observation is expired")
    return dict(value)
