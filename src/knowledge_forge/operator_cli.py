"""Privileged headless operator CLI for Knowledge Forge promotion.

Unlike ``knowledge-forge``, this executable is authority-bearing and must only
be invoked by an already-authenticated human/operator host boundary. It must
not be exposed as an ordinary model tool.

Consumer-specific catalogs are not mutated here. After registry promotion the
host refreshes its own catalog/index from canonical registry truth.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .operator_promotion import (
    AUTHORIZATION_BOUNDARY,
    OperatorAuthorization,
    OperatorPromotionError,
    persist_operator_promotion,
    promote_validated_skill,
)
from .registry import load_skill_registry
from .validate import validate_skill_package

MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class OperatorCliError(ValueError):
    """Raised when privileged operator input is incomplete or unsafe."""


def _emit(payload: Mapping[str, Any], *, stream: Any = None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink():
        raise OperatorCliError(f"{label} may not be a symlink")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorCliError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise OperatorCliError(f"{label} must contain a JSON object")
    return value


def _snapshot_package(root_value: str) -> dict[str, bytes]:
    root = Path(root_value)
    if root.is_symlink() or not root.is_dir():
        raise OperatorCliError("package root must be a real directory")
    files: dict[str, bytes] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise OperatorCliError(
                f"package artifact may not be a symlink: {path.relative_to(root)}"
            )
        if not path.is_file():
            continue
        payload = path.read_bytes()
        relative = path.relative_to(root).as_posix()
        files[relative] = payload
        total += len(payload)
        if len(files) > MAX_PACKAGE_FILES:
            raise OperatorCliError("package exceeds file limit")
        if total > MAX_PACKAGE_BYTES:
            raise OperatorCliError("package exceeds byte limit")
    if not files:
        raise OperatorCliError("package contains no regular files")
    return files


def _mapping_from_bytes(
    files: Mapping[str, bytes],
    path: str,
) -> dict[str, Any]:
    payload = files.get(path)
    if payload is None:
        raise OperatorCliError(f"package is missing {path}")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperatorCliError(f"package {path} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise OperatorCliError(f"package {path} must contain a JSON object")
    return value


def _command_promote(args: argparse.Namespace) -> int:
    files = _snapshot_package(args.package_root)
    validation = validate_skill_package(files)
    if validation.get("valid") is not True:
        raise OperatorCliError(
            "package validation failed: "
            + "; ".join(validation.get("errors", []))
        )
    if validation.get("security_findings") not in ([], None):
        raise OperatorCliError(
            "package validation reported security findings"
        )

    manifest = _mapping_from_bytes(files, "manifest.json")
    claim_index = _mapping_from_bytes(files, "claim-index.json")
    source_receipt = _mapping_from_bytes(files, "SOURCE_RECEIPT.json")
    validated_lifecycle = _load_json(
        Path(args.validated_lifecycle),
        "validated lifecycle",
    )
    registry = load_skill_registry(args.registry)

    # The CLI itself is a privileged operator surface. External host/shell
    # authentication precedes invocation; these refs are provenance labels.
    authorization = OperatorAuthorization(
        principal_ref=args.principal_ref,
        authorization_ref=args.authorization_ref,
        verified_by_host=True,
    )
    result = promote_validated_skill(
        registry,
        manifest,
        validated_lifecycle,
        claim_index,
        source_receipt,
        authorization=authorization,
        decision_ref=args.decision_ref,
        occurred_at=args.occurred_at,
    )
    paths = persist_operator_promotion(
        result,
        registry_path=args.registry,
        promoted_lifecycle_path=args.promoted_lifecycle_out,
        receipt_path=args.receipt_out,
    )
    receipt = result["receipt"]
    _emit(
        {
            "schema_version": receipt["schema_version"],
            "command": "promote",
            "authorization_boundary": AUTHORIZATION_BOUNDARY,
            "skill_id": receipt["skill_id"],
            "skill_version": receipt["skill_version"],
            "package_receipt_sha256": receipt["package_receipt_sha256"],
            "lifecycle_after_head_sha256": (
                receipt["lifecycle_after_head_sha256"]
            ),
            "registry_after_sha256": receipt["registry_after_sha256"],
            "routing_projection_sha256": (
                receipt["routing_projection_sha256"]
            ),
            "consumer_catalog_refresh_required": True,
            "model_authorized": False,
            "paths": paths,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-forge-operator",
        description=(
            "Privileged Knowledge Forge operator control plane. "
            "Invoke only after external human authentication."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    promote = subparsers.add_parser(
        "promote",
        help="promote one exact VALIDATED package into registry truth",
    )
    promote.add_argument("--package-root", required=True)
    promote.add_argument("--validated-lifecycle", required=True)
    promote.add_argument("--registry", required=True)
    promote.add_argument("--promoted-lifecycle-out", required=True)
    promote.add_argument("--receipt-out", required=True)
    promote.add_argument("--principal-ref", required=True)
    promote.add_argument("--authorization-ref", required=True)
    promote.add_argument("--decision-ref", required=True)
    promote.add_argument("--occurred-at", required=True)
    promote.set_defaults(handler=_command_promote)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (OperatorCliError, OperatorPromotionError, ValueError) as exc:
        _emit(
            {
                "schema_version": "0.1",
                "command": getattr(args, "command", None),
                "ok": False,
                "error": str(exc),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
