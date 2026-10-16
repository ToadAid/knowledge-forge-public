from __future__ import annotations

import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from _platform_support import skip_bash_launcher_on_windows, skip_unless_symlinks
from unittest.mock import patch

from knowledge_forge.approval import (
    ApprovalContractError,
    ReviewDecisionLedger,
    approval_request_sha256,
)
from knowledge_forge.approval_tui import (
    _confirmation_token,
    _load_review_inputs,
    main,
    run_local_review_tui,
)
from test_approval import REVIEWED, fixture, sha


REPO_ROOT = Path(__file__).resolve().parents[1]


def phrase_for(request):
    """Confirmation phrase the polished TUI expects for this request."""
    return "APPROVE " + approval_request_sha256(request).removeprefix("sha256:")[:8]


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
        terminal = ScriptedTerminal(["v", "a", phrase_for(request)])
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
                ScriptedTerminal(["a", "yes", "no", "maybe", ""]),
            )

    def test_confirmation_token_derives_from_request_sha256(self):
        request, _evidence, _binding, _identity = fixture()
        digest = "sha256:" + "9f3ab2c1" + "0" * 56
        self.assertEqual(_confirmation_token(digest), "9f3ab2c1")
        self.assertEqual(_confirmation_token(digest), _confirmation_token(digest))
        self.assertEqual(
            phrase_for(request),
            "APPROVE " + _confirmation_token(approval_request_sha256(request)),
        )
        import knowledge_forge.approval_tui as module

        self.assertFalse(hasattr(module, "_suffix"))

    def test_confirmation_mismatch_retry_then_success_records_once(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["a", "yes", "wrong", phrase_for(request)])
        receipt = run_local_review_tui(request, evidence, binding, terminal)
        self.assertEqual(receipt["state"], "REVIEWED_APPROVED")
        rendered = "".join(terminal.output)
        self.assertIn("Mismatch: approval not recorded.", rendered)
        self.assertIn(f"Expected exactly: {phrase_for(request)}", rendered)
        self.assertFalse(receipt["authorization_granted"])

    def test_confirmation_mismatch_fails_closed_after_three_attempts(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["a", "yes", "no", "maybe"])
        with self.assertRaisesRegex(ApprovalContractError, "confirmation mismatch"):
            run_local_review_tui(request, evidence, binding, terminal)
        self.assertIn(f"Expected exactly: {phrase_for(request)}", "".join(terminal.output))

    def test_confirmation_eof_fails_closed(self):
        request, evidence, binding, _identity = fixture()
        with self.assertRaisesRegex(ApprovalContractError, "terminal review cancelled"):
            run_local_review_tui(
                request,
                evidence,
                binding,
                ScriptedTerminal(["a"]),
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
        terminal = ScriptedTerminal(["a", phrase_for(request)])
        with self.assertRaisesRegex(ApprovalContractError, "stale candidate"):
            run_local_review_tui(
                request, evidence, stale, terminal
            )
        self.assertIn("stale or changed", "".join(terminal.output))

    def test_expired_request_shows_warning_and_refuses(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["a", phrase_for(request)])
        with self.assertRaisesRegex(ApprovalContractError, "expired"):
            with patch("knowledge_forge.approval_tui._now", return_value="2026-10-13T16:31:00Z"):
                run_local_review_tui(request, evidence, binding, terminal)
        self.assertIn("REQUEST EXPIRED", "".join(terminal.output))

    def test_request_expiring_while_tui_is_open_refuses_without_receipt(self):
        request, evidence, binding, identity = fixture()
        terminal = ScriptedTerminal(["a", phrase_for(request)])
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
                ScriptedTerminal(["a", phrase_for(request)]),
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

    @skip_bash_launcher_on_windows
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

    @skip_bash_launcher_on_windows
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

    @skip_bash_launcher_on_windows
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

    def test_from_dir_loads_the_canonical_triple(self):
        request, evidence, binding, _identity = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (
                ("approval-request.json", request),
                ("approval-evidence.json", evidence),
                ("current-candidate.json", binding),
            ):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            loaded = _load_review_inputs(None, None, None, str(root))
        self.assertEqual(loaded, (request, evidence, binding))

    @skip_unless_symlinks
    def test_from_dir_refuses_missing_symlinked_or_oversized_inputs(self):
        request, evidence, binding, _identity = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "approval-request.json").write_text(json.dumps(request), encoding="utf-8")
            for pending in ("approval-evidence.json", "current-candidate.json"):
                with self.assertRaisesRegex(ApprovalContractError, "regular non-symlink file"):
                    _load_review_inputs(None, None, None, str(root))
                value = binding if "candidate" in pending else evidence
                (root / pending).write_text(json.dumps(value), encoding="utf-8")
            # all three present and loadable now
            _load_review_inputs(None, None, None, str(root))
            # symlinked member refused
            target = root / "plain.json"
            target.write_text("{}", encoding="utf-8")
            (root / "current-candidate.json").unlink()
            (root / "current-candidate.json").symlink_to(target)
            with self.assertRaisesRegex(ApprovalContractError, "regular non-symlink file"):
                _load_review_inputs(None, None, None, str(root))
            # oversized member refused
            (root / "current-candidate.json").unlink()
            (root / "current-candidate.json").write_text("x" * (256 * 1024 + 1), encoding="utf-8")
            with self.assertRaisesRegex(ApprovalContractError, "exceeds"):
                _load_review_inputs(None, None, None, str(root))

    def test_from_dir_flag_conflicts_fail_closed(self):
        request, evidence, binding, _identity = fixture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, value in (
                ("approval-request.json", request),
                ("approval-evidence.json", evidence),
                ("current-candidate.json", binding),
            ):
                (root / name).write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ApprovalContractError, "cannot be combined"):
                _load_review_inputs("x.json", None, None, str(root))
            with self.assertRaisesRegex(ApprovalContractError, "required unless"):
                _load_review_inputs(None, None, None, None)
            receipt_out = str(root / "receipt.json")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["review", "--from-dir", str(root), "--request", "x", "--receipt-out", receipt_out])
            self.assertEqual(code, 2)
            self.assertIn("cannot be combined", err.getvalue())
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                code = main(["review", "--receipt-out", receipt_out])
            self.assertEqual(code, 2)
            self.assertIn("required unless", err.getvalue())
            self.assertFalse(Path(receipt_out).exists())

    def test_evidence_view_guides_operator_verification(self):
        request, evidence, binding, _identity = fixture()
        terminal = ScriptedTerminal(["v", "q"])
        result = run_local_review_tui(request, evidence, binding, terminal)
        rendered = "".join(terminal.output)
        self.assertIn("Exact bounded evidence", rendered)
        self.assertIn("Labels are opaque identifiers shown on purpose", rendered)
        self.assertIn("independently recompute and compare each sha256", rendered)
        self.assertIn("APPROVE plus the first 8 hex characters of", rendered)
        for marker in ("/home/synthetic/private/source.py", "ghp_syntheticsecret"):
            self.assertNotIn(marker, rendered)
        self.assertIsNone(result["receipt"])

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
                ScriptedTerminal(["a", phrase_for(request)]),
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
