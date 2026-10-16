from __future__ import annotations

import unittest
from unittest.mock import patch

from knowledge_forge import generic_host_adapter
from knowledge_forge.generic_host_adapter import (
    GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
    GENERIC_HOST_APPROVAL_EVENT_KIND,
    GenericHostAdapterError,
    complete_generic_host_approval,
    prepare_generic_host_approval,
)
from knowledge_forge.registry import load_skill_registry
from tests.test_approval_host import APPROVE_AT, _Flow


def _event(flow: _Flow, adapter_id: str, decision: str) -> dict:
    card = prepare_generic_host_approval(
        flow.from_dir, flow.bundle, adapter_id=adapter_id, observed_at=APPROVE_AT
    )
    profile = card["host_adapter"]
    surface_principals = {
        "openclaw": "openclaw:operator.approvals",
        "hermes": "hermes:approval-transport-user",
        "generic-native-host": "native-host:user:42",
        "codex": "codex:user:tommy",
    }
    return {
        "schema_version": GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
        "kind": GENERIC_HOST_APPROVAL_EVENT_KIND,
        "adapter_id": adapter_id,
        "approval_id": f"{adapter_id}:approval:00000001",
        "request_sha256": card["request_sha256"],
        "decision": decision,
        "reviewer_principal": surface_principals[adapter_id],
        "decided_at": APPROVE_AT,
        "authentication": {
            "mechanism": profile["authentication_mechanism"],
            "one_shot": True,
            "bypass_active": False,
            "review_surface": profile["review_surface"],
        },
    }


class GenericHostAdapterTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def _complete(self, adapter_id: str, event: dict):
        return complete_generic_host_approval(
            self.flow.from_dir,
            self.flow.bundle,
            host_event=event,
            adapter_id=adapter_id,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            now=APPROVE_AT,
        )

    def test_openclaw_one_shot_operator_approval_promotes(self):
        result = self._complete(
            "openclaw", _event(self.flow, "openclaw", "allow-once")
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(result["adapter_id"], "openclaw")
        self.assertEqual(
            result["host_authentication_mechanism"], "plugin.approval"
        )
        self.assertIn(self.flow.manifest["skill_id"], load_skill_registry(
            self.flow.registry_path
        )["skills"])

    def test_hermes_one_shot_transport_approval_promotes(self):
        result = self._complete(
            "hermes", _event(self.flow, "hermes", "once")
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(result["adapter_id"], "hermes")

    def test_provider_neutral_one_shot_approval_promotes(self):
        result = self._complete(
            "generic-native-host",
            _event(self.flow, "generic-native-host", "approve-once"),
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(result["adapter_id"], "generic-native-host")

    def test_codex_verified_one_shot_approval_promotes(self):
        result = self._complete(
            "codex", _event(self.flow, "codex", "approve-once")
        )
        self.assertTrue(result["promoted"])
        self.assertEqual(result["adapter_id"], "codex")

    def test_denial_is_terminal_without_promotion(self):
        result = self._complete(
            "openclaw", _event(self.flow, "openclaw", "deny")
        )
        self.assertEqual(result["status"], "REJECTED")
        self.assertFalse(result["promoted"])
        self.assertEqual(load_skill_registry(self.flow.registry_path)["skills"], {})

    def test_openclaw_allow_always_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["decision"] = "allow-always"
        with self.assertRaisesRegex(GenericHostAdapterError, "one-shot"):
            self._complete("openclaw", event)

    def test_hermes_session_and_always_are_refused(self):
        for decision in ("session", "always"):
            with self.subTest(decision=decision):
                event = _event(self.flow, "hermes", "once")
                event["decision"] = decision
                with self.assertRaisesRegex(GenericHostAdapterError, "one-shot"):
                    self._complete("hermes", event)

    def test_hermes_yolo_or_other_bypass_is_refused(self):
        event = _event(self.flow, "hermes", "once")
        event["authentication"]["bypass_active"] = True
        with self.assertRaisesRegex(GenericHostAdapterError, "bypass/YOLO"):
            self._complete("hermes", event)

    def test_reusable_host_approval_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["authentication"]["one_shot"] = False
        with self.assertRaisesRegex(GenericHostAdapterError, "reusable"):
            self._complete("openclaw", event)

    def test_stale_or_future_host_event_time_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["decided_at"] = "2026-10-14T12:29:00Z"
        with self.assertRaisesRegex(GenericHostAdapterError, "trusted completion time"):
            self._complete("openclaw", event)

    def test_malformed_denial_time_is_refused(self):
        event = _event(self.flow, "openclaw", "deny")
        event["decided_at"] = "now"
        with self.assertRaisesRegex(GenericHostAdapterError, "RFC3339"):
            self._complete("openclaw", event)

    def test_mismatched_request_digest_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["request_sha256"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(GenericHostAdapterError, "does not bind"):
            self._complete("openclaw", event)

    def test_adapter_substitution_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["adapter_id"] = "hermes"
        with self.assertRaisesRegex(GenericHostAdapterError, "adapter mismatch"):
            self._complete("openclaw", event)

    def test_request_swap_between_review_and_signing_is_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        original = generic_host_adapter._request_from_card_path

        def swapped_request(path):
            request = original(path)
            request["request_id"] = "request-swapped-after-review"
            return request

        with patch.object(
            generic_host_adapter,
            "_request_from_card_path",
            side_effect=swapped_request,
        ), self.assertRaisesRegex(GenericHostAdapterError, "changed after"):
            self._complete("openclaw", event)

    def test_chat_text_cannot_be_used_as_host_event(self):
        with self.assertRaisesRegex(GenericHostAdapterError, "chat text"):
            complete_generic_host_approval(
                self.flow.from_dir,
                self.flow.bundle,
                host_event="APPROVE",  # type: ignore[arg-type]
                adapter_id="openclaw",
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                now=APPROVE_AT,
            )

    def test_unknown_or_extra_event_fields_are_refused(self):
        event = _event(self.flow, "openclaw", "allow-once")
        event["agent_says_approved"] = True
        with self.assertRaisesRegex(GenericHostAdapterError, "exact contract"):
            self._complete("openclaw", event)

    def test_prepared_card_remains_non_authorizing(self):
        card = prepare_generic_host_approval(
            self.flow.from_dir,
            self.flow.bundle,
            adapter_id="hermes",
            observed_at=APPROVE_AT,
        )
        self.assertFalse(card["authorization_granted"])
        self.assertFalse(card["promotion_performed"])
        self.assertFalse(card["host_adapter"]["persistent_approval_allowed"])
        self.assertFalse(card["host_adapter"]["bypass_allowed"])


if __name__ == "__main__":
    unittest.main()
