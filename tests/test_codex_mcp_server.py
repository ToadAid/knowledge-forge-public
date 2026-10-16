from __future__ import annotations

from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from _platform_support import skip_linux_account_module_on_windows

skip_linux_account_module_on_windows("codex_mcp_server")

from knowledge_forge.codex_mcp_server import CodexApprovalMcpServer, TOOL_NAME
from tests.test_approval_host import APPROVE_AT, _Flow
from tests.test_codex_mcp_approval import CREDENTIAL, SIGNATURE


class CodexMcpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)
        self.sent_frames = []

    def run_server(self, messages: list[dict]) -> list[dict]:
        source = StringIO("".join(json.dumps(item) + "\n" for item in messages))
        destination = StringIO()
        server = CodexApprovalMcpServer(
            workspace_root=self.flow.root,
            operator_socket=self.flow.root / "operator.sock",
            input_stream=source,
            output_stream=destination,
        )
        fake_socket = Mock()
        protected = {
            "status": "AWAITING_CODEX_NATIVE_USER_VERIFICATION",
            "challenge_id": "protected-session-1",
            "elicitation": {
                "jsonrpc": "2.0",
                "id": "codex-protected:protected-session-1",
                "method": "elicitation/create",
                "params": {
                    "mode": "openai/userVerification",
                    "challenge": "Y2hhbGxlbmdl",
                    "title": "Approve Knowledge Forge promotion",
                    "description": "Exact protected candidate",
                },
            },
        }
        promoted = {
            "status": "PROMOTED",
            "skill_id": self.flow.manifest["skill_id"],
            "skill_version": self.flow.manifest["skill_version"],
            "principal": "codex:user:tommy",
            "receipt_sha256": "sha256:" + "ab" * 32,
            "paths": {},
        }
        completion = (
            {"status": "REFUSED", "error": "Codex native approval was declined"}
            if any(
                item.get("result", {}).get("action") == "decline"
                for item in messages
                if isinstance(item, dict)
            )
            else promoted
        )
        with patch("knowledge_forge.codex_mcp_server._now", return_value=APPROVE_AT), \
             patch("knowledge_forge.codex_mcp_server.socket.socket", return_value=fake_socket), \
             patch("knowledge_forge.codex_mcp_server.snapshot_bundle", return_value={"synthetic": True}), \
             patch("knowledge_forge.codex_mcp_server.send_frame", side_effect=lambda _c, value: self.sent_frames.append(value)), \
             patch("knowledge_forge.codex_mcp_server.recv_frame", side_effect=[protected, completion]):
            server.serve()
        return [json.loads(line) for line in destination.getvalue().splitlines()]

    def test_lists_one_bounded_mutating_tool(self) -> None:
        output = self.run_server(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ]
        )
        tool = output[1]["result"]["tools"][0]
        self.assertEqual(tool["name"], TOOL_NAME)
        self.assertFalse(tool["annotations"]["readOnlyHint"])
        self.assertTrue(tool["annotations"]["destructiveHint"])
        self.assertNotIn("complete", json.dumps(tool).lower())

    def test_native_elicitation_verified_then_promotes(self) -> None:
        call = {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {
                    "request_dir": str(self.flow.from_dir),
                    "bundle_dir": str(self.flow.bundle),
                },
            },
        }
        native = {
            "jsonrpc": "2.0",
            "id": "codex-protected:protected-session-1",
            "result": {
                "action": "accept",
                "content": {"credentialId": CREDENTIAL, "signature": SIGNATURE},
            },
        }
        output = self.run_server([call, native])
        self.assertEqual(output[0]["method"], "elicitation/create")
        self.assertEqual(
            output[0]["params"]["mode"], "openai/userVerification"
        )
        result = output[1]["result"]
        self.assertFalse(result["isError"])
        self.assertTrue(result["structuredContent"]["promoted"])
        self.assertNotIn("signature", json.dumps(result))
        self.assertEqual(self.sent_frames[0]["operation"], "PREPARE_CODEX")
        self.assertEqual(self.sent_frames[1]["operation"], "COMPLETE_CODEX")
        self.assertNotIn("signature", json.dumps(result))

    def test_decline_never_promotes(self) -> None:
        decline_call = {
            "jsonrpc": "2.0",
            "id": 8,
            "method": "tools/call",
            "params": {
                "name": TOOL_NAME,
                "arguments": {
                    "request_dir": str(self.flow.from_dir),
                    "bundle_dir": str(self.flow.bundle),
                },
            },
        }
        decline = {
            "jsonrpc": "2.0",
            "id": "codex-protected:protected-session-1",
            "result": {"action": "decline"},
        }
        output = self.run_server([decline_call, decline])
        self.assertTrue(output[-1]["result"]["isError"])
        self.assertFalse(output[-1]["result"]["structuredContent"]["promoted"])

    def test_path_outside_workspace_is_refused_before_socket_connection(self) -> None:
        with tempfile.TemporaryDirectory() as outside:
            source = StringIO(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 9,
                        "method": "tools/call",
                        "params": {
                            "name": TOOL_NAME,
                            "arguments": {
                                "request_dir": str(Path(outside)),
                                "bundle_dir": str(self.flow.bundle),
                            },
                        },
                    }
                )
                + "\n"
            )
            destination = StringIO()
            server = CodexApprovalMcpServer(
                workspace_root=self.flow.root,
                operator_socket=self.flow.root / "operator.sock",
                input_stream=source,
                output_stream=destination,
            )
            with patch("knowledge_forge.codex_mcp_server.socket.socket") as opened:
                server.serve()
            result = json.loads(destination.getvalue())["result"]
            self.assertTrue(result["isError"])
            self.assertFalse(result["structuredContent"]["promoted"])
            self.assertIn(
                "outside the configured workspace", result["content"][0]["text"]
            )
            opened.assert_not_called()


if __name__ == "__main__":
    raise SystemExit(unittest.main())
