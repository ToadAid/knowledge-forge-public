"""Host-facing inline approval adapter with no end-user command surface.

The requesting agent may call :func:`prepare_inline_approval` to produce a
bounded card for its surrounding host.  That result is deliberately
non-authorizing.  Only the host, after an independently authenticated native
Approve action, may sign a decision and call :func:`complete_inline_promotion`.
Ordinary chat text and agent-created pseudo-terminals never enter that path.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

from .approval import (
    approval_request_sha256,
    approval_request_state,
    bind_request_evidence,
    project_approval_request,
    require_current_candidate,
)
from .approval_host_cli import (
    _bind_candidate_to_request,
    _mapping_from_bytes,
    _read_json_object,
    _snapshot_package,
    run_approval,
)
from .candidate_preview import preview_validated_candidate
from .validate import validate_or_raise


INLINE_APPROVAL_SCHEMA_VERSION = "0.1"
INLINE_APPROVAL_KIND = "KNOWLEDGE_FORGE_INLINE_HOST_APPROVAL_REQUEST"


class InlineApprovalError(ValueError):
    """The inline host request or protected completion was invalid."""


def _validated_inputs(
    request_dir: Path, bundle: Path, *, observed_at: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    request = _read_json_object(
        request_dir / "approval-request.json", "approval request"
    )
    evidence = _read_json_object(
        request_dir / "approval-evidence.json", "approval evidence"
    )
    binding = _read_json_object(
        request_dir / "current-candidate.json", "current candidate binding"
    )
    request, evidence = bind_request_evidence(request, evidence)
    require_current_candidate(request, binding)
    if approval_request_state(request, as_of=observed_at) != "PENDING":
        raise InlineApprovalError("approval request is expired or not pending")

    files = _snapshot_package(bundle / "package")
    validate_or_raise(files)
    manifest = _mapping_from_bytes(files, "manifest.json")
    source_receipt = _mapping_from_bytes(files, "SOURCE_RECEIPT.json")
    lifecycle = _read_json_object(
        bundle / "lifecycle.validated.json",
        "validated lifecycle",
        maximum_bytes=16 * 1024 * 1024,
    )
    _bind_candidate_to_request(manifest, lifecycle, source_receipt, request)
    preview = preview_validated_candidate(files, lifecycle, observed_at=observed_at)
    return request, evidence, manifest, preview


def prepare_inline_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Return a host-renderable card; never return promotion authority."""

    request_dir = Path(request_dir)
    bundle = Path(bundle)
    request, evidence, manifest, preview = _validated_inputs(
        request_dir, bundle, observed_at=observed_at
    )
    projection = project_approval_request(
        request, evidence, surface="DESKTOP_CARD", as_of=observed_at
    )
    return {
        "schema_version": INLINE_APPROVAL_SCHEMA_VERSION,
        "kind": INLINE_APPROVAL_KIND,
        "status": "AWAITING_NATIVE_HOST_APPROVAL",
        "request_sha256": approval_request_sha256(request),
        "skill_id": preview["skill_id"],
        "skill_version": preview["skill_version"],
        "title": manifest.get("title", preview["skill_id"]),
        "source_count": preview["source_count"],
        "unit_count": preview["unit_count"],
        "claim_count": preview["claim_count"],
        "source_corpus_coverage": preview["source_corpus_coverage"],
        "coverage_warning": preview["warning"],
        "package_receipt_sha256": preview["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": preview[
            "validated_lifecycle_head_sha256"
        ],
        "evidence_items": projection["evidence_items"],
        "expires_at": projection["expires_at"],
        "actions": ["APPROVE_AND_PROMOTE", "REJECT", "VIEW_DETAILS"],
        "approval_channel": "NATIVE_HOST_UI_EVENT_OUTSIDE_AGENT_CHANNEL",
        "chat_text_can_authorize": False,
        "agent_can_authorize": False,
        "agent_can_promote": False,
        "authorization_granted": False,
        "promotion_performed": False,
        "tool_grants": [],
    }


def _immutable_output_paths(
    registry_path: Path, request: Mapping[str, Any]
) -> tuple[Path, Path]:
    state_root = registry_path.absolute().parent
    request_hex = approval_request_sha256(request).removeprefix("sha256:")
    skill_hex = sha256(str(request["target_skill_id"]).encode("utf-8")).hexdigest()
    version = request["target_skill_version"]
    lifecycle = (
        state_root / "lifecycles"
        / f"{skill_hex}.v{version}.{request_hex[:16]}.json"
    )
    receipt = state_root / "receipts" / f"{request_hex}.json"
    return lifecycle, receipt


def complete_inline_promotion(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    signed_host_decision: Mapping[str, Any],
    registry_path: str | Path,
    host_key_path: str | Path,
    now: str,
) -> dict[str, Any]:
    """Complete only a signed native-host decision using immutable outputs.

    The host must keep its signing key and this function outside agent tool
    permissions.  A chat string such as ``APPROVE`` is intentionally not an
    accepted parameter.
    """

    if not isinstance(signed_host_decision, Mapping):
        raise InlineApprovalError(
            "a signed decision from the native host is required; chat text cannot authorize"
        )
    request_dir = Path(request_dir)
    bundle = Path(bundle)
    registry_path = Path(registry_path)
    request, _evidence, _manifest, _preview = _validated_inputs(
        request_dir, bundle, observed_at=now
    )
    lifecycle_out, receipt_out = _immutable_output_paths(registry_path, request)
    result = run_approval(
        request_dir,
        decision=dict(signed_host_decision),
        bundle=bundle,
        registry_path=registry_path,
        host_key_path=Path(host_key_path),
        promoted_lifecycle_out=lifecycle_out,
        receipt_out=receipt_out,
        now=now,
    )
    return {
        **result,
        "interaction": "INLINE_NATIVE_HOST_APPROVAL",
        "user_command_required": False,
        "chat_text_authorized": False,
    }
