"""Codex native user-verification bridge for one-shot promotion approval.

This module is host-only code.  It prepares a bounded challenge for Codex
app-server's native ``userVerification/verify`` RPC and accepts a proof only
after a protected verifier maps the enrolled device credential to the
authenticated Codex account.  The verifier, host key, registry, and completion
call must all remain outside model tool permissions.

MCP confirmation or chat text is deliberately not accepted here.  Codex user
verification is experimental and this bridge does not enable it, enroll a
credential, register a public key, or install a host service.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import secrets
from typing import Any, Callable, Mapping

from .generic_host_adapter import (
    GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
    GENERIC_HOST_APPROVAL_EVENT_KIND,
    complete_generic_host_approval,
    prepare_generic_host_approval,
)


CODEX_APPROVAL_BRIDGE_SCHEMA_VERSION = "0.1"
CODEX_APPROVAL_CHALLENGE_KIND = "KNOWLEDGE_FORGE_CODEX_USER_VERIFICATION"
CODEX_USER_VERIFICATION_METHOD = "userVerification/verify"

_CHALLENGE_FIELDS = frozenset({
    "schema_version",
    "kind",
    "adapter_id",
    "action",
    "approval_id",
    "request_sha256",
    "skill_id",
    "skill_version",
    "issued_at",
    "expires_at",
    "nonce",
    "display_sha256",
})
_RPC_FIELDS = frozenset({"method", "params"})
_RPC_PARAM_FIELDS = frozenset({"challenge", "title", "description"})
_RPC_RESULT_FIELDS = frozenset({"proof"})
_PROOF_FIELDS = frozenset({"credentialId", "signature"})
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,255}$")
_SIGNATURE_RE = re.compile(r"^[A-Za-z0-9_-]{64,256}$")


class CodexApprovalBridgeError(ValueError):
    """Codex verification data cannot authorize promotion."""


CodexProofVerifier = Callable[[bytes, str, str], str]


def _exact(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != set(fields):
        raise CodexApprovalBridgeError(f"{label} fields must match exact contract")


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
        raise CodexApprovalBridgeError("Codex approval data is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _time(value: Any, label: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise CodexApprovalBridgeError(f"{label} must be bounded RFC3339 text")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CodexApprovalBridgeError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CodexApprovalBridgeError(f"{label} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: Any, label: str, *, maximum: int = 4096) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        raise CodexApprovalBridgeError(f"{label} must be unpadded base64url")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise CodexApprovalBridgeError(f"{label} must be unpadded base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise CodexApprovalBridgeError(f"{label} must be unpadded base64url") from exc
    if not decoded or len(decoded) > maximum:
        raise CodexApprovalBridgeError(f"{label} decoded size must be 1..{maximum} bytes")
    return decoded


def _display(card: Mapping[str, Any], expires_at: str) -> tuple[str, str]:
    title = "Approve Knowledge Forge promotion"
    description = (
        "Promote exactly this validated knowledge skill once.\n"
        f"Skill: {card['skill_id']}\n"
        f"Version: {card['skill_version']}\n"
        f"Request: {card['request_sha256']}\n"
        f"Expires: {expires_at}"
    )
    if len(title.encode("utf-8")) > 256 or len(description.encode("utf-8")) > 4096:
        raise CodexApprovalBridgeError("Codex approval display exceeds native bounds")
    return title, description


def prepare_codex_native_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    observed_at: str,
    lifetime_seconds: int = 300,
) -> dict[str, Any]:
    """Prepare a non-authorizing Codex native verification challenge."""

    if (
        isinstance(lifetime_seconds, bool)
        or not isinstance(lifetime_seconds, int)
        or not 1 <= lifetime_seconds <= 300
    ):
        raise CodexApprovalBridgeError("challenge lifetime must be 1..300 seconds")
    now, issued_at = _time(observed_at, "observed_at")
    card = prepare_generic_host_approval(
        request_dir,
        bundle,
        adapter_id="codex",
        observed_at=issued_at,
    )
    request_expiry, _ = _time(card["expires_at"], "request expires_at")
    expiry = min(request_expiry, now + timedelta(seconds=lifetime_seconds))
    if expiry <= now:
        raise CodexApprovalBridgeError("approval request expires before challenge")
    expires_at = expiry.isoformat().replace("+00:00", "Z")
    nonce = secrets.token_hex(32)
    if not _HEX_64_RE.fullmatch(nonce):
        raise CodexApprovalBridgeError("host randomness returned an invalid nonce")
    approval_id = f"codex:{nonce}"
    title, description = _display(card, expires_at)
    challenge_record = {
        "schema_version": CODEX_APPROVAL_BRIDGE_SCHEMA_VERSION,
        "kind": CODEX_APPROVAL_CHALLENGE_KIND,
        "adapter_id": "codex",
        "action": "PROMOTE_VALIDATED_SKILL_ONCE",
        "approval_id": approval_id,
        "request_sha256": card["request_sha256"],
        "skill_id": card["skill_id"],
        "skill_version": card["skill_version"],
        "issued_at": issued_at,
        "expires_at": expires_at,
        "nonce": nonce,
        "display_sha256": _digest({"title": title, "description": description}),
    }
    challenge_bytes = _canonical_bytes(challenge_record)
    if len(challenge_bytes) > 4096:
        raise CodexApprovalBridgeError("Codex verification challenge exceeds 4096 bytes")
    return {
        **card,
        "codex_user_verification": {
            "method": CODEX_USER_VERIFICATION_METHOD,
            "params": {
                "challenge": _b64url_encode(challenge_bytes),
                "title": title,
                "description": description,
            },
        },
        "challenge_record": challenge_record,
        "authorization_granted": False,
        "promotion_performed": False,
    }


def _validate_prepared(
    prepared: Mapping[str, Any],
    *,
    current_card: Mapping[str, Any],
    now: str,
) -> tuple[dict[str, Any], bytes]:
    if not isinstance(prepared, Mapping):
        raise CodexApprovalBridgeError("prepared Codex approval must be a mapping")
    record_value = prepared.get("challenge_record")
    rpc_value = prepared.get("codex_user_verification")
    if not isinstance(record_value, Mapping) or not isinstance(rpc_value, Mapping):
        raise CodexApprovalBridgeError("prepared Codex approval is incomplete")
    record = dict(record_value)
    rpc = dict(rpc_value)
    _exact(record, _CHALLENGE_FIELDS, "Codex challenge record")
    _exact(rpc, _RPC_FIELDS, "Codex verification RPC")
    params_value = rpc.get("params")
    if not isinstance(params_value, Mapping):
        raise CodexApprovalBridgeError("Codex verification params must be a mapping")
    params = dict(params_value)
    _exact(params, _RPC_PARAM_FIELDS, "Codex verification params")
    if rpc.get("method") != CODEX_USER_VERIFICATION_METHOD:
        raise CodexApprovalBridgeError("unexpected Codex verification method")
    if (
        record.get("schema_version") != CODEX_APPROVAL_BRIDGE_SCHEMA_VERSION
        or record.get("kind") != CODEX_APPROVAL_CHALLENGE_KIND
        or record.get("adapter_id") != "codex"
        or record.get("action") != "PROMOTE_VALIDATED_SKILL_ONCE"
    ):
        raise CodexApprovalBridgeError("unsupported Codex approval challenge")
    if record.get("request_sha256") != current_card["request_sha256"]:
        raise CodexApprovalBridgeError("Codex challenge does not bind current request")
    if (
        record.get("skill_id") != current_card["skill_id"]
        or record.get("skill_version") != current_card["skill_version"]
    ):
        raise CodexApprovalBridgeError("Codex challenge does not bind current skill")
    nonce = record.get("nonce")
    if not isinstance(nonce, str) or not _HEX_64_RE.fullmatch(nonce):
        raise CodexApprovalBridgeError("Codex challenge nonce is invalid")
    if record.get("approval_id") != f"codex:{nonce}":
        raise CodexApprovalBridgeError("Codex approval ID does not bind its nonce")
    issued, issued_text = _time(record.get("issued_at"), "issued_at")
    expires, expires_text = _time(record.get("expires_at"), "expires_at")
    current, _ = _time(now, "now")
    if issued_text != record["issued_at"] or expires_text != record["expires_at"]:
        raise CodexApprovalBridgeError("Codex challenge times must be canonical UTC")
    if current < issued or current >= expires or expires - issued > timedelta(seconds=300):
        raise CodexApprovalBridgeError("Codex approval challenge is stale or expired")
    title, description = _display(current_card, record["expires_at"])
    if params.get("title") != title or params.get("description") != description:
        raise CodexApprovalBridgeError("Codex approval display changed after preparation")
    if record.get("display_sha256") != _digest({"title": title, "description": description}):
        raise CodexApprovalBridgeError("Codex challenge does not bind its display")
    encoded_challenge = params.get("challenge")
    challenge_bytes = _b64url_decode(encoded_challenge, "Codex challenge")
    if challenge_bytes != _canonical_bytes(record):
        raise CodexApprovalBridgeError("Codex challenge bytes do not match the record")
    return record, challenge_bytes


def complete_codex_native_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    prepared: Mapping[str, Any],
    verification_result: Mapping[str, Any],
    verify_proof: CodexProofVerifier,
    registry_path: str | Path,
    host_key_path: str | Path,
    now: str,
) -> dict[str, Any]:
    """Verify one Codex device proof and complete one protected promotion."""

    current_card = prepare_generic_host_approval(
        request_dir, bundle, adapter_id="codex", observed_at=now
    )
    record, challenge_bytes = _validate_prepared(
        prepared, current_card=current_card, now=now
    )
    if not isinstance(verification_result, Mapping):
        raise CodexApprovalBridgeError(
            "Codex user-verification result is required; chat text cannot authorize"
        )
    result = dict(verification_result)
    _exact(result, _RPC_RESULT_FIELDS, "Codex verification result")
    proof_value = result.get("proof")
    if not isinstance(proof_value, Mapping):
        raise CodexApprovalBridgeError("Codex verification proof must be a mapping")
    proof = dict(proof_value)
    _exact(proof, _PROOF_FIELDS, "Codex verification proof")
    credential_id = proof.get("credentialId")
    signature = proof.get("signature")
    if not isinstance(credential_id, str) or not _CREDENTIAL_RE.fullmatch(credential_id):
        raise CodexApprovalBridgeError("Codex credential ID is invalid")
    if not isinstance(signature, str) or not _SIGNATURE_RE.fullmatch(signature):
        raise CodexApprovalBridgeError("Codex device signature is invalid")
    _b64url_decode(signature, "Codex device signature", maximum=256)
    try:
        reviewer_principal = verify_proof(
            challenge_bytes, credential_id, signature
        )
    except Exception as exc:
        raise CodexApprovalBridgeError(
            "Codex device proof was not verified by the protected host"
        ) from exc
    if not isinstance(reviewer_principal, str) or not reviewer_principal:
        raise CodexApprovalBridgeError(
            "protected Codex verifier did not return an authenticated principal"
        )
    event = {
        "schema_version": GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
        "kind": GENERIC_HOST_APPROVAL_EVENT_KIND,
        "adapter_id": "codex",
        "approval_id": record["approval_id"],
        "request_sha256": record["request_sha256"],
        "decision": "approve-once",
        "reviewer_principal": reviewer_principal,
        "decided_at": now,
        "authentication": {
            "mechanism": "codex-user-verification",
            "one_shot": True,
            "bypass_active": False,
            "review_surface": "CODEX_NATIVE_USER_VERIFICATION",
        },
    }
    completed = complete_generic_host_approval(
        request_dir,
        bundle,
        host_event=event,
        adapter_id="codex",
        registry_path=registry_path,
        host_key_path=host_key_path,
        now=now,
    )
    return {
        **completed,
        "interaction": "CODEX_NATIVE_USER_VERIFICATION",
        "credential_id": credential_id,
        "device_proof_verified": True,
    }
