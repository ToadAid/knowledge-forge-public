"""One-word, review-only CLI for ordinary Claude/Codex terminal users.

The agent and user may share the same UID and terminal. Typed words are NOT
independent human authentication. This module never imports operator code.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Mapping, Sequence

from .approval import (
    ApprovalContractError,
    ReviewDecisionLedger,
    approval_request_state,
    bind_request_evidence,
    project_approval_request,
    require_current_candidate,
    validate_review_receipt,
)
from .approval_tui import (
    LOCAL_REVIEWER,
    ReviewTerminal,
    SystemReviewTerminal,
    _load_json,
    _now,
    _write_receipt,
)


WORDS = frozenset({"APPROVE", "REJECT", "DETAILS", "CANCEL"})


def _details(projection: Mapping[str, Any]) -> str:
    rows = [
        "Knowledge Forge — exact review evidence (UNTRUSTED)",
        f"Request: {projection['request_id']}",
        f"Request SHA: {projection['request_sha256']}",
        f"Package SHA: {projection['package_receipt_sha256']}",
        f"Lifecycle SHA: {projection['validated_lifecycle_head_sha256']}",
        f"Evidence SHA: {projection['evidence_digest']}",
        "Bounded evidence items:",
    ]
    rows.extend(f"  {v['label']}: {v['sha256']}" for v in projection["evidence_items"])
    rows.append("All evidence is caller-supplied. Host verification: NONE.\n")
    return "\n".join(rows)


def run_typed_review(
    request_value: Any,
    evidence_value: Any,
    current_binding_value: Any,
    terminal: ReviewTerminal,
    *,
    clock: Callable[[], str] = _now,
    ledger: ReviewDecisionLedger | None = None,
) -> dict[str, Any]:
    """Record a one-word review acknowledgement, NEVER promotion authority.

    The injectable clock is useful for deterministic tests but remains
    caller-controlled. An authenticated host MUST NOT use this as trusted time.
    """
    if not terminal.input_is_tty or not terminal.output_is_tty:
        raise ApprovalContractError("typed review requires an interactive input and output TTY")
    request, evidence = bind_request_evidence(request_value, evidence_value)
    require_current_candidate(request, current_binding_value)
    reviewed_at = clock()
    if approval_request_state(request, as_of=reviewed_at) != "PENDING":
        raise ApprovalContractError("review request expired before presentation")
    projection = project_approval_request(
        request, evidence, surface="TERMINAL_TUI", as_of=reviewed_at,
    )
    terminal.write(
        "\n🐸 Knowledge Forge — REVIEW ONLY\n"
        f"Skill: {projection['target_skill_id']}\n"
        f"Version: {projection['target_skill_version']} | State: VALIDATED (claimed)\n"
        f"Request: {projection['request_id']}\n"
        f"Package: {projection['package_receipt_sha256']}\n"
        f"Expires: {projection['expires_at']}\n"
        "Source coverage: NOT INDEPENDENTLY ESTABLISHED\n"
        "Type APPROVE, REJECT, DETAILS or CANCEL.\n"
        "Typing APPROVE records a REVIEW only; it CANNOT promote a skill.\n"
    )
    session = ledger if ledger is not None else ReviewDecisionLedger()
    for _ in range(12):
        try:
            word = terminal.read_line("Review > ").strip()
        except (EOFError, KeyboardInterrupt) as exc:
            raise ApprovalContractError("review input interrupted; no decision recorded") from exc
        if word not in WORDS:
            raise ApprovalContractError("invalid review word; exact uppercase word required")
        if word == "DETAILS":
            terminal.write(_details(projection))
            continue
        if word == "CANCEL":
            terminal.write("Review cancelled. Promotion: DISABLED.\n")
            return {
                "state": "PENDING", "receipt": None, "review_only": True,
                "host_authentication_verified": False,
                "authorization_granted": False, "promotion_authorized": False,
                "operator_cli_invoked": False, "production_registry_mutated": False,
            }
        reason = None
        if word == "REJECT":
            try:
                reason = terminal.read_line("Reason for rejection > ").strip()
            except (EOFError, KeyboardInterrupt) as exc:
                raise ApprovalContractError("rejection input interrupted") from exc
            if not reason:
                raise ApprovalContractError("REJECT requires a reason")
        # Must re-check caller-supplied binding and expiry when decision is made.
        require_current_candidate(request, current_binding_value)
        at = clock()
        if approval_request_state(request, as_of=at) != "PENDING":
            raise ApprovalContractError("review request expired during confirmation")
        result = session.record(
            request, evidence, current_binding_value,
            decision=word, reviewed_at=at, identity=LOCAL_REVIEWER,
            review_surface="TERMINAL_TUI", reason=reason,
        )
        receipt = validate_review_receipt(result, request=request)
        terminal.write(
            f"Review recorded: {receipt['state']}\n"
            "Authentication: NONE | Authorization: NONE | Promotion: DISABLED\n"
        )
        return receipt
    raise ApprovalContractError("review details limit exceeded without decision")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m knowledge_forge.typed_review",
        description="One-word terminal review, NEVER authentication or promotion",
    )
    parser.add_argument("--request", required=True)
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--current-binding", required=True)
    parser.add_argument("--receipt-out", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        request = _load_json(args.request, "approval request")
        evidence = _load_json(args.evidence, "approval evidence")
        binding = _load_json(args.current_binding, "current candidate binding")
        receipt = run_typed_review(request, evidence, binding, SystemReviewTerminal())
        if receipt.get("receipt_id"):
            _write_receipt(args.receipt_out, receipt)
        return 0
    except ApprovalContractError as exc:
        print(json.dumps({
            "ok": False, "review_only": True, "error": str(exc),
            "host_authentication_verified": False,
            "authorization_granted": False, "promotion_authorized": False,
            "operator_cli_invoked": False,
        }, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
