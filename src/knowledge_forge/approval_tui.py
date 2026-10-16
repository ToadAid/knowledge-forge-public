"""Interactive, local, review-only terminal surface for approval requests."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Protocol, Sequence

from .approval import (
    ApprovalContractError,
    PRESENTATION_SURFACES,
    ReviewDecisionLedger,
    ReviewIdentity,
    approval_request_state,
    bind_request_evidence,
    build_approval_evidence,
    build_approval_request,
    build_current_candidate_binding,
    project_approval_request,
    require_current_candidate,
)


MAX_INPUT_BYTES = 256 * 1024
LOCAL_REVIEWER = ReviewIdentity(principal_ref="local-terminal-reviewer")


class ReviewTerminal(Protocol):
    input_is_tty: bool
    output_is_tty: bool

    def write(self, text: str) -> None: ...
    def read_line(self, prompt: str) -> str: ...


class SystemReviewTerminal:
    @property
    def input_is_tty(self) -> bool:
        return sys.stdin.isatty()

    @property
    def output_is_tty(self) -> bool:
        return sys.stdout.isatty()

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def read_line(self, prompt: str) -> str:
        return input(prompt)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _confirmation_token(request_sha256: str) -> str:
    """Neutral attention token: first 8 hex chars of the canonical request digest.

    The phrase is an aid so a human must actually read the prompt before the
    review is recorded. It is not a secret and not proof of authenticity; it is
    never stored in receipts or the session ledger.
    """
    return request_sha256.split(":", 1)[1][:8]


def _card(projection: Mapping[str, Any]) -> str:
    warning = (
        "WARNING: REQUEST EXPIRED — approval unavailable"
        if projection["state"] == "EXPIRED"
        else "Review only — not operator authorization"
    )
    width = 74
    border = "+" + "-" * (width + 2) + "+"

    def row(text: str) -> str:
        return f"| {text:<{width}} |"

    def wrapped(label: str, value: Any) -> list[str]:
        text = f"{label}: {value}"
        return [row(text[index:index + width]) for index in range(0, len(text), width)]

    lines = [border, row("KNOWLEDGE FORGE — HUMAN REVIEW"), border]
    lines.extend(wrapped("Agent", projection["requesting_agent"]))
    lines.extend(wrapped("Action", "Promote validated skill"))
    lines.extend(wrapped("Skill", projection["target_skill_id"]))
    lines.extend(wrapped("Version", projection["target_skill_version"]))
    lines.extend(wrapped("Candidate", "VALIDATED"))
    lines.extend(wrapped("Review state", projection["state"]))
    lines.append(row("Package digest:"))
    lines.append(row(str(projection["package_receipt_sha256"])))
    lines.extend(wrapped("Evidence", f"Available ({len(projection['evidence_items'])} bounded items)"))
    lines.extend(wrapped("Expires", projection["expires_at"]))
    lines.extend([row(warning), border])
    lines.extend([
        row("[V] View evidence  [A] Approve review  [R] Reject"),
        row("[C] Request changes                    [Q] Exit"),
        border,
        "",
    ])
    return "\n".join(lines)


def _evidence_text(projection: Mapping[str, Any]) -> str:
    lines = [
        "Exact bounded evidence",
        f"request_sha256: {projection['request_sha256']}",
        f"package_receipt_sha256: {projection['package_receipt_sha256']}",
        f"validated_lifecycle_head_sha256: {projection['validated_lifecycle_head_sha256']}",
        f"evidence_digest: {projection['evidence_digest']}",
        "Labels are opaque identifiers shown on purpose; this view contains hashes"
        " only, no file contents or local paths.",
        "Before approving, independently recompute and compare each sha256 against"
        " the agent's artifacts in your own workspace.",
        "Confirmation phrase: APPROVE plus the first 8 hex characters of"
        " request_sha256 above.",
    ]
    for item in projection["evidence_items"]:
        lines.append(f"evidence.{item['label']}: {item['sha256']}")
    lines.extend([
        "host_authentication_verified: false",
        "authorization_granted: false",
        "promotion_authorized: false",
        "",
    ])
    return "\n".join(lines)


def run_local_review_tui(
    request_value: Any,
    evidence_value: Any,
    current_binding_value: Any,
    terminal: ReviewTerminal,
    *,
    ledger: ReviewDecisionLedger | None = None,
) -> dict[str, Any]:
    return _run_local_review_tui_at(
        request_value,
        evidence_value,
        current_binding_value,
        terminal,
        reviewed_at=_now(),
        decision_clock=_now,
        ledger=ledger,
    )


def _run_local_review_tui_at(
    request_value: Any,
    evidence_value: Any,
    current_binding_value: Any,
    terminal: ReviewTerminal,
    *,
    reviewed_at: str,
    decision_clock: Callable[[], str] | None = None,
    ledger: ReviewDecisionLedger | None = None,
) -> dict[str, Any]:
    """Run with deterministic time only for tests and the synthetic demo."""
    if not terminal.input_is_tty:
        raise ApprovalContractError("interactive TTY input is required")
    if not terminal.output_is_tty:
        raise ApprovalContractError("interactive TTY output is required")
    request, evidence = bind_request_evidence(request_value, evidence_value)
    try:
        require_current_candidate(request, current_binding_value)
    except ApprovalContractError:
        terminal.write("WARNING: stale or changed candidate evidence\nreview refused\nauthorization_granted: false\n")
        raise
    projection = project_approval_request(
        request,
        evidence,
        surface="TERMINAL_TUI",
        as_of=reviewed_at,
    )
    terminal.write(_card(projection))
    if approval_request_state(request, as_of=reviewed_at) == "EXPIRED":
        raise ApprovalContractError("expired approval request refused")
    session = ledger or ReviewDecisionLedger()
    approval_mismatches = 0
    while True:
        try:
            choice = terminal.read_line("Select V/A/R/C/Q > ").strip().lower()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ApprovalContractError("terminal review cancelled") from exc
        if choice in {"q", "quit", "exit"}:
            return {
                "state": "PENDING",
                "receipt": None,
                "authorization_granted": False,
                "promotion_authorized": False,
                "operator_cli_invoked": False,
            }
        if choice in {"v", "view"}:
            terminal.write(_evidence_text(projection))
            continue
        decision: str
        reason: str | None = None
        if choice in {"a", "approve"}:
            expected = f"APPROVE {_confirmation_token(projection['request_sha256'])}"
            try:
                phrase = terminal.read_line(f"Type {expected} to record review > ")
            except (EOFError, KeyboardInterrupt) as exc:
                raise ApprovalContractError("terminal review cancelled") from exc
            if phrase.strip() != expected:
                approval_mismatches += 1
                if approval_mismatches >= 3:
                    raise ApprovalContractError(
                        "approval confirmation mismatch after 3 attempts; "
                        f"expected exactly: {expected}"
                    )
                terminal.write(
                    "Mismatch: approval not recorded.\n"
                    f"Expected exactly: {expected} — also shown in the evidence view (V).\n"
                    "Type A to try again or Q to exit.\n"
                )
                continue
            decision = "APPROVE"
        elif choice in {"r", "reject"}:
            reason = terminal.read_line("Rejection reason > ").strip()
            if not reason:
                raise ApprovalContractError("rejection reason is required")
            decision = "REJECT"
        elif choice in {"c", "changes", "request_changes"}:
            reason = terminal.read_line("Requested changes > ").strip()
            if not reason:
                raise ApprovalContractError("change reason is required")
            decision = "REQUEST_CHANGES"
        else:
            terminal.write("Invalid selection. Choose V, A, R, C, or Q.\n")
            continue
        decision_reviewed_at = decision_clock() if decision_clock is not None else reviewed_at
        if approval_request_state(request, as_of=decision_reviewed_at) == "EXPIRED":
            terminal.write(
                "WARNING: request expired while review was open\n"
                "review refused\n"
                "authorization_granted: false\n"
            )
            raise ApprovalContractError("expired approval request refused")
        receipt = session.record(
            request,
            evidence,
            current_binding_value,
            decision=decision,
            reviewed_at=decision_reviewed_at,
            identity=LOCAL_REVIEWER,
            review_surface="TERMINAL_TUI",
            reason=reason,
        )
        terminal.write(
            "Review recorded\n"
            f"state: {receipt['state']}\n"
            f"receipt_id: {receipt['receipt_id']}\n"
            "host_authentication_verified: false\n"
            "authorization_granted: false\n"
            "promotion_authorized: false\n"
            "operator_cli_invoked: false\n"
        )
        return receipt


def _load_json(path_value: str, label: str) -> dict[str, Any]:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise ApprovalContractError(f"{label} must be a regular non-symlink file")
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ApprovalContractError(f"{label} exceeds {MAX_INPUT_BYTES} bytes")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalContractError(f"{label} must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ApprovalContractError(f"{label} must contain a JSON object")
    return value


def _load_review_inputs(
    request_value: str | None,
    evidence_value: str | None,
    current_binding_value: str | None,
    from_dir_value: str | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Resolve review inputs from explicit flags or one canonical bundle directory.

    With --from-dir, exactly three canonical filenames are read from the given
    directory through _load_json (same bounded checks). Validation fails closed
    before any file read.
    """
    if from_dir_value is not None:
        if any(value is not None for value in (request_value, evidence_value, current_binding_value)):
            raise ApprovalContractError(
                "--from-dir cannot be combined with --request/--evidence/--current-binding"
            )
        base = Path(from_dir_value)
        if base.is_symlink() or not base.is_dir():
            raise ApprovalContractError("--from-dir must be an existing real directory")
        request_value = str(base / "approval-request.json")
        evidence_value = str(base / "approval-evidence.json")
        current_binding_value = str(base / "current-candidate.json")
    elif any(value is None for value in (request_value, evidence_value, current_binding_value)):
        raise ApprovalContractError(
            "--request, --evidence and --current-binding are required unless --from-dir is given"
        )
    return (
        _load_json(request_value, "approval request (approval-request.json)"),
        _load_json(evidence_value, "approval evidence (approval-evidence.json)"),
        _load_json(current_binding_value, "current candidate binding (current-candidate.json)"),
    )


def _write_receipt(path_value: str, receipt: Mapping[str, Any]) -> None:
    path = Path(path_value)
    if path.exists() or path.is_symlink():
        raise ApprovalContractError("receipt output already exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ApprovalContractError("receipt parent must be an existing real directory")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        payload = (json.dumps(dict(receipt), sort_keys=True, indent=2) + "\n").encode("utf-8")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written < 1:
                raise ApprovalContractError("receipt output write failed")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _synthetic() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    now = datetime(2026, 10, 13, 16, 0, tzinfo=timezone.utc)
    requested = now.isoformat().replace("+00:00", "Z")
    expires = (now + timedelta(days=365 * 50)).isoformat().replace("+00:00", "Z")
    digest_a = "sha256:" + "a" * 64
    digest_b = "sha256:" + "b" * 64
    evidence = build_approval_evidence(
        request_id="review.synthetic.0001",
        target_skill_id="skill.synthetic.roofing",
        target_skill_version=1,
        package_receipt_sha256=digest_a,
        validated_lifecycle_head_sha256=digest_b,
        items=[
            {"label": "faithfulness-report", "sha256": "sha256:" + "c" * 64},
            {"label": "validation-report", "sha256": "sha256:" + "d" * 64},
        ],
    )
    request = build_approval_request(
        request_id=evidence["request_id"],
        requesting_agent="Synthetic generic agent",
        requesting_principal="synthetic-requester",
        target_skill_id=evidence["target_skill_id"],
        target_skill_version=evidence["target_skill_version"],
        package_receipt_sha256=evidence["package_receipt_sha256"],
        validated_lifecycle_head_sha256=evidence["validated_lifecycle_head_sha256"],
        requested_at=requested,
        expires_at=expires,
        evidence_digest=evidence["evidence_digest"],
    )
    return request, evidence, build_current_candidate_binding(request, observed_at=requested), requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-forge-review",
        description="Review-only local TUI. It never creates operator authorization or runs promotion.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    preview = commands.add_parser(
        "preview-validated",
        help="read-only, bounded VALIDATED candidate evidence preview; never approves",
    )
    preview.add_argument("--format", choices=("text", "json"), default="text")
    preview.add_argument("--package-root", required=True)
    preview.add_argument("--validated-lifecycle", required=True)
    review = commands.add_parser("review")
    review.add_argument("--request")
    review.add_argument("--evidence")
    review.add_argument("--current-binding")
    review.add_argument(
        "--from-dir",
        help="directory holding approval-request.json, approval-evidence.json and current-candidate.json",
    )
    review.add_argument("--receipt-out", required=True)
    synthetic = commands.add_parser("synthetic")
    synthetic.add_argument("--receipt-out", required=True)
    projections = commands.add_parser("synthetic-projections")
    projections.add_argument("--as-of")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preview-validated":
            from .candidate_preview import preview_validated_paths

            value = preview_validated_paths(
                args.package_root,
                args.validated_lifecycle,
                observed_at=_now(),
            )
            if args.format == "json":
                sys.stdout.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
            else:
                rows = [
                    "KNOWLEDGE FORGE — VALIDATED CANDIDATE (READ ONLY)",
                    f"Skill: {value['skill_id']}",
                    f"Knowledge: {value['knowledge_id']}",
                    f"Version: {value['skill_version']}",
                    f"Units: {value['unit_count']} | Claims: {value['claim_count']} | Sources: {value['source_count']}",
                    "Coverage: UNKNOWN beyond this package; do not infer complete source coverage.",
                    "Claim keys (up to 10):",
                    *(f"  - {key}" for key in value["claim_keys_first_10"]),
                    f"Package receipt: {value['package_receipt_sha256']}",
                    f"Validated lifecycle: {value['validated_lifecycle_head_sha256']}",
                    "REVIEW ONLY: no review request or receipt created.",
                    "NOT authenticated, NOT authorized, NOT promoted.",
                    "Existing approval cards and operator boundary remain separate.",
                ]
                sys.stdout.write("\n".join(rows) + "\n")
            return 0
        if args.command == "synthetic-projections":
            request, evidence, _binding, synthetic_now = _synthetic()
            as_of = args.as_of or synthetic_now
            value = {
                surface: project_approval_request(request, evidence, surface=surface, as_of=as_of)
                for surface in sorted(PRESENTATION_SURFACES)
            }
            sys.stdout.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
            return 0
        if args.command == "synthetic":
            request, evidence, binding, synthetic_now = _synthetic()
            receipt = _run_local_review_tui_at(
                request,
                evidence,
                binding,
                SystemReviewTerminal(),
                reviewed_at=synthetic_now,
            )
        else:
            request, evidence, binding = _load_review_inputs(
                args.request, args.evidence, args.current_binding, args.from_dir
            )
            receipt = run_local_review_tui(
                request,
                evidence,
                binding,
                SystemReviewTerminal(),
            )
        if receipt.get("receipt_id"):
            _write_receipt(args.receipt_out, receipt)
        return 0
    except ApprovalContractError as exc:
        sys.stderr.write(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "review_only": True,
                    "host_authentication_verified": False,
                    "authorization_granted": False,
                    "promotion_authorized": False,
                    "operator_cli_invoked": False,
                },
                sort_keys=True,
            )
            + "\n"
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
