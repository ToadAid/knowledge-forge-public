from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest

from _platform_support import skip_posix_file_modes_on_windows

from knowledge_forge.approval import (
    ApprovalContractError,
    approval_request_sha256,
)
from knowledge_forge.approve_tui import (
    _confirmation_token,
    _run_approve_tui_at,
    main,
    run_setup,
)
from knowledge_forge.approval_host import ApprovalHostError
from tests.test_approval_host import APPROVE_AT, EXPIRES_AT, _Flow


def phrase_for(request) -> str:
    return "APPROVE " + _confirmation_token(approval_request_sha256(request))


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


class ApproveTuiTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def test_noninteractive_input_and_output_refuse(self):
        with self.assertRaisesRegex(
            ApprovalContractError, "TTY input is required"
        ):
            _run_approve_tui_at(
                self.flow.from_dir, ScriptedTerminal([], input_tty=False),
                bundle_dir=self.flow.bundle, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )
        with self.assertRaisesRegex(
            ApprovalContractError, "TTY output is required"
        ):
            _run_approve_tui_at(
                self.flow.from_dir, ScriptedTerminal([], output_tty=False),
                bundle_dir=self.flow.bundle, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )

    def test_missing_request_dir_refuses(self):
        missing = self.flow.root / "no-such-dir"
        with self.assertRaisesRegex(ApprovalContractError, "approval request"):
            _run_approve_tui_at(
                missing, ScriptedTerminal([]),
                bundle_dir=None, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )

    def test_full_approve_promotes_for_real_and_writes_receipts(self):
        terminal = ScriptedTerminal([phrase_for(self.flow.request)])
        payload = _run_approve_tui_at(
            self.flow.from_dir, terminal,
            bundle_dir=self.flow.bundle, state_root=self.flow.state,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            host_id="local-terminal-shell", decided_by="tommy:manual",
            approved_at=APPROVE_AT,
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["promoted"])
        self.assertFalse(payload["model_authorized"])
        self.assertEqual(payload["host_id"], "local-terminal-shell")
        self.assertTrue(Path(payload["decision_path"]).is_file())
        decision = json.loads(Path(payload["decision_path"]).read_text())
        self.assertEqual(decision["decision"], "APPROVED")
        self.assertEqual(
            decision["request_sha256"], approval_request_sha256(self.flow.request)
        )
        registry_text = self.flow.registry_path.read_text()
        self.assertIn("PROMOTED", registry_text)
        self.assertIn(self.flow.manifest["skill_id"], registry_text)
        receipts = [
            self.flow.state / "lifecycle.promoted.json",
            self.flow.state / "receipt.json",
        ]
        for path in receipts:
            self.assertTrue(path.is_file(), path)
        lifecycle = json.loads(
            (self.flow.state / "lifecycle.promoted.json").read_text()
        )
        self.assertEqual(lifecycle.get("state"), "PROMOTED")
        receipt = json.loads((self.flow.state / "receipt.json").read_text())
        self.assertTrue(receipt.get("promoted") or receipt.get("skill_id"))
        joined = "\n".join(terminal.output)
        self.assertIn("PROMOTED", joined)
        self.assertIn("Type: APPROVE <confirmation phrase>", joined)

    def test_approve_without_bundle_override_uses_promotion_bundle_subdir(self):
        # The pilot convention: the agent stages the validated bundle as
        # <request-dir>/promotion-bundle — one word, no extra flags.
        bundle = self.flow.from_dir / "promotion-bundle"
        for relative, payload in self.flow.files.items():
            target = bundle / "package" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (bundle / "lifecycle.validated.json").write_bytes(
            json.dumps(self.flow.validated).encode("utf-8")
        )
        terminal = ScriptedTerminal([phrase_for(self.flow.request)])
        payload = _run_approve_tui_at(
            self.flow.from_dir, terminal,
            bundle_dir=None, state_root=self.flow.state,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            host_id=None, decided_by=None, approved_at=APPROVE_AT,
        )
        self.assertTrue(payload["promoted"])

    def test_reject_records_signed_decision_without_any_promotion(self):
        reject_phrase = "REJECT " + _confirmation_token(
            approval_request_sha256(self.flow.request)
        )
        terminal = ScriptedTerminal([reject_phrase])
        payload = _run_approve_tui_at(
            self.flow.from_dir, terminal,
            bundle_dir=self.flow.bundle, state_root=self.flow.state,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            host_id=None, decided_by=None, approved_at=APPROVE_AT,
        )
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["promoted"])
        self.assertEqual(payload["decision"], "REJECTED")
        decision = json.loads(Path(payload["decision_path"]).read_text())
        self.assertEqual(decision["decision"], "REJECTED")
        self.assertNotIn("PROMOTED", self.flow.registry_path.read_text())
        self.assertFalse((self.flow.state / "receipt.json").exists())
        self.assertIn("Nothing was promoted", "\n".join(terminal.output))

    def test_confirmation_mismatch_fails_closed_after_three_attempts(self):
        terminal = ScriptedTerminal(["APPROVE 00000000", "APPROVE deadbeef", "APPROVE ffffffff"])
        with self.assertRaisesRegex(ApprovalContractError, "mismatch after 3"):
            _run_approve_tui_at(
                self.flow.from_dir, terminal,
                bundle_dir=self.flow.bundle, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )
        joined = "\n".join(terminal.output)
        self.assertIn("2 attempt(s) remain", joined)
        self.assertIn("1 attempt(s) remain", joined)
        self.assertIn("REFUSED: approval confirmation mismatch after 3", joined)

    def test_eof_fails_closed(self):
        terminal = ScriptedTerminal([])
        with self.assertRaisesRegex(ApprovalContractError, "no decision entered"):
            _run_approve_tui_at(
                self.flow.from_dir, terminal,
                bundle_dir=self.flow.bundle, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )
        self.assertIn("REFUSED", "\n".join(terminal.output))

    def test_expired_request_refuses_before_any_prompt(self):
        terminal = ScriptedTerminal([])
        with self.assertRaisesRegex(ApprovalContractError, "expired"):
            _run_approve_tui_at(
                self.flow.from_dir, terminal,
                bundle_dir=self.flow.bundle, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at="2026-10-16T23:59:59Z",
            )
        self.assertEqual(terminal.answers, [])
        self.assertIn("REQUEST EXPIRED", "\n".join(terminal.output))

    def test_missing_bundle_reports_exact_missing_dependency(self):
        terminal = ScriptedTerminal([])
        with self.assertRaisesRegex(ApprovalContractError, "promotion bundle"):
            _run_approve_tui_at(
                self.flow.from_dir, terminal,
                bundle_dir=None, state_root=self.flow.state,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )
        self.assertIn("stage the validated bundle", "\n".join(terminal.output))

    def test_missing_registry_after_approval_names_admin_setup(self):
        empty_state = self.flow.root / "uninitialized-state"
        empty_state.mkdir()
        terminal = ScriptedTerminal([phrase_for(self.flow.request)])
        with self.assertRaisesRegex(ApprovalHostError, "approve --setup"):
            _run_approve_tui_at(
                self.flow.from_dir, terminal,
                bundle_dir=self.flow.bundle, state_root=empty_state,
                registry_path=None,
                host_key_path=self.flow.host_key_path,
                host_id=None, decided_by=None, approved_at=APPROVE_AT,
            )
        self.assertIn("approve --setup", "\n".join(terminal.output))
        self.assertIn("REFUSED: governed registry not found", "\n".join(terminal.output))
        self.assertFalse((empty_state / "receipt.json").exists())
        self.assertFalse((empty_state / "lifecycle.promoted.json").exists())

    @skip_posix_file_modes_on_windows
    def test_setup_creates_state_root_key_and_registry_then_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_root = Path(tmp) / "approval-host"
            payload = run_setup(state_root, now=APPROVE_AT)
            self.assertTrue(payload["ok"])
            self.assertTrue(Path(payload["host_key_path"]).is_file())
            self.assertTrue(Path(payload["registry_path"]).is_file())
            self.assertEqual(payload["registry_created"], True)
            self.assertEqual(state_root.stat().st_mode & 0o777, 0o700)
            self.assertEqual(Path(payload["host_key_path"]).stat().st_mode & 0o777, 0o600)
            with self.assertRaisesRegex(ApprovalHostError, "already exists"):
                run_setup(state_root, now=APPROVE_AT)

    def test_piped_cli_refuses_and_reports_tty_dependency(self):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(self.flow.from_dir), "--bundle", str(self.flow.bundle),
                         "--registry", str(self.flow.registry_path),
                         "--host-key", str(self.flow.host_key_path)])
        self.assertEqual(code, 2)
        payload = json.loads(stdout.getvalue())
        self.assertFalse(payload["promoted"])
        self.assertIn("TTY", payload["error"])


if __name__ == "__main__":
    import unittest as _unittest
    _unittest.main()