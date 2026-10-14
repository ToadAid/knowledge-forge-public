"""P3B-P1: untrusted, non-authorizing promotion-review handoff.

This is an agent-facing CONTRACT, not a protected host, authentication service,
operator credential, or durable one-time decision store. An agent can generate,
modify and replay these bytes. Consumers MUST NOT translate this handoff or any
unprotected review receipt into OperatorAuthorization.
"""
from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
import json
from typing import Any, Mapping

from .approval import (
    ApprovalContractError,
    approval_request_sha256,
    bind_request_evidence,
    build_approval_evidence,
    build_approval_request,
)
from .candidate_preview import _clock, preview_validated_candidate

HANDOFF_KIND = "UNTRUSTED_AGENT_PROMOTION_REVIEW_HANDOFF_NOT_AUTHORIZATION"
COVERAGE = "UNKNOWN_FROM_PACKAGE_DO_NOT_CLAIM_COMPLETE_CORPUS"
MAX_REQUEST_LIFETIME = timedelta(minutes=15)
_FIELDS = frozenset({
    "schema_version", "kind", "approval_request", "approval_evidence",
    "request_sha256", "source_corpus_coverage", "host_authentication_verified",
    "authorization_granted", "promotion_authorized", "review_receipt_written",
    "operator_cli_invoked", "production_registry_mutated", "execution_authority",
    "tool_grants",
})


def _sha(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _items(files: Mapping[str, bytes], lifecycle: Mapping[str, Any]) -> list[dict[str, str]]:
    return [
        {"label": "claim-index", "sha256": _sha(files["claim-index.json"])},
        {"label": "manifest", "sha256": _sha(files["manifest.json"])},
        {"label": "source-receipt", "sha256": _sha(files["SOURCE_RECEIPT.json"])},
        {"label": "validated-lifecycle", "sha256": _sha(_json_bytes(lifecycle))},
    ]


def _bounded_times(requested_at: str, expires_at: str) -> tuple[str, str]:
    requested, requested_text = _clock(requested_at)
    expires, expires_text = _clock(expires_at)
    if not requested < expires <= requested + MAX_REQUEST_LIFETIME:
        raise ApprovalContractError("handoff expiry must be > request and <= 15 minutes")
    return requested_text, expires_text


def build_untrusted_review_handoff(
    files: Mapping[str, bytes], lifecycle: Mapping[str, Any], *,
    request_id: str, requesting_agent: str, requesting_principal: str,
    requested_at: str, expires_at: str,
) -> dict[str, Any]:
    """Create a *request* for separate review, NEVER a host decision."""
    requested_at, expires_at = _bounded_times(requested_at, expires_at)
    preview = preview_validated_candidate(files, lifecycle, observed_at=requested_at)
    evidence = build_approval_evidence(
        request_id=request_id,
        target_skill_id=preview["skill_id"],
        target_skill_version=preview["skill_version"],
        package_receipt_sha256=preview["package_receipt_sha256"],
        validated_lifecycle_head_sha256=preview["validated_lifecycle_head_sha256"],
        items=_items(files, lifecycle),
    )
    request = build_approval_request(
        request_id=request_id,
        requesting_agent=requesting_agent,
        requesting_principal=requesting_principal,
        target_skill_id=preview["skill_id"],
        target_skill_version=preview["skill_version"],
        package_receipt_sha256=preview["package_receipt_sha256"],
        validated_lifecycle_head_sha256=preview["validated_lifecycle_head_sha256"],
        requested_at=requested_at, expires_at=expires_at,
        evidence_digest=evidence["evidence_digest"],
    )
    bind_request_evidence(request, evidence)
    return {
        "schema_version": "0.1", "kind": HANDOFF_KIND,
        "approval_request": request, "approval_evidence": evidence,
        "request_sha256": approval_request_sha256(request),
        "source_corpus_coverage": COVERAGE,
        "host_authentication_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "review_receipt_written": False,
        "operator_cli_invoked": False,
        "production_registry_mutated": False,
        "execution_authority": "NONE", "tool_grants": [],
    }


def validate_untrusted_review_handoff(
    value: Mapping[str, Any], files: Mapping[str, bytes],
    lifecycle: Mapping[str, Any], *, observed_at: str,
) -> dict[str, Any]:
    """Revalidate against supplied candidate bytes; NOT protected host truth.

    A real host must obtain the package from an independently trusted snapshot,
    authenticate the human outside agent authority, atomically claim a one-use
    decision token, and call its own privileged operator process. This code does
    none of those things and never returns any authorization object.
    """
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ApprovalContractError("handoff fields mismatch")
    if value.get("schema_version") != "0.1" or value.get("kind") != HANDOFF_KIND:
        raise ApprovalContractError("unsupported or authorizing handoff kind")
    if value.get("source_corpus_coverage") != COVERAGE:
        raise ApprovalContractError("handoff must not claim complete source coverage")
    for flag in (
        "host_authentication_verified", "authorization_granted",
        "promotion_authorized", "review_receipt_written", "operator_cli_invoked",
        "production_registry_mutated",
    ):
        if value.get(flag) is not False:
            raise ApprovalContractError("handoff cannot assert authority: " + flag)
    if value.get("execution_authority") != "NONE" or value.get("tool_grants") != []:
        raise ApprovalContractError("handoff cannot grant execution tools")
    request, evidence = bind_request_evidence(
        value.get("approval_request"), value.get("approval_evidence")
    )
    if value.get("request_sha256") != approval_request_sha256(request):
        raise ApprovalContractError("handoff request digest mismatch")
    requested_at, expires_at = _bounded_times(request["requested_at"], request["expires_at"])
    if (requested_at, expires_at) != (request["requested_at"], request["expires_at"]):
        raise ApprovalContractError("handoff request timestamps not canonical")
    now, _ = _clock(observed_at)
    start, _ = _clock(requested_at)
    end, _ = _clock(expires_at)
    if not start <= now < end:
        raise ApprovalContractError("handoff not current: not started or expired")
    preview = preview_validated_candidate(files, lifecycle, observed_at=observed_at)
    for field, source in (
        ("target_skill_id", "skill_id"),
        ("target_skill_version", "skill_version"),
        ("package_receipt_sha256", "package_receipt_sha256"),
        ("validated_lifecycle_head_sha256", "validated_lifecycle_head_sha256"),
    ):
        if request.get(field) != preview[source]:
            raise ApprovalContractError("stale candidate: " + field)
    expected_evidence = build_approval_evidence(
        request_id=request["request_id"],
        target_skill_id=preview["skill_id"],
        target_skill_version=preview["skill_version"],
        package_receipt_sha256=preview["package_receipt_sha256"],
        validated_lifecycle_head_sha256=preview["validated_lifecycle_head_sha256"],
        items=_items(files, lifecycle),
    )
    if evidence != expected_evidence:
        raise ApprovalContractError("candidate evidence bytes changed")
    return {
        "status": "UNTRUSTED_HANDOFF_VALID_EXTERNAL_HOST_REQUIRED",
        "request_sha256": value["request_sha256"],
        "candidate_validated": True,
        "host_authentication_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "one_time_replay_protection_verified": False,
        "operator_cli_invoked": False,
        "execution_authority": "NONE",
    }
