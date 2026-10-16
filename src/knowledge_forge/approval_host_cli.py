"""Single-action approval host CLI for real, authenticated promotion.

`knowledge-forge-approve <from-dir> --bundle <dir> --registry <path>
--host-key <keyfile> --promoted-lifecycle-out <path> --receipt-out <path>`

This is the trusted-host surface: the process (ToadAid-app backend or the
human administrator's shell) owns the host key and the ledger directory and
must never be invoked from the agent process. One invocation takes the staged
request artifacts plus the host's signed decision, verifies everything, and
performs the actual PROMOTED registry transition through the unchanged
engine in ``operator_promotion``. There is no simulation path: if verification
fails, nothing is promoted and the error says which dependency is missing.
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .lifecycle import evidence_sha256
from .operator_promotion import (
    OperatorPromotionError,
    AUTHORIZATION_BOUNDARY,
    OPERATOR_PROMOTION_SCHEMA_VERSION,
    promote_validated_skill,
    persist_operator_promotion,
)
from .approval import (
    ApprovalContractError,
    bind_request_evidence,
    require_current_candidate,
)
from .approval_host import (
    ApprovalHostError,
    authorize_from_host_decision,
    decision_sha256,
    read_host_key_file,
)
from .registry import SkillRegistryError, load_skill_registry
from .trust_wall import MAX_PACKAGE_BYTES, MAX_PACKAGE_FILES
from .validate import PackageValidationError, validate_or_raise

_APPROVE_FROM_DIR_FILES = (
    "approval-request.json",
    "approval-evidence.json",
    "current-candidate.json",
    "approval-host-decision.json",
)


class ApprovalHostCliError(ValueError):
    """Raised when the single-action approval flow refuses before promoting."""


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or any(
        part.is_symlink() for part in path.absolute().parents
    ) or path.is_symlink():
        raise ApprovalHostCliError(f"{label} is missing or not a regular file: {path}")
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalHostCliError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ApprovalHostCliError(f"{label} must contain a JSON object: {path}")
    return payload


def _read_host_key(path: Path) -> bytes:
    return read_host_key_file(path)


def _snapshot_package(root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ApprovalHostCliError(
                f"package artifact may not be a symlink: {path.relative_to(root)}"
            )
        if not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_PACKAGE_BYTES or total + size > MAX_PACKAGE_BYTES:
            raise ApprovalHostCliError("package exceeds byte limit")
        total += size
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        if len(payload) != size:
            raise ApprovalHostCliError(
                f"package artifact changed while being read: {relative}"
            )
        files[relative] = payload
        if len(files) > MAX_PACKAGE_FILES:
            raise ApprovalHostCliError("package exceeds file limit")
    if not files:
        raise ApprovalHostCliError("package contains no regular files")
    return files


def _mapping_from_bytes(files: Mapping[str, bytes], path: str) -> dict[str, Any]:
    payload = files.get(path)
    if payload is None:
        raise ApprovalHostCliError(f"package is missing {path}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalHostCliError(f"package {path} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ApprovalHostCliError(f"package {path} must contain a JSON object")
    return value


def _bind_candidate_to_request(
    manifest: Mapping[str, Any],
    validated_lifecycle: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    request: Mapping[str, Any],
) -> None:
    if manifest.get("skill_id") != request["target_skill_id"]:
        raise ApprovalHostCliError(
            "approval request does not match the delivered package: skill_id"
        )
    if manifest.get("skill_version") != request["target_skill_version"]:
        raise ApprovalHostCliError(
            "approval request does not match the delivered package: skill_version"
        )
    if evidence_sha256(source_receipt) != request["package_receipt_sha256"]:
        raise ApprovalHostCliError(
            "approval request does not match the delivered package: "
            "package receipt digest"
        )
    lifecycle_head = (
        validated_lifecycle.get("events") or [{}]
    )[-1].get("event_sha256")
    if lifecycle_head != request["validated_lifecycle_head_sha256"]:
        raise ApprovalHostCliError(
            "approval request does not match the delivered package: "
            "validated lifecycle head digest"
        )


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ApprovalHostCliError(f"{label} is missing or not a regular file: {path}")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalHostCliError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ApprovalHostCliError(f"{label} must contain a JSON object")
    return value


def run_approval(
    from_dir: Path,
    *,
    bundle: Path,
    registry_path: Path,
    host_key_path: Path,
    promoted_lifecycle_out: Path,
    receipt_out: Path,
    now: str,
) -> dict[str, Any]:
    request = _read_json_object(from_dir / "approval-request.json", "approval request")
    evidence = _read_json_object(from_dir / "approval-evidence.json", "approval evidence")
    binding = _read_json_object(
        from_dir / "current-candidate.json", "current candidate binding"
    )
    decision = _read_json_object(
        from_dir / "approval-host-decision.json", "approval host decision"
    )

    # Validate request/evidence/binding and refuse stale candidates.
    _, _ = bind_request_evidence(request, evidence)
    require_current_candidate(request, binding)

    host_key = _read_host_key(host_key_path)
    ledger_path = Path(registry_path).absolute().parent / (
        "approval-host-decisions.sqlite3"
    )
    if not Path(registry_path).is_file():
        raise ApprovalHostCliError(
            f"governed registry not found at {registry_path}: the approval "
            "host must provide an initialized registry; promotion cannot "
            "create one"
        )
    registry = load_skill_registry(registry_path)

    files = _snapshot_package(bundle / "package")
    validate_or_raise(files)
    manifest = _mapping_from_bytes(files, "manifest.json")
    claim_index = _mapping_from_bytes(files, "claim-index.json")
    source_receipt = _mapping_from_bytes(files, "SOURCE_RECEIPT.json")
    validated_lifecycle = _read_json_object(
        bundle / "lifecycle.validated.json", "validated lifecycle"
    )
    _bind_candidate_to_request(manifest, validated_lifecycle, source_receipt, request)

    authorization = authorize_from_host_decision(
        request,
        decision,
        host_key=host_key,
        ledger_path=ledger_path,
        as_of=now,
    )
    digest = decision_sha256(decision)
    result = promote_validated_skill(
        registry,
        manifest,
        validated_lifecycle,
        claim_index,
        source_receipt,
        authorization=authorization,
        decision_ref=f"approval-host-decision:{digest}",
        occurred_at=now,
    )
    paths = persist_operator_promotion(
        result,
        registry_path=registry_path,
        promoted_lifecycle_path=promoted_lifecycle_out,
        receipt_path=receipt_out,
    )
    receipt = result["receipt"]
    return {
        "schema_version": OPERATOR_PROMOTION_SCHEMA_VERSION,
        "command": "approve",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "promoted": True,
        "skill_id": receipt["skill_id"],
        "skill_version": receipt["skill_version"],
        "package_receipt_sha256": receipt["package_receipt_sha256"],
        "registry_after_sha256": receipt["registry_after_sha256"],
        "routing_projection_sha256": receipt["routing_projection_sha256"],
        "model_authorized": False,
        "consumer_catalog_refresh_required": True,
        "paths": paths,
        "decision_ref": f"approval-host-decision:{digest}",
    }


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="knowledge-forge-approve",
        description=(
            "Trusted-host approval: apply one human 'Approve' from a signed "
            "host decision and perform the real PROMOTED registry transition."
        ),
    )
    parser.add_argument("from_dir", help="directory holding the approval contract artifacts")
    parser.add_argument("--bundle", required=True, help="validated bundle (package/ + lifecycle.validated.json)")
    parser.add_argument("--registry", required=True, help="governed registry.json (initialized by the administrator)")
    parser.add_argument("--host-key", required=True, help="0600 host-owned HMAC key file")
    parser.add_argument("--promoted-lifecycle-out", required=True)
    parser.add_argument("--receipt-out", required=True)
    parser.add_argument("--now", default=None, help="RFC3339 now (defaults to current UTC time)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.now is None:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    else:
        now = args.now
    try:
        payload = run_approval(
            Path(args.from_dir),
            bundle=Path(args.bundle),
            registry_path=Path(args.registry),
            host_key_path=Path(args.host_key),
            promoted_lifecycle_out=Path(args.promoted_lifecycle_out),
            receipt_out=Path(args.receipt_out),
            now=now,
        )
    except (
        ApprovalHostError,
        ApprovalHostCliError,
        ApprovalContractError,
        OperatorPromotionError,
        SkillRegistryError,
        PackageValidationError,
    ) as exc:
        _emit({
            "ok": False,
            "command": "approve",
            "error": str(exc),
            "promoted": False,
            "model_authorized": False,
        })
        return 2
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())