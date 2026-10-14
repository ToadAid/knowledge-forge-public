from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge.approval import ApprovalContractError, ReviewDecisionLedger
from knowledge_forge.approval_tui import run_local_review_tui
from test_approval import REVIEWED, fixture, sha


REPO_ROOT = Path(__file__).resolve().parents[1]


class ScriptedTerminal:
    def __init__(self, answers, *, input_tty=True, output_tty=True):
        self.answers = list(answers)
        self.input_is_tty = input_tty
        self.output_is_tty = output_tty
        self.output = []

    def write(self, text):
        self.output.append(text)

    def read_line(self, prompt):
        self.output.append(prompt)
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class ApprovalTuiTests(unittest.TestCase):
    def setUp(self):
        self.clock = patch("knowledge_forge.approval_tui._now", return_value=REVIEWED)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_noninteractive_input_and_output_refuse(self):
        request, evidence, binding, _identity = fixture()
        for terminal in (
            ScriptedTerminal([], input_tty=False),
            ScriptedTerminal([], output_tty=False),
        ):
            with self.assertRaisesRegex(ApprovalContractError, "interactive TTY"):
                run_local_review_tui(request, evidence, binding, terminal)

    def test_scripted_approval_records_review_but_never_authority_or_promotion(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["v", "a", "APPROVE etic0001"])
        receipt = run_local_review_tui(request, evidence, binding, terminal)
        self.assertEqual(receipt["state"], "REVIEWED_APPROVED")
        self.assertFalse(receipt["host_authentication_verified"])
        self.assertFalse(receipt["authorization_granted"])
        self.assertFalse(receipt["promotion_authorized"])
        self.assertFalse(receipt["operator_cli_invoked"])
        self.assertFalse(receipt["production_registry_mutated"])
        rendered = "".join(terminal.output)
        self.assertIn("Exact bounded evidence", rendered)
        self.assertIn(request["package_receipt_sha256"], rendered)
        self.assertIn(request["validated_lifecycle_head_sha256"], rendered)
        self.assertIn("Review only", rendered)

    def test_explicit_confirmation_is_required(self):
        request, evidence, binding, _identity = fixture()
        with self.assertRaisesRegex(ApprovalContractError, "confirmation mismatch"):
            run_local_review_tui(
                request,
                evidence,
                binding,
                ScriptedTerminal(["a", "yes"]),
            )

    def test_rejection_and_changes_require_reasons(self):
        request, evidence, binding, _identity = fixture()
        with self.assertRaisesRegex(ApprovalContractError, "reason is required"):
            run_local_review_tui(
                request,
                evidence,
                binding,
                ScriptedTerminal(["r", ""]),
            )
        receipt = run_local_review_tui(
            request,
            evidence,
            binding,
            ScriptedTerminal(["c", "Bind a newer validation report"]),
        )
        self.assertEqual(receipt["state"], "CHANGES_REQUESTED")
        self.assertEqual(receipt["reason"], "Bind a newer validation report")

    def test_stale_candidate_refuses_before_prompt_or_decision(self):
        request, evidence, binding, _identity = fixture()
        stale = deepcopy(binding)
        stale["package_receipt_sha256"] = sha("f")
        terminal = ScriptedTerminal(["a", "APPROVE etic0001"])
        with self.assertRaisesRegex(ApprovalContractError, "stale candidate"):
            run_local_review_tui(
                request, evidence, stale, terminal
            )
        self.assertIn("stale or changed", "".join(terminal.output))

    def test_expired_request_shows_warning_and_refuses(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["a", "APPROVE etic0001"])
        with self.assertRaisesRegex(ApprovalContractError, "expired"):
            with patch("knowledge_forge.approval_tui._now", return_value="2026-10-13T16:31:00Z"):
                run_local_review_tui(request, evidence, binding, terminal)
        self.assertIn("REQUEST EXPIRED", "".join(terminal.output))

    def test_request_expiring_while_tui_is_open_refuses_without_receipt(self):
        request, evidence, binding, identity = fixture()
        terminal = ScriptedTerminal(["a", "APPROVE etic0001"])
        ledger = ReviewDecisionLedger()
        with patch(
            "knowledge_forge.approval_tui._now",
            side_effect=[REVIEWED, "2026-10-13T16:31:00Z"],
        ) as clock:
            with self.assertRaisesRegex(ApprovalContractError, "expired"):
                run_local_review_tui(
                    request,
                    evidence,
                    binding,
                    terminal,
                    ledger=ledger,
                )
        self.assertEqual(clock.call_count, 2)
        self.assertIn("expired while review was open", "".join(terminal.output))
        receipt = ledger.record(
            request,
            evidence,
            binding,
            decision="EXPIRE",
            reviewed_at="2026-10-13T16:31:00Z",
            identity=identity,
            review_surface="TERMINAL_TUI",
        )
        self.assertEqual(receipt["state"], "EXPIRED")

    def test_duplicate_decision_in_same_session_refuses(self):
        request, evidence, binding, identity = fixture()
        ledger = ReviewDecisionLedger()
        ledger.record(
            request,
            evidence,
            binding,
            decision="REJECT",
            reviewed_at=REVIEWED,
            identity=identity,
            review_surface="TERMINAL_TUI",
            reason="synthetic refusal",
        )
        with self.assertRaisesRegex(ApprovalContractError, "replay or duplicate"):
            run_local_review_tui(
                request,
                evidence,
                binding,
                ScriptedTerminal(["a", "APPROVE etic0001"]),
                ledger=ledger,
            )

    def test_ui_projects_no_private_source_body_or_secret_like_fixture(self):
        request, evidence, binding, _identity = fixture()
        private_markers = [
            "/home/synthetic/private/source.py",
            "ghp_syntheticsecret",
            "-----BEGIN PRIVATE KEY-----",
            "private implementation body",
        ]
        terminal = ScriptedTerminal(["v", "q"])
        result = run_local_review_tui(request, evidence, binding, terminal)
        rendered = "".join(terminal.output)
        for marker in private_markers:
            self.assertNotIn(marker, rendered)
        self.assertIsNone(result["receipt"])
        self.assertFalse(result["authorization_granted"])

    def test_piped_cli_refuses_and_never_exposes_operator_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            receipt = Path(tmp) / "should-not-exist.json"
            run = subprocess.run(
                [str(REPO_ROOT / "bin" / "knowledge-forge-review"), "synthetic", "--receipt-out", str(receipt)],
                cwd=REPO_ROOT,
                input="a\nAPPROVE etic0001\n",
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("interactive TTY input is required", run.stderr)
            self.assertIn('"operator_cli_invoked": false', run.stderr)
            self.assertFalse(receipt.exists())
        installer = (REPO_ROOT / "bin" / "knowledge-forge-install-agent").read_text(encoding="utf-8")
        self.assertIn("knowledge-forge-operator is deliberately NOT installed", installer)

    def test_expired_real_review_cli_rejects_caller_clock_override(self):
        request, evidence, binding, _identity = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            request_path = root / "request.json"
            evidence_path = root / "evidence.json"
            binding_path = root / "binding.json"
            receipt_path = root / "receipt.json"
            for path, value in (
                (request_path, request),
                (evidence_path, evidence),
                (binding_path, binding),
            ):
                path.write_text(json.dumps(value), encoding="utf-8")
            run = subprocess.run(
                [
                    str(REPO_ROOT / "bin" / "knowledge-forge-review"),
                    "review",
                    "--request",
                    str(request_path),
                    "--evidence",
                    str(evidence_path),
                    "--current-binding",
                    str(binding_path),
                    "--receipt-out",
                    str(receipt_path),
                    "--reviewed-at",
                    REVIEWED,
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(run.returncode, 2)
            self.assertIn("unrecognized arguments: --reviewed-at", run.stderr)
            self.assertFalse(receipt_path.exists())

    def test_cli_projects_all_surfaces_without_transport_authority(self):
        first = subprocess.run(
            [str(REPO_ROOT / "bin" / "knowledge-forge-review"), "synthetic-projections", "--as-of", REVIEWED],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        second = subprocess.run(
            [str(REPO_ROOT / "bin" / "knowledge-forge-review"), "synthetic-projections", "--as-of", REVIEWED],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        for surface in ("TERMINAL_TUI", "TELEGRAM_CARD", "DESKTOP_CARD", "WEB_TEMPLE"):
            self.assertIn(f'"{surface}"', first.stdout)
        self.assertNotIn("knowledge-forge-operator", first.stdout)

    def test_review_has_no_operator_or_registry_mutation_path(self):
        request, evidence, binding, _identity = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "production-registry-sentinel.json"
            original = b'{"production":"unchanged"}\n'
            registry.write_bytes(original)
            receipt = run_local_review_tui(
                request,
                evidence,
                binding,
                ScriptedTerminal(["a", "APPROVE etic0001"]),
            )
            self.assertEqual(registry.read_bytes(), original)
            self.assertFalse(receipt["operator_cli_invoked"])
            self.assertFalse(receipt["production_registry_mutated"])
        implementation = (
            (REPO_ROOT / "src" / "knowledge_forge" / "approval.py").read_text(encoding="utf-8")
            + (REPO_ROOT / "src" / "knowledge_forge" / "approval_tui.py").read_text(encoding="utf-8")
        )
        self.assertNotIn("from .operator", implementation)
        self.assertNotIn("subprocess", implementation)


if __name__ == "__main__":
    unittest.main()
