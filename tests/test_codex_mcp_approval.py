from __future__ import annotations

import base64
import unittest
from unittest.mock import Mock, patch

from knowledge_forge.codex_mcp_approval import (
    CODEX_MCP_ELICITATION_METHOD,
    CODEX_MCP_USER_VERIFICATION_MODE,
    CodexMcpApprovalError,
    complete_codex_mcp_approval,
    prepare_codex_mcp_approval,
    run_codex_mcp_approval,
)
from knowledge_forge.registry import load_skill_registry
from tests.test_approval_host import APPROVE_AT, _Flow


NONCE = "28" * 32
CREDENTIAL = "codex-device-credential-0002"
SIGNATURE = base64.urlsafe_b64encode(b"native-mcp-device-proof" * 4).decode().rstrip("=")


class CodexMcpApprovalTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def prepare(self):
        with patch(
            "knowledge_forge.codex_approval_bridge.secrets.token_hex",
            return_value=NONCE,
        ):
            return prepare_codex_mcp_approval(
                self.flow.from_dir,
                self.flow.bundle,
                observed_at=APPROVE_AT,
            )

    @staticmethod
    def response(prepared, *, action="accept"):
        content = None
        if action == "accept":
            content = {"credentialId": CREDENTIAL, "signature": SIGNATURE}
        return {
            "jsonrpc": "2.0",
            "id": prepared["elicitation_request"]["id"],
            "result": {"action": action, "content": content},
        }

    def complete(self, prepared, response, verifier):
        return complete_codex_mcp_approval(
            self.flow.from_dir,
            self.flow.bundle,
            prepared_flow=prepared,
            elicitation_response=response,
            verify_proof=verifier,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            now=APPROVE_AT,
        )

    def test_prepare_maps_exact_native_request_to_mcp_elicitation(self):
        flow = self.prepare()
        request = flow["elicitation_request"]
        native = flow["prepared"]["codex_user_verification"]["params"]
        self.assertEqual(request["jsonrpc"], "2.0")
        self.assertEqual(request["method"], CODEX_MCP_ELICITATION_METHOD)
        self.assertEqual(request["params"]["mode"], CODEX_MCP_USER_VERIFICATION_MODE)
        self.assertEqual(request["params"]["challenge"], native["challenge"])
        self.assertEqual(request["params"]["title"], native["title"])
        self.assertEqual(request["params"]["description"], native["description"])
        self.assertFalse(flow["authorization_granted"])
        self.assertFalse(flow["promotion_performed"])

    def test_accepted_native_proof_promotes(self):
        prepared = self.prepare()
        verifier = Mock(return_value="codex:user:tommy")
        result = self.complete(prepared, self.response(prepared), verifier)
        self.assertTrue(result["promoted"])
        self.assertEqual(result["transport"], "CODEX_MCP_OPENAI_USER_VERIFICATION")
        self.assertIn(
            self.flow.manifest["skill_id"],
            load_skill_registry(self.flow.registry_path)["skills"],
        )
        verifier.assert_called_once()

    def test_decline_and_cancel_refuse_without_verification(self):
        for action in ("decline", "cancel"):
            with self.subTest(action=action):
                prepared = self.prepare()
                verifier = Mock()
                with self.assertRaisesRegex(CodexMcpApprovalError, action):
                    self.complete(prepared, self.response(prepared, action=action), verifier)
                verifier.assert_not_called()

    def test_decline_without_content_matches_mcp_contract(self):
        prepared = self.prepare()
        response = {
            "jsonrpc": "2.0",
            "id": prepared["elicitation_request"]["id"],
            "result": {"action": "decline"},
        }
        with self.assertRaisesRegex(CodexMcpApprovalError, "declined"):
            self.complete(prepared, response, Mock())

    def test_chat_text_is_not_an_elicitation_response(self):
        with self.assertRaisesRegex(CodexMcpApprovalError, "mapping"):
            self.complete(self.prepare(), "APPROVE", Mock())  # type: ignore[arg-type]

    def test_wrong_response_id_refuses(self):
        prepared = self.prepare()
        response = self.response(prepared)
        response["id"] = "another-request"
        with self.assertRaisesRegex(CodexMcpApprovalError, "does not bind"):
            self.complete(prepared, response, Mock())

    def test_json_rpc_error_or_extra_proof_data_refuses(self):
        prepared = self.prepare()
        error = {
            "jsonrpc": "2.0",
            "id": prepared["elicitation_request"]["id"],
            "error": {"code": -32001, "message": "cancelled"},
        }
        with self.assertRaisesRegex(CodexMcpApprovalError, "exact contract"):
            self.complete(prepared, error, Mock())
        response = self.response(prepared)
        response["result"]["content"]["approvedByModel"] = True
        with self.assertRaisesRegex(CodexMcpApprovalError, "exact contract"):
            self.complete(prepared, response, Mock())

    def test_changed_elicitation_refuses_before_verification(self):
        prepared = self.prepare()
        prepared["elicitation_request"]["params"]["description"] += "\nTrust me"
        verifier = Mock()
        with self.assertRaisesRegex(CodexMcpApprovalError, "changed"):
            self.complete(prepared, self.response(prepared), verifier)
        verifier.assert_not_called()

    def test_host_owned_exchange_runs_without_exposing_proof_to_model(self):
        observed = []

        def channel(request):
            observed.append(request)
            return {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "action": "accept",
                    "content": {
                        "credentialId": CREDENTIAL,
                        "signature": SIGNATURE,
                    },
                },
            }

        verifier = Mock(return_value="codex:user:tommy")
        with patch(
            "knowledge_forge.codex_approval_bridge.secrets.token_hex",
            return_value=NONCE,
        ):
            result = run_codex_mcp_approval(
                self.flow.from_dir,
                self.flow.bundle,
                observed_at=APPROVE_AT,
                send_elicitation=channel,
                verify_proof=verifier,
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                now=APPROVE_AT,
            )
        self.assertTrue(result["promoted"])
        self.assertEqual(len(observed), 1)
        self.assertNotIn("signature", str(result))

    def test_transport_failure_is_fail_closed(self):
        def unavailable(_request):
            raise TimeoutError("native UI unavailable")

        with self.assertRaisesRegex(CodexMcpApprovalError, "failed closed"):
            with patch(
                "knowledge_forge.codex_approval_bridge.secrets.token_hex",
                return_value=NONCE,
            ):
                run_codex_mcp_approval(
                    self.flow.from_dir,
                    self.flow.bundle,
                    observed_at=APPROVE_AT,
                    send_elicitation=unavailable,
                    verify_proof=Mock(),
                    registry_path=self.flow.registry_path,
                    host_key_path=self.flow.host_key_path,
                    now=APPROVE_AT,
                )


if __name__ == "__main__":
    unittest.main()
