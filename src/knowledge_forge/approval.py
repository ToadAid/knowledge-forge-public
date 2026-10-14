"""Provider-neutral, review-only approval contracts.

P2B0 records human-review intent without authenticating an operator and without
creating promotion authority.  The privileged operator boundary remains in
``operator_promotion`` and deliberately has no dependency on this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Mapping


APPROVAL_REQUEST_SCHEMA_VERSION = "0.1"
APPROVAL_EVIDENCE_SCHEMA_VERSION = "0.1"
CURRENT_BINDING_SCHEMA_VERSION = "0.1"
REVIEW_RECEIPT_SCHEMA_VERSION = "0.1"
PRESENTATION_SCHEMA_VERSION = "0.1"

REQUEST_TYPE = "SKILL_PROMOTION_REVIEW"
REQUESTED_ACTION = "PROMOTE_VALIDATED_SKILL"
AUTHORITY_BOUNDARY = "EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY"
REVIEW_IDENTITY_KIND = "UNAUTHENTICATED_LOCAL_REVIEWER"
REVIEW_IDENTITY_SOURCE = "LOCAL_TTY_REVIEW_ONLY"

DECISIONS = frozenset({
    "APPROVE",
    "REJECT",
    "REQUEST_CHANGES",
    "EXPIRE",
    "CANCEL",
})
REVIEW_STATES = frozenset({
    "PENDING",
    "REVIEWED_APPROVED",
    "REVIEWED_REJECTED",
    "CHANGES_REQUESTED",
    "EXPIRED",
    "CANCELLED",
    "RECONCILIATION_REQUIRED",
})
PRESENTATION_SURFACES = frozenset({"TERMINAL_TUI", "TELEGRAM_CARD", "DESKTOP_CARD", "WEB_TEMPLE"})

_DECISION_STATE = {
    "APPROVE": "REVIEWED_APPROVED",
    "REJECT": "REVIEWED_REJECTED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "EXPIRE": "EXPIRED",
    "CANCEL": "CANCELLED",
}
_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_CANONICAL_SKILL_ID = re.compile(r"^knowledge-forge:[a-z0-9][a-z0-9._:-]{0,255}@sha256:[0-9a-f]{64}$")
_EVIDENCE_LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

_REQUEST_FIELDS = frozenset({
    "schema_version",
    "request_id",
    "request_type",
    "requesting_agent",
    "requesting_principal",
    "target_skill_id",
    "target_skill_version",
    "package_receipt_sha256",
    "validated_lifecycle_head_sha256",
    "requested_at",
    "expires_at",
    "evidence_digest",
    "requested_action",
    "authority_boundary",
})
_EVIDENCE_FIELDS = frozenset({
    "schema_version",
    "request_id",
    "target_skill_id",
    "target_skill_version",
    "package_receipt_sha256",
    "validated_lifecycle_head_sha256",
    "lifecycle_state",
    "items",
    "evidence_digest",
})
_EVIDENCE_ITEM_FIELDS = frozenset({"label", "sha256"})
_BINDING_FIELDS = frozenset({
    "schema_version",
    "target_skill_id",
    "target_skill_version",
    "package_receipt_sha256",
    "validated_lifecycle_head_sha256",
    "evidence_digest",
    "lifecycle_state",
    "observed_at",
})
_RECEIPT_FIELDS = frozenset({
    "schema_version",
    "receipt_id",
    "request_id",
    "request_sha256",
    "evidence_digest",
    "decision",
    "state",
    "reviewed_at",
    "reviewer_principal",
    "reviewer_identity_kind",
    "review_surface",
    "reason",
    "review_decision_recorded",
    "host_authentication_verified",
    "authorization_granted",
    "promotion_authorized",
    "execution_authority",
    "tool_grants",
    "operator_cli_invoked",
    "production_registry_mutated",
})


class ApprovalContractError(ValueError):
    """Raised when review data cannot preserve the authority boundary."""


@dataclass(frozen=True)
class ReviewIdentity:
    """A display identity, explicitly not a host-authenticated operator."""

    principal_ref: str
    identity_kind: str = REVIEW_IDENTITY_KIND
    source: str = REVIEW_IDENTITY_SOURCE
    host_authenticated: bool = False


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApprovalContractError("value is not canonical JSON data") from exc


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ApprovalContractError(f"{label} must be a mapping")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        missing = sorted(expected - set(value))
        raise ApprovalContractError(
            f"{label} fields must match exact contract; unknown={unknown} missing={missing}"
        )


def _text(value: Any, label: str, *, maximum: int = 160) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ApprovalContractError(f"{label} must be bounded printable text")
    return value


def _identifier(value: Any, label: str) -> str:
    text = _text(value, label, maximum=128)
    if not _IDENTIFIER.fullmatch(text):
        raise ApprovalContractError(f"{label} must be a safe identifier")
    return text


def _skill_identifier(value: Any, label: str) -> str:
    # Preserve synthetic IDs and support canonical Forge IDs with @sha256.
    identifier = _text(value, label, maximum=350)
    if _CANONICAL_SKILL_ID.fullmatch(identifier):
        return identifier
    if '@' in identifier:
        raise ApprovalContractError(f"{label} must be a canonical Forge skill identity")
    return _identifier(identifier, label)


def _sha256_ref(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256_REF.fullmatch(value):
        raise ApprovalContractError(f"{label} must be a sha256 reference")
    return value


def _time(value: Any, label: str) -> tuple[datetime, str]:
    text = _text(value, label, maximum=40)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ApprovalContractError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalContractError(f"{label} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _version(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ApprovalContractError(f"{label} must be a positive integer")
    return value


def build_approval_evidence(
    *,
    request_id: str,
    target_skill_id: str,
    target_skill_version: int,
    package_receipt_sha256: str,
    validated_lifecycle_head_sha256: str,
    items: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    normalized_items: list[dict[str, str]] = []
    for raw in items:
        item = _mapping(raw, "evidence item")
        _exact_fields(item, _EVIDENCE_ITEM_FIELDS, "evidence item")
        label = _text(item.get("label"), "evidence item label", maximum=64)
        if not _EVIDENCE_LABEL.fullmatch(label):
            raise ApprovalContractError("evidence item label must be a safe opaque label")
        normalized_items.append({
            "label": label,
            "sha256": _sha256_ref(item.get("sha256"), "evidence item sha256"),
        })
    if not normalized_items or len(normalized_items) > 16:
        raise ApprovalContractError("evidence must contain 1..16 items")
    labels = [item["label"] for item in normalized_items]
    if labels != sorted(labels) or len(labels) != len(set(labels)):
        raise ApprovalContractError("evidence item labels must be unique and sorted")
    body = {
        "schema_version": APPROVAL_EVIDENCE_SCHEMA_VERSION,
        "request_id": _identifier(request_id, "request_id"),
        "target_skill_id": _skill_identifier(target_skill_id, "target_skill_id"),
        "target_skill_version": _version(target_skill_version, "target_skill_version"),
        "package_receipt_sha256": _sha256_ref(package_receipt_sha256, "package_receipt_sha256"),
        "validated_lifecycle_head_sha256": _sha256_ref(validated_lifecycle_head_sha256, "validated_lifecycle_head_sha256"),
        "lifecycle_state": "VALIDATED",
        "items": normalized_items,
    }
    return {**body, "evidence_digest": _digest(body)}


def validate_approval_evidence(value: Any) -> dict[str, Any]:
    evidence = dict(_mapping(value, "approval evidence"))
    _exact_fields(evidence, _EVIDENCE_FIELDS, "approval evidence")
    if evidence.get("schema_version") != APPROVAL_EVIDENCE_SCHEMA_VERSION:
        raise ApprovalContractError("unsupported approval evidence schema_version")
    rebuilt = build_approval_evidence(
        request_id=evidence.get("request_id"),
        target_skill_id=evidence.get("target_skill_id"),
        target_skill_version=evidence.get("target_skill_version"),
        package_receipt_sha256=evidence.get("package_receipt_sha256"),
        validated_lifecycle_head_sha256=evidence.get("validated_lifecycle_head_sha256"),
        items=evidence.get("items") if isinstance(evidence.get("items"), list) else [],
    )
    if rebuilt != evidence:
        raise ApprovalContractError("approval evidence digest or content mismatch")
    return rebuilt


def build_approval_request(
    *,
    request_id: str,
    requesting_agent: str,
    requesting_principal: str,
    target_skill_id: str,
    target_skill_version: int,
    package_receipt_sha256: str,
    validated_lifecycle_head_sha256: str,
    requested_at: str,
    expires_at: str,
    evidence_digest: str,
) -> dict[str, Any]:
    requested_dt, requested = _time(requested_at, "requested_at")
    expires_dt, expires = _time(expires_at, "expires_at")
    if expires_dt <= requested_dt:
        raise ApprovalContractError("expires_at must be after requested_at")
    return {
        "schema_version": APPROVAL_REQUEST_SCHEMA_VERSION,
        "request_id": _identifier(request_id, "request_id"),
        "request_type": REQUEST_TYPE,
        "requesting_agent": _text(requesting_agent, "requesting_agent", maximum=80),
        "requesting_principal": _text(requesting_principal, "requesting_principal", maximum=128),
        "target_skill_id": _skill_identifier(target_skill_id, "target_skill_id"),
        "target_skill_version": _version(target_skill_version, "target_skill_version"),
        "package_receipt_sha256": _sha256_ref(package_receipt_sha256, "package_receipt_sha256"),
        "validated_lifecycle_head_sha256": _sha256_ref(validated_lifecycle_head_sha256, "validated_lifecycle_head_sha256"),
        "requested_at": requested,
        "expires_at": expires,
        "evidence_digest": _sha256_ref(evidence_digest, "evidence_digest"),
        "requested_action": REQUESTED_ACTION,
        "authority_boundary": AUTHORITY_BOUNDARY,
    }


def validate_approval_request(value: Any) -> dict[str, Any]:
    request = dict(_mapping(value, "approval request"))
    _exact_fields(request, _REQUEST_FIELDS, "approval request")
    if request.get("schema_version") != APPROVAL_REQUEST_SCHEMA_VERSION:
        raise ApprovalContractError("unsupported approval request schema_version")
    if request.get("request_type") != REQUEST_TYPE:
        raise ApprovalContractError("unsupported approval request_type")
    if request.get("requested_action") != REQUESTED_ACTION:
        raise ApprovalContractError("unsupported requested_action")
    if request.get("authority_boundary") != AUTHORITY_BOUNDARY:
        raise ApprovalContractError("approval request authority boundary mismatch")
    rebuilt = build_approval_request(
        request_id=request.get("request_id"),
        requesting_agent=request.get("requesting_agent"),
        requesting_principal=request.get("requesting_principal"),
        target_skill_id=request.get("target_skill_id"),
        target_skill_version=request.get("target_skill_version"),
        package_receipt_sha256=request.get("package_receipt_sha256"),
        validated_lifecycle_head_sha256=request.get("validated_lifecycle_head_sha256"),
        requested_at=request.get("requested_at"),
        expires_at=request.get("expires_at"),
        evidence_digest=request.get("evidence_digest"),
    )
    if rebuilt != request:
        raise ApprovalContractError("approval request is not canonical")
    return rebuilt


def approval_request_sha256(value: Any) -> str:
    return _digest(validate_approval_request(value))


def bind_request_evidence(request_value: Any, evidence_value: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    request = validate_approval_request(request_value)
    evidence = validate_approval_evidence(evidence_value)
    for field in (
        "request_id",
        "target_skill_id",
        "target_skill_version",
        "package_receipt_sha256",
        "validated_lifecycle_head_sha256",
        "evidence_digest",
    ):
        if request.get(field) != evidence.get(field):
            raise ApprovalContractError(f"approval evidence {field} does not match request")
    return request, evidence


def build_current_candidate_binding(request_value: Any, *, observed_at: str) -> dict[str, Any]:
    request = validate_approval_request(request_value)
    _dt, observed = _time(observed_at, "observed_at")
    return {
        "schema_version": CURRENT_BINDING_SCHEMA_VERSION,
        "target_skill_id": request["target_skill_id"],
        "target_skill_version": request["target_skill_version"],
        "package_receipt_sha256": request["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": request["validated_lifecycle_head_sha256"],
        "evidence_digest": request["evidence_digest"],
        "lifecycle_state": "VALIDATED",
        "observed_at": observed,
    }


def validate_current_candidate_binding(value: Any) -> dict[str, Any]:
    binding = dict(_mapping(value, "current candidate binding"))
    _exact_fields(binding, _BINDING_FIELDS, "current candidate binding")
    if binding.get("schema_version") != CURRENT_BINDING_SCHEMA_VERSION:
        raise ApprovalContractError("unsupported current binding schema_version")
    _skill_identifier(binding.get("target_skill_id"), "target_skill_id")
    _version(binding.get("target_skill_version"), "target_skill_version")
    _sha256_ref(binding.get("package_receipt_sha256"), "package_receipt_sha256")
    _sha256_ref(binding.get("validated_lifecycle_head_sha256"), "validated_lifecycle_head_sha256")
    _sha256_ref(binding.get("evidence_digest"), "evidence_digest")
    if binding.get("lifecycle_state") != "VALIDATED":
        raise ApprovalContractError("current candidate lifecycle_state must be VALIDATED")
    _time(binding.get("observed_at"), "observed_at")
    return binding


def require_current_candidate(request_value: Any, binding_value: Any) -> dict[str, Any]:
    request = validate_approval_request(request_value)
    binding = validate_current_candidate_binding(binding_value)
    for field in (
        "target_skill_id",
        "target_skill_version",
        "package_receipt_sha256",
        "validated_lifecycle_head_sha256",
        "evidence_digest",
    ):
        if request[field] != binding[field]:
            raise ApprovalContractError(f"stale candidate binding: {field}")
    return binding


def approval_request_state(request_value: Any, *, as_of: str) -> str:
    request = validate_approval_request(request_value)
    now, _normalized = _time(as_of, "as_of")
    expires, _expires = _time(request["expires_at"], "expires_at")
    return "EXPIRED" if now >= expires else "PENDING"


def _validate_review_identity(identity: Any, request: Mapping[str, Any]) -> ReviewIdentity:
    if not isinstance(identity, ReviewIdentity):
        raise ApprovalContractError("review identity must use the bounded review-only identity contract")
    principal = _text(identity.principal_ref, "reviewer principal", maximum=128)
    if identity.host_authenticated is not False:
        raise ApprovalContractError("protected host authentication is unavailable in P2B0")
    if identity.identity_kind != REVIEW_IDENTITY_KIND or identity.source != REVIEW_IDENTITY_SOURCE:
        raise ApprovalContractError("forged or unsupported reviewer identity")
    if principal in {request["requesting_agent"], request["requesting_principal"]}:
        raise ApprovalContractError("requesting model or principal cannot review its own request")
    return identity


def _receipt_body(
    request: Mapping[str, Any],
    *,
    decision: str,
    reviewed_at: str,
    reviewer_principal: str,
    review_surface: str,
    reason: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_RECEIPT_SCHEMA_VERSION,
        "request_id": request["request_id"],
        "request_sha256": _digest(request),
        "evidence_digest": request["evidence_digest"],
        "decision": decision,
        "state": _DECISION_STATE[decision],
        "reviewed_at": reviewed_at,
        "reviewer_principal": reviewer_principal,
        "reviewer_identity_kind": REVIEW_IDENTITY_KIND,
        "review_surface": review_surface,
        "reason": reason,
        "review_decision_recorded": True,
        "host_authentication_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "execution_authority": "NONE",
        "tool_grants": [],
        "operator_cli_invoked": False,
        "production_registry_mutated": False,
    }


def validate_review_receipt(value: Any, *, request: Mapping[str, Any] | None = None) -> dict[str, Any]:
    receipt = dict(_mapping(value, "review receipt"))
    _exact_fields(receipt, _RECEIPT_FIELDS, "review receipt")
    if receipt.get("schema_version") != REVIEW_RECEIPT_SCHEMA_VERSION:
        raise ApprovalContractError("unsupported review receipt schema_version")
    receipt_id = _sha256_ref(receipt.get("receipt_id"), "receipt_id")
    decision = receipt.get("decision")
    if decision not in DECISIONS or receipt.get("state") != _DECISION_STATE[decision]:
        raise ApprovalContractError("review receipt decision/state mismatch")
    _identifier(receipt.get("request_id"), "request_id")
    _sha256_ref(receipt.get("request_sha256"), "request_sha256")
    _sha256_ref(receipt.get("evidence_digest"), "evidence_digest")
    _time(receipt.get("reviewed_at"), "reviewed_at")
    _text(receipt.get("reviewer_principal"), "reviewer_principal", maximum=128)
    if receipt.get("reviewer_identity_kind") != REVIEW_IDENTITY_KIND:
        raise ApprovalContractError("review receipt identity kind mismatch")
    if receipt.get("review_surface") not in PRESENTATION_SURFACES:
        raise ApprovalContractError("review receipt surface is unsupported")
    reason = receipt.get("reason")
    if reason is not None:
        _text(reason, "reason", maximum=500)
    if decision in {"REJECT", "REQUEST_CHANGES"} and reason is None:
        raise ApprovalContractError("rejection and change decisions require a reason")
    required_false = (
        "host_authentication_verified",
        "authorization_granted",
        "promotion_authorized",
        "operator_cli_invoked",
        "production_registry_mutated",
    )
    if receipt.get("review_decision_recorded") is not True:
        raise ApprovalContractError("review receipt must record review only")
    if any(receipt.get(field) is not False for field in required_false):
        raise ApprovalContractError("review receipt cannot grant authority or perform promotion")
    if receipt.get("execution_authority") != "NONE" or receipt.get("tool_grants") != []:
        raise ApprovalContractError("review receipt cannot widen tools or execution authority")
    body = {key: receipt[key] for key in receipt if key != "receipt_id"}
    if _digest(body) != receipt_id:
        raise ApprovalContractError("review receipt digest mismatch")
    if request is not None:
        bound = validate_approval_request(request)
        if (
            receipt["request_id"] != bound["request_id"]
            or receipt["request_sha256"] != _digest(bound)
            or receipt["evidence_digest"] != bound["evidence_digest"]
        ):
            raise ApprovalContractError("review receipt does not bind the exact request")
    return receipt


class ReviewDecisionLedger:
    """In-memory duplicate guard for one review session; not protected authority."""

    def __init__(self) -> None:
        self._receipts: dict[str, dict[str, Any]] = {}

    def record(
        self,
        request_value: Any,
        evidence_value: Any,
        binding_value: Any,
        *,
        decision: str,
        reviewed_at: str,
        identity: ReviewIdentity,
        review_surface: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        request, _evidence = bind_request_evidence(request_value, evidence_value)
        require_current_candidate(request, binding_value)
        request_digest = _digest(request)
        if request_digest in self._receipts:
            raise ApprovalContractError("review decision replay or duplicate consumption refused")
        if decision not in DECISIONS:
            raise ApprovalContractError("unsupported review decision")
        if review_surface not in PRESENTATION_SURFACES:
            raise ApprovalContractError("unsupported presentation surface")
        reviewer = _validate_review_identity(identity, request)
        reviewed_dt, reviewed = _time(reviewed_at, "reviewed_at")
        requested_dt, _requested = _time(request["requested_at"], "requested_at")
        expires_dt, _expires = _time(request["expires_at"], "expires_at")
        if reviewed_dt < requested_dt:
            raise ApprovalContractError("reviewed_at cannot precede requested_at")
        expired = reviewed_dt >= expires_dt
        if decision == "EXPIRE" and not expired:
            raise ApprovalContractError("request cannot expire before expires_at")
        if decision != "EXPIRE" and expired:
            raise ApprovalContractError("expired approval request cannot be reviewed")
        normalized_reason: str | None = None
        if reason is not None:
            normalized_reason = _text(reason, "reason", maximum=500)
        if decision in {"REJECT", "REQUEST_CHANGES"} and normalized_reason is None:
            raise ApprovalContractError("rejection and change decisions require a reason")
        body = _receipt_body(
            request,
            decision=decision,
            reviewed_at=reviewed,
            reviewer_principal=reviewer.principal_ref,
            review_surface=review_surface,
            reason=normalized_reason,
        )
        receipt = {"receipt_id": _digest(body), **body}
        validated = validate_review_receipt(receipt, request=request)
        self._receipts[request_digest] = deepcopy(validated)
        return deepcopy(validated)


def reconcile_review_state(
    request_value: Any,
    receipts: Iterable[Mapping[str, Any]],
    *,
    as_of: str,
) -> str:
    request = validate_approval_request(request_value)
    items = list(receipts)
    if not items:
        return approval_request_state(request, as_of=as_of)
    if len(items) != 1:
        return "RECONCILIATION_REQUIRED"
    try:
        return validate_review_receipt(items[0], request=request)["state"]
    except ApprovalContractError:
        return "RECONCILIATION_REQUIRED"


def project_approval_request(
    request_value: Any,
    evidence_value: Any,
    *,
    surface: str,
    as_of: str,
) -> dict[str, Any]:
    request, evidence = bind_request_evidence(request_value, evidence_value)
    if surface not in PRESENTATION_SURFACES:
        raise ApprovalContractError("unsupported presentation surface")
    return {
        "schema_version": PRESENTATION_SCHEMA_VERSION,
        "surface": surface,
        "request_id": request["request_id"],
        "request_sha256": _digest(request),
        "requesting_agent": request["requesting_agent"],
        "requested_action": request["requested_action"],
        "target_skill_id": request["target_skill_id"],
        "target_skill_version": request["target_skill_version"],
        "package_receipt_sha256": request["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": request["validated_lifecycle_head_sha256"],
        "evidence_digest": request["evidence_digest"],
        "evidence_items": deepcopy(evidence["items"]),
        "expires_at": request["expires_at"],
        "state": approval_request_state(request, as_of=as_of),
        "available_decisions": sorted(DECISIONS),
        "review_only": True,
        "transport_authority": "NONE",
        "host_authentication_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "execution_authority": "NONE",
        "tool_grants": [],
    }


def issue_operator_authorization_from_review(_receipt: Any) -> None:
    """Permanent P2B0 fail-closed seam: review receipts are not authorization."""

    raise ApprovalContractError(
        "P2B0 review receipts cannot create OperatorAuthorization; protected host authentication is required"
    )
