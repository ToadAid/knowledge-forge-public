"""Codex MCP elicitation transport for native Knowledge Forge approval.

Codex forwards an MCP server's ``elicitation/create`` request with mode
``openai/userVerification`` to its native client.  The client displays the
bounded title and description, obtains local user verification, and returns a
device proof in the accepted elicitation content.

This module maps that transport onto :mod:`codex_approval_bridge`.  It does not
turn an ordinary MCP tool confirmation into authority.  The completion call,
proof verifier, host key, decision ledger, and governed registry must execute
in a protected host process outside model permissions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .codex_approval_bridge import (
    CodexProofVerifier,
    complete_codex_native_approval,
    prepare_codex_native_approval,
)


CODEX_MCP_ELICITATION_METHOD = "elicitation/create"
CODEX_MCP_USER_VERIFICATION_MODE = "openai/userVerification"

_REQUEST_FIELDS = frozenset({"jsonrpc", "id", "method", "params"})
_PARAM_FIELDS = frozenset({"mode", "challenge", "title", "description"})
_RESPONSE_FIELDS = frozenset({"jsonrpc", "id", "result"})
_RESULT_FIELDS = frozenset({"action", "content"})
_PROOF_FIELDS = frozenset({"credentialId", "signature"})


class CodexMcpApprovalError(ValueError):
    """A Codex MCP elicitation cannot authorize promotion."""


CodexMcpElicitationChannel = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise CodexMcpApprovalError(f"{label} must be a mapping")
    return dict(value)


def _exact(value: Mapping[str, Any], fields: frozenset[str], label: str) -> None:
    if set(value) != set(fields):
        raise CodexMcpApprovalError(f"{label} fields must match exact contract")


def prepare_codex_mcp_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    observed_at: str,
    lifetime_seconds: int = 300,
) -> dict[str, Any]:
    """Prepare one non-authorizing native verification elicitation."""

    prepared = prepare_codex_native_approval(
        request_dir,
        bundle,
        observed_at=observed_at,
        lifetime_seconds=lifetime_seconds,
    )
    native = _mapping(
        prepared["codex_user_verification"], "Codex user verification request"
    )
    params = _mapping(native.get("params"), "Codex user verification params")
    request_id = prepared["challenge_record"]["approval_id"]
    elicitation = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": CODEX_MCP_ELICITATION_METHOD,
        "params": {
            "mode": CODEX_MCP_USER_VERIFICATION_MODE,
            "challenge": params["challenge"],
            "title": params["title"],
            "description": params["description"],
        },
    }
    return {
        "prepared": prepared,
        "elicitation_request": elicitation,
        "status": "AWAITING_CODEX_NATIVE_USER_VERIFICATION",
        "authorization_granted": False,
        "promotion_performed": False,
    }


def _proof_from_response(
    response: Mapping[str, Any], *, expected_request_id: str
) -> dict[str, Any]:
    envelope = _mapping(response, "Codex MCP elicitation response")
    _exact(envelope, _RESPONSE_FIELDS, "Codex MCP elicitation response")
    if envelope.get("jsonrpc") != "2.0":
        raise CodexMcpApprovalError("Codex MCP response must use JSON-RPC 2.0")
    if envelope.get("id") != expected_request_id:
        raise CodexMcpApprovalError("Codex MCP response ID does not bind the approval")
    result = _mapping(envelope.get("result"), "Codex MCP elicitation result")
    action = result.get("action")
    if action in {"decline", "cancel"}:
        if set(result) not in ({"action"}, _RESULT_FIELDS) or (
            "content" in result and result["content"] is not None
        ):
            raise CodexMcpApprovalError(
                "declined Codex MCP elicitation has unexpected content"
            )
        outcome = "declined" if action == "decline" else "cancelled"
        raise CodexMcpApprovalError(f"Codex native approval was {outcome}")
    if action != "accept":
        raise CodexMcpApprovalError("Codex MCP elicitation action is invalid")
    _exact(result, _RESULT_FIELDS, "accepted Codex MCP elicitation result")
    proof = _mapping(result.get("content"), "Codex MCP verification proof")
    _exact(proof, _PROOF_FIELDS, "Codex MCP verification proof")
    return {"proof": proof}


def complete_codex_mcp_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    prepared_flow: Mapping[str, Any],
    elicitation_response: Mapping[str, Any],
    verify_proof: CodexProofVerifier,
    registry_path: str | Path,
    host_key_path: str | Path,
    now: str,
) -> dict[str, Any]:
    """Consume one accepted native elicitation through the protected bridge."""

    flow = _mapping(prepared_flow, "prepared Codex MCP approval")
    if set(flow) != {
        "prepared",
        "elicitation_request",
        "status",
        "authorization_granted",
        "promotion_performed",
    }:
        raise CodexMcpApprovalError(
            "prepared Codex MCP approval fields must match exact contract"
        )
    if (
        flow.get("status") != "AWAITING_CODEX_NATIVE_USER_VERIFICATION"
        or flow.get("authorization_granted") is not False
        or flow.get("promotion_performed") is not False
    ):
        raise CodexMcpApprovalError("prepared Codex MCP approval state is invalid")
    prepared = _mapping(flow.get("prepared"), "prepared native approval")
    request = _mapping(flow.get("elicitation_request"), "Codex MCP elicitation")
    _exact(request, _REQUEST_FIELDS, "Codex MCP elicitation")
    params = _mapping(request.get("params"), "Codex MCP elicitation params")
    _exact(params, _PARAM_FIELDS, "Codex MCP elicitation params")
    native = _mapping(
        prepared.get("codex_user_verification"), "native verification request"
    )
    native_params = _mapping(native.get("params"), "native verification params")
    record = _mapping(prepared.get("challenge_record"), "native challenge record")
    expected_request_id = record.get("approval_id")
    if not isinstance(expected_request_id, str) or not expected_request_id:
        raise CodexMcpApprovalError("native approval ID is missing")
    if (
        request.get("jsonrpc") != "2.0"
        or request.get("id") != expected_request_id
        or request.get("method") != CODEX_MCP_ELICITATION_METHOD
        or params.get("mode") != CODEX_MCP_USER_VERIFICATION_MODE
        or params.get("challenge") != native_params.get("challenge")
        or params.get("title") != native_params.get("title")
        or params.get("description") != native_params.get("description")
    ):
        raise CodexMcpApprovalError(
            "Codex MCP elicitation changed after protected preparation"
        )
    verification_result = _proof_from_response(
        elicitation_response, expected_request_id=expected_request_id
    )
    completed = complete_codex_native_approval(
        request_dir,
        bundle,
        prepared=prepared,
        verification_result=verification_result,
        verify_proof=verify_proof,
        registry_path=registry_path,
        host_key_path=host_key_path,
        now=now,
    )
    return {
        **completed,
        "transport": "CODEX_MCP_OPENAI_USER_VERIFICATION",
    }


def run_codex_mcp_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    observed_at: str,
    send_elicitation: CodexMcpElicitationChannel,
    verify_proof: CodexProofVerifier,
    registry_path: str | Path,
    host_key_path: str | Path,
    now: str,
    lifetime_seconds: int = 300,
) -> dict[str, Any]:
    """Run the host-owned native UI exchange and protected completion.

    ``send_elicitation`` is supplied by the Codex MCP host transport.  The
    model is not given the callback, returned proof, proof verifier, or
    completion capability.
    """

    flow = prepare_codex_mcp_approval(
        request_dir,
        bundle,
        observed_at=observed_at,
        lifetime_seconds=lifetime_seconds,
    )
    try:
        response = send_elicitation(flow["elicitation_request"])
    except Exception as exc:
        raise CodexMcpApprovalError(
            "Codex native verification transport failed closed"
        ) from exc
    return complete_codex_mcp_approval(
        request_dir,
        bundle,
        prepared_flow=flow,
        elicitation_response=response,
        verify_proof=verify_proof,
        registry_path=registry_path,
        host_key_path=host_key_path,
        now=now,
    )
