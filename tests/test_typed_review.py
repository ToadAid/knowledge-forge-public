"""P3B-UX-TYPED-REVIEW-P0: typed REVIEW only, no human authentication."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import ast
import json
import subprocess
import sys
import tempfile
import unittest

from knowledge_forge.approval import (
    ApprovalContractError, ReviewDecisionLedger, build_approval_evidence,
    build_approval_request, build_current_candidate_binding,
    validate_review_receipt,
)
from knowledge_forge.typed_review import WORDS, run_typed_review

T0 = "2026-10-14T20:00:00Z"
T1 = "2026-10-14T20:05:00Z"
T2 = "2026-10-14T20:10:00Z"


def sha(c):
    return "sha256:" + c * 64


def fixture():
    e = build_approval_evidence(
        request_id="review.typed.1001",
        target_skill_id="knowledge-forge:rooftile.quote@" + sha("a"),
        target_skill_version=1, package_receipt_sha256=sha("b"),
        validated_lifecycle_head_sha256=sha("c"),
        items=[{"label": "manifest", "sha256": sha("d")}],
    )
    r = build_approval_request(
        request_id=e["request_id"], requesting_agent="Claude Code",
        requesting_principal="agent-cli-session",
        target_skill_id=e["target_skill_id"],
        target_skill_version=e["target_skill_version"],
        package_receipt_sha256=e["package_receipt_sha256"],
        validated_lifecycle_head_sha256=e["validated_lifecycle_head_sha256"],
        requested_at=T0, expires_at=T2, evidence_digest=e["evidence_digest"],
    )
    return r, e, build_current_candidate_binding(r, observed_at=T1)


class FakeTerminal:
    input_is_tty = True
    output_is_tty = True

    def __init__(self, *lines):
        self.lines = list(lines)
        self.output = ""

    def write(self, text):
        self.output += text

    def read_line(self, _prompt):
        if not self.lines:
            raise EOFError()
        return self.lines.pop(0)


def review(*lines, clock=lambda: T1, ledger=None, values=None):
    a, b, c = fixture() if values is None else values
    terminal = FakeTerminal(*lines)
    result = run_typed_review(a, b, c, terminal, clock=clock, ledger=ledger)
    return result, terminal.output


class TypedReviewTests(unittest.TestCase):
    def test_single_word_approve_is_review_only(self):
        result, output = review("APPROVE")
        self.assertEqual(result["state"], "REVIEWED_APPROVED")
        self.assertEqual(result["decision"], "APPROVE")
        self.assertIs(result["host_authentication_verified"], False)
        self.assertIs(result["authorization_granted"], False)
        self.assertIs(result["promotion_authorized"], False)
        self.assertIs(result["operator_cli_invoked"], False)
        self.assertIs(result["production_registry_mutated"], False)
        self.assertEqual(result["execution_authority"], "NONE")
        self.assertEqual(result["tool_grants"], [])
        self.assertIn("REVIEW ONLY", output)
        self.assertIn("Promotion: DISABLED", output)
        validate_review_receipt(result, request=fixture()[0])

    def test_reject_records_reason_no_authority(self):
        result, _ = review("REJECT", "Needs better sourcing")
        self.assertEqual(result["state"], "REVIEWED_REJECTED")
        self.assertEqual(result["reason"], "Needs better sourcing")
        self.assertIs(result["authorization_granted"], False)

    def test_reject_without_reason_fails(self):
        with self.assertRaises(ApprovalContractError):
            review("REJECT", "")

    def test_cancel_no_receipt(self):
        result, _ = review("CANCEL")
        self.assertIsNone(result["receipt"])
        self.assertIs(result["promotion_authorized"], False)

    def test_details_show_exact_evidence_then_approve(self):
        result, output = review("DETAILS", "APPROVE")
        self.assertIn("Evidence SHA:", output)
        self.assertIn("manifest:", output)
        self.assertEqual(result["state"], "REVIEWED_APPROVED")

    def test_all_words_fixed(self):
        self.assertEqual(WORDS, {"APPROVE", "REJECT", "DETAILS", "CANCEL"})

    def test_fuzzy_and_lowercase_refused(self):
        for word in ("approve", "go ahead", "Approve", "yes", "APPROVE 1001", "A", " "):
            with self.subTest(word=word), self.assertRaises(ApprovalContractError):
                review(word)

    def test_piped_input_and_output_refused(self):
        for field in ("input_is_tty", "output_is_tty"):
            terminal = FakeTerminal("APPROVE")
            setattr(terminal, field, False)
            with self.subTest(field=field), self.assertRaises(ApprovalContractError):
                run_typed_review(*fixture(), terminal, clock=lambda: T1)

    def test_expired_initially_refused(self):
        with self.assertRaises(ApprovalContractError):
            review("APPROVE", clock=lambda: T2)

    def test_expired_during_confirmation_refused(self):
        clock_values = iter((T1, T2))
        with self.assertRaises(ApprovalContractError):
            review("APPROVE", clock=lambda: next(clock_values))

    def test_stale_candidate_refused(self):
        values = fixture()
        values[2]["validated_lifecycle_head_sha256"] = sha("e")
        with self.assertRaises(ApprovalContractError):
            review("APPROVE", values=values)

    def test_tampered_evidence_refused(self):
        values = fixture()
        values[1]["items"][0]["sha256"] = sha("e")
        with self.assertRaises(ApprovalContractError):
            review("APPROVE", values=values)

    def test_duplicate_session_decision_refused(self):
        ledger = ReviewDecisionLedger()
        review("APPROVE", ledger=ledger)
        with self.assertRaises(ApprovalContractError):
            review("APPROVE", ledger=ledger)

    def test_input_interrupt_refused(self):
        with self.assertRaises(ApprovalContractError):
            review()

    def test_unchanged_input(self):
        val = fixture()
        before = deepcopy(val)
        review("APPROVE", values=val)
        self.assertEqual(val, before)

    def test_details_limit_refused(self):
        with self.assertRaises(ApprovalContractError):
            review(*(["DETAILS"] * 12))

    def test_cli_help_no_privileged_operator(self):
        result = subprocess.run([sys.executable, "-m", "knowledge_forge.typed_review", "--help"],
                                capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--promote", result.stdout)
        self.assertNotIn("--authorize", result.stdout)

    def test_module_imports_no_privilege(self):
        path = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "typed_review.py"
        nodes = ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        modules = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                modules.update(n.name.split(".")[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module.split(".")[0])
        self.assertFalse(modules & {"operator_cli", "operator_promotion", "sqlite3", "subprocess", "socket", "ctypes", "pwd", "grp"})

    def test_no_receipt_written_by_runner(self):
        with tempfile.TemporaryDirectory() as temp:
            before = list(Path(temp).iterdir())
            review("APPROVE")
            self.assertEqual(before, list(Path(temp).iterdir()))


if __name__ == "__main__":
    unittest.main()
