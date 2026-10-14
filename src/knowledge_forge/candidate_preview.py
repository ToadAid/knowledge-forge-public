"""Read-only, bounded VALIDATED candidate preview for the existing review contract.

This module cannot authorize, review, promote, or create review receipts.  The
in-memory request projection is a demonstration of the existing P2B0 schema,
not a current host-observed candidate or a live operator approval request.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .approval import (
    ApprovalContractError,
    build_approval_evidence,
    build_approval_request,
    project_approval_request,
)
from .cli import KnowledgeForgeCliError, _snapshot_package, _load_json_mapping
from .lifecycle import validate_lifecycle_record
from .validate import validate_skill_package

MAX_LIFECYCLE_BYTES = 2 * 1024 * 1024


def _sha(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False) + "\n").encode("utf-8")


def _file_mapping(files: Mapping[str, bytes], name: str) -> dict[str, Any]:
    data = files.get(name)
    if data is None:
        raise ApprovalContractError(f"candidate missing {name}")
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalContractError(f"candidate {name} must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ApprovalContractError(f"candidate {name} must be a JSON object")
    return value


def _clock(value: str) -> tuple[datetime, str]:
    if not isinstance(value, str):
        raise ApprovalContractError("preview clock must be an RFC3339 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ApprovalContractError("preview clock must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalContractError("preview clock must have timezone")
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def preview_validated_candidate(
    files: Mapping[str, bytes],
    lifecycle: Mapping[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Validate *actual* candidate evidence; return metadata, never bodies."""
    report = validate_skill_package(files)
    if not report.get("valid") or report.get("errors") or report.get("security_findings"):
        raise ApprovalContractError("candidate package validation refused")
    if not isinstance(lifecycle, Mapping):
        raise ApprovalContractError("candidate lifecycle must be a mapping")
    lifecycle_report = validate_lifecycle_record(lifecycle)
    if not lifecycle_report.get("valid"):
        raise ApprovalContractError("candidate lifecycle validation refused")
    if lifecycle.get("state") != "VALIDATED":
        raise ApprovalContractError("candidate must have VALIDATED lifecycle")

    manifest = _file_mapping(files, "manifest.json")
    receipt = _file_mapping(files, "SOURCE_RECEIPT.json")
    claim_index = _file_mapping(files, "claim-index.json")
    units = manifest.get("units")
    if not isinstance(units, list) or not units:
        raise ApprovalContractError("candidate manifest needs units")

    binding = (
        "skill_id", "skill_version", "knowledge_id", "candidate_sha256", "source_ids"
    )
    if any(lifecycle.get(k) != manifest.get(k) for k in binding):
        raise ApprovalContractError("candidate lifecycle/manifest binding mismatch")
    if lifecycle.get("package_receipt_sha256") != _sha(_canonical_bytes(receipt)):
        raise ApprovalContractError("candidate receipt/lifecycle hash mismatch")
    unit_ids = [u.get("unit_id") for u in units]
    if lifecycle.get("unit_ids") != unit_ids:
        raise ApprovalContractError("candidate lifecycle/unit identity mismatch")
    if manifest.get("authority") != "KNOWLEDGE_ONLY":
        raise ApprovalContractError("candidate cannot grant authority")
    events = lifecycle.get("events")
    if not isinstance(events, list) or not events or not isinstance(events[-1], dict):
        raise ApprovalContractError("candidate lifecycle head missing")
    head = events[-1].get("event_sha256")
    if not isinstance(head, str) or not head.startswith("sha256:"):
        raise ApprovalContractError("candidate lifecycle head invalid")

    now, normalized_at = _clock(observed_at)
    expires = (now + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    # Use only exact bounded digests, not source bodies, file paths or user secrets.
    items = [
        {"label": "claim-index", "sha256": _sha(files["claim-index.json"])},
        {"label": "manifest", "sha256": _sha(files["manifest.json"])},
        {"label": "source-receipt", "sha256": _sha(files["SOURCE_RECEIPT.json"])},
        {"label": "validated-lifecycle", "sha256": _sha(_canonical_bytes(lifecycle))},
    ]
    skill_id = manifest["skill_id"]
    request_id = "preview." + sha256((skill_id + head).encode("utf-8")).hexdigest()[:24]
    evidence = build_approval_evidence(
        request_id=request_id,
        target_skill_id=skill_id,
        target_skill_version=manifest["skill_version"],
        package_receipt_sha256=lifecycle["package_receipt_sha256"],
        validated_lifecycle_head_sha256=head,
        items=items,
    )
    request = build_approval_request(
        request_id=request_id,
        requesting_agent="Knowledge Forge read-only preview",
        requesting_principal="unauthenticated-preview-not-a-request",
        target_skill_id=skill_id,
        target_skill_version=manifest["skill_version"],
        package_receipt_sha256=lifecycle["package_receipt_sha256"],
        validated_lifecycle_head_sha256=head,
        requested_at=normalized_at,
        expires_at=expires,
        evidence_digest=evidence["evidence_digest"],
    )
    projection = project_approval_request(
        request, evidence, surface="TERMINAL_TUI", as_of=normalized_at
    )
    claims = claim_index.get("claims")
    if not isinstance(claims, list):
        raise ApprovalContractError("candidate missing claim index")
    keys = [unit.get("claim_key") for unit in units]
    if any(not isinstance(key, str) for key in keys):
        raise ApprovalContractError("candidate missing claim keys")
    return {
        "schema_version": "0.1",
        "command": "preview-validated",
        "kind": "READ_ONLY_VALIDATED_CANDIDATE_PREVIEW_NOT_AUTHORIZATION",
        "skill_id": skill_id,
        "skill_version": manifest["skill_version"],
        "knowledge_id": manifest["knowledge_id"],
        "lifecycle_state": "VALIDATED",
        "unit_count": len(units),
        "claim_count": len(claims),
        "source_count": len(manifest["source_ids"]),
        "source_corpus_coverage": "UNKNOWN_FROM_PACKAGE_DO_NOT_CLAIM_COMPLETE_CORPUS",
        "claim_keys_first_10": keys[:10],
        "artifact_count": report["artifact_count"],
        "package_receipt_sha256": lifecycle["package_receipt_sha256"],
        "validated_lifecycle_head_sha256": head,
        "review_contract_projection_demo_only": projection,
        "review_request_written": False,
        "review_receipt_written": False,
        "host_authentication_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "operator_cli_invoked": False,
        "production_registry_mutated": False,
        "tool_grants": [],
        "execution_authority": "NONE",
        "warning": (
            "PREVIEW ONLY. This projection is not a real approval request, "
            "review receipt, authenticated human authorization or promoted registry truth. "
            "Package units do not prove full source-corpus coverage."
        ),
    }


def preview_validated_paths(
    package_root: str, validated_lifecycle_path: str, *, observed_at: str
) -> dict[str, Any]:
    path = Path(validated_lifecycle_path)
    if path.is_symlink() or not path.is_file():
        raise ApprovalContractError("validated lifecycle must be a regular non-symlink file")
    if path.stat().st_size > MAX_LIFECYCLE_BYTES:
        raise ApprovalContractError("validated lifecycle exceeds size limit")
    try:
        files = _snapshot_package(package_root)
        lifecycle = _load_json_mapping(validated_lifecycle_path, "validated lifecycle")
    except (KnowledgeForgeCliError, OSError, UnicodeDecodeError) as exc:
        raise ApprovalContractError("cannot read candidate safely: " + str(exc)) from exc
    return preview_validated_candidate(files, lifecycle, observed_at=observed_at)
