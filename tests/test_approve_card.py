"""Claude-side on-screen approval card: desktop host through the adapter.

Covers the card core decision flow (scripted desktop window): a real
Approve & Promote click plus typed confirm phrase completes the unchanged
verified promotion chain through the generic host adapter; Reject records a
signed no-op; close-without-decision and 3-mismatch confirmations refuse
fail-closed; expired requests refuse before any prompt. The real zenity
window is exercised by a manual smoke (GUI is never a CI surface).
"""

from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest import mock

from _platform_support import skip_posix_file_modes_on_windows

from knowledge_forge.approval import approval_request_sha256
from knowledge_forge.approval_tui import _confirmation_token
from knowledge_forge.approve_card import (
    ApproveCardError,
    ScriptedCard,
    _card_text,
    _run_approve_card_at,
    run_setup,
)
from knowledge_forge.approval_host import ApprovalHostError
from knowledge_forge.generic_host_adapter import HOST_ADAPTER_PROFILES
from knowledge_forge.registry import load_skill_registry
from tests.test_approval_host import _Flow, APPROVE_AT, EXPIRES_AT


class ApproveCardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def _token(self) -> str:
        return _confirmation_token(approval_request_sha256(self.flow.request))

    def _card(self, decisions, phrases) -> ScriptedCard:
        return ScriptedCard(list(decisions), list(phrases))

    def _run(self, card, **overrides):
        args = dict(
            bundle_dir=self.flow.bundle,
            state_root=self.flow.state,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            decided_by="local-desktop-user:tommy",
            now=APPROVE_AT,
        )
        args.update(overrides)
        return _run_approve_card_at(self.flow.from_dir, card, **args)

    def test_full_card_approve_promotes_for_real(self) -> None:
        phrase = f"APPROVE {self._token()}"
        card = self._card(["APPROVE"], [phrase])
        payload = self._run(card)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["promoted"])
        self.assertFalse(payload["chat_text_authorized"])
        self.assertEqual(payload["adapter_id"], "claude")
        registry = load_skill_registry(self.flow.registry_path)
        versions = registry["skills"][self.flow.manifest["skill_id"]]["versions"]
        promoted = [
            entry for _, entry in sorted(versions.items())
            if entry["lifecycle_record"]["state"] == "PROMOTED"
        ]
        self.assertTrue(promoted)
        # The card window sees the approved pipeline stamps.
        self.assertNotIn("authorization_granted", payload)

    def test_reject_records_no_promotion(self) -> None:
        card = self._card(["REJECT"], [])
        payload = self._run(card)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["promoted"])
        self.assertEqual(payload["status"], "REJECTED")
        registry = load_skill_registry(self.flow.registry_path)
        self.assertNotIn(self.flow.manifest["skill_id"], registry["skills"])

    def test_close_without_decision_refuses_fail_closed(self) -> None:
        card = self._card([None], [])
        payload = self._run(card)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["promoted"])
        self.assertIn("closed without a decision", payload["refusal"])
        self.assertTrue(
            any("fail-closed" in text for text in card.shown)
        )

    def test_phrase_mismatch_three_times_refuses(self) -> None:
        bad = f"APPROVE {'0' * 8}"
        card = self._card(["APPROVE"], [bad, bad, bad])
        payload = self._run(card)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["promoted"])
        self.assertIn("confirmation mismatch", payload["refusal"])

    def test_card_text_states_laws_and_caveat(self) -> None:
        card = self._card(["REJECT"], [])
        self._run(card)
        text = card.shown[0]
        self.assertIn("VALIDATED != PROMOTED", text)
        self.assertIn("procedural", text)
        self.assertIn("Chat text can never authorize", text)
        self.assertIn(f"APPROVE {self._token()}", text)
        self.assertIn(self.flow.manifest["skill_id"], text)
        self.assertIn(EXPIRES_AT, text)

    def test_expired_request_refuses_before_any_prompt(self) -> None:
        from knowledge_forge.inline_approval import InlineApprovalError

        card = self._card([], [])
        with self.assertRaises(InlineApprovalError):
            self._run(card, now=EXPIRES_AT)
            # +1s would be better; EXPIRES_AT itself is not yet expired.
        self.assertEqual(
            [text for text in card.shown if "REFUSED" in text],
            card.shown,
            "after expiry only the refusal message should be shown",
        )

    @skip_posix_file_modes_on_windows
    def test_setup_provisions_state_root_key_and_registry(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as sandbox:
            root = Path(sandbox) / "card-state"
            payload = run_setup(root, now=APPROVE_AT)
            self.assertTrue(payload["ok"])
            self.assertEqual(os.stat(root).st_mode & 0o777, 0o700)
            self.assertEqual(
                os.stat(payload["host_key_path"]).st_mode & 0o777, 0o600
            )
            with self.assertRaises(ApprovalHostError):
                run_setup(root, now=APPROVE_AT)

    def test_zenity_card_refuses_without_display_dependency(self) -> None:
        from knowledge_forge.approve_card import ZenityCard

        env = dict(os.environ)
        env.pop("DISPLAY", None)
        env.pop("WAYLAND_DISPLAY", None)
        with mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(ApproveCardError) as ctx:
                ZenityCard()
        self.assertIn("graphical session", str(ctx.exception))

    def test_claude_profile_is_registered(self) -> None:
        profile = HOST_ADAPTER_PROFILES.get("claude")
        self.assertIsNotNone(profile)
        self.assertEqual(profile.approval_decision, "approve-once")
        self.assertEqual(profile.rejection_decision, "deny")
        self.assertEqual(profile.review_surface, "CLAUDE_USER_APPROVAL_CARD")


if __name__ == "__main__":
    raise SystemExit(unittest.main())