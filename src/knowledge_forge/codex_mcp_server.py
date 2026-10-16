"""Minimal stdio MCP server for one Codex-native promotion operation.

The model may request the operation, but this unprivileged server only relays
the native ``openai/userVerification`` exchange. Proof verification and
completion stay in the protected operator. No completion or signing primitive
is advertised as an MCP tool.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import sys
from typing import Any, IO, Mapping

from .inline_approval import prepare_inline_approval
from .protected_operator_client import snapshot_bundle
from .protected_operator_service import CODEX_SOCKET_PATH, recv_frame, send_frame


SERVER_NAME = "knowledge-forge-codex-approval"
SERVER_VERSION = "0.1.0"
PROTOCOL_VERSION = "2025-11-25"
TOOL_NAME = "request_knowledge_promotion"


class CodexMcpServerError(ValueError):
    """The bounded MCP operation could not proceed."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _beneath(root: Path, supplied: Any, label: str) -> Path:
    if not isinstance(supplied, str) or not supplied:
        raise CodexMcpServerError(f"{label} must be a nonempty path string")
    candidate = Path(supplied)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CodexMcpServerError(f"{label} is outside the configured workspace") from exc
    if not resolved.is_dir():
        raise CodexMcpServerError(f"{label} must be a directory")
    return resolved


def _tool() -> dict[str, Any]:
    return {
        "name": TOOL_NAME,
        "title": "Approve and promote validated knowledge",
        "description": (
            "Request one exact validated Knowledge Forge promotion. The tool "
            "opens Codex native user verification; chat text and ordinary tool "
            "confirmation do not authorize it. Promotion occurs only after an "
            "enrolled device proof verifies."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "request_dir": {
                    "type": "string",
                    "description": "Approval-request directory beneath the configured workspace.",
                },
                "bundle_dir": {
                    "type": "string",
                    "description": "Validated bundle directory beneath the configured workspace.",
                },
            },
            "required": ["request_dir", "bundle_dir"],
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    }


class CodexApprovalMcpServer:
    def __init__(
        self,
        *,
        workspace_root: str | Path,
        operator_socket: str | Path,
        input_stream: IO[str],
        output_stream: IO[str],
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve(strict=True)
        self.operator_socket = Path(operator_socket)
        if self.operator_socket != CODEX_SOCKET_PATH:
            raise CodexMcpServerError('Only the Codex proof-only socket is permitted; no fallback')
        self.input = input_stream
        self.output = output_stream

    def _send(self, value: Mapping[str, Any]) -> None:
        self.output.write(json.dumps(dict(value), separators=(",", ":")) + "\n")
        self.output.flush()

    def _response(self, request_id: Any, result: Mapping[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "id": request_id, "result": dict(result)})

    def _error(self, request_id: Any, code: int, message: str) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message[:1000]},
            }
        )

    def _read_for_id(self, expected_id: str) -> dict[str, Any]:
        for line in self.input:
            if len(line.encode("utf-8")) > 1024 * 1024:
                raise CodexMcpServerError("MCP response exceeded one MiB")
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexMcpServerError("MCP client returned invalid JSON") from exc
            if isinstance(message, dict) and message.get("id") == expected_id:
                return message
        raise CodexMcpServerError("MCP client closed during native verification")

    def _call(self, request_id: Any, params: Any) -> None:
        connection: socket.socket | None = None
        try:
            if not isinstance(params, dict) or set(params) != {"name", "arguments"}:
                raise CodexMcpServerError("tools/call fields must match exact contract")
            if params["name"] != TOOL_NAME:
                raise CodexMcpServerError("unknown Knowledge Forge tool")
            arguments = params["arguments"]
            if not isinstance(arguments, dict) or set(arguments) != {
                "request_dir", "bundle_dir"
            }:
                raise CodexMcpServerError("tool arguments must match exact contract")
            request_dir = _beneath(
                self.workspace_root, arguments["request_dir"], "request directory"
            )
            bundle_dir = _beneath(
                self.workspace_root, arguments["bundle_dir"], "bundle directory"
            )
            observed_at = _now()
            prepare_inline_approval(
                request_dir, bundle_dir, observed_at=observed_at
            )
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.settimeout(30)
            connection.connect(str(self.operator_socket))
            send_frame(
                connection,
                {"operation": "PREPARE_CODEX", "bundle": snapshot_bundle(str(bundle_dir))},
            )
            protected = recv_frame(connection)
            if set(protected) != {"status", "challenge_id", "elicitation"} or (
                protected.get("status") != "AWAITING_CODEX_NATIVE_USER_VERIFICATION"
            ):
                raise CodexMcpServerError(
                    str(protected.get("error", "protected operator refused preparation"))
                )
            elicitation = protected["elicitation"]
            if not isinstance(elicitation, dict):
                raise CodexMcpServerError("protected operator returned invalid elicitation")
            elicitation_id = elicitation["id"]
            if elicitation_id == request_id:
                raise CodexMcpServerError("MCP request identifiers collided")
            self._send(elicitation)
            native_response = self._read_for_id(elicitation_id)
            send_frame(
                connection,
                {
                    "operation": "COMPLETE_CODEX",
                    "challenge_id": protected["challenge_id"],
                    "elicitation_response": native_response,
                },
            )
            result = recv_frame(connection)
            if result.get("status") != "PROMOTED":
                raise CodexMcpServerError(
                    str(result.get("error", "protected operator refused promotion"))
                )
            public = {
                "promoted": True,
                "status": result["status"],
                "skill_id": result.get("skill_id"),
                "skill_version": result.get("skill_version"),
                "receipt_sha256": result.get("receipt_sha256"),
                "transport": "CODEX_MCP_PROTECTED_OPERATOR",
            }
            self._response(
                request_id,
                {
                    "structuredContent": public,
                    "content": [
                        {
                            "type": "text",
                            "text": "Knowledge Forge promotion completed with verified native approval.",
                        }
                    ],
                    "isError": False,
                },
            )
        except Exception as exc:
            self._response(
                request_id,
                {
                    "content": [{"type": "text", "text": str(exc)[:1000]}],
                    "isError": True,
                    "structuredContent": {"promoted": False},
                },
            )
        finally:
            if connection is not None:
                connection.close()

    def serve(self) -> None:
        for line in self.input:
            if len(line.encode("utf-8")) > 1024 * 1024:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                self._error(None, -32700, "parse error")
                continue
            if not isinstance(request, dict):
                self._error(None, -32600, "invalid request")
                continue
            method = request.get("method")
            request_id = request.get("id")
            if method in {"notifications/initialized", "notifications/cancelled"}:
                continue
            if method == "initialize":
                self._response(
                    request_id,
                    {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                        "instructions": (
                            "Use request_knowledge_promotion only after the user asks to "
                            "promote an exact validated Knowledge Forge bundle."
                        ),
                    },
                )
            elif method == "ping":
                self._response(request_id, {})
            elif method == "tools/list":
                self._response(request_id, {"tools": [_tool()]})
            elif method == "tools/call":
                self._call(request_id, request.get("params"))
            elif request_id is not None:
                self._error(request_id, -32601, "method not found")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Codex native Knowledge Forge MCP server")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--operator-socket", default=str(CODEX_SOCKET_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import pwd
        expected_uid = pwd.getpwnam('kf-agent').pw_uid
        if expected_uid == 0 or os.getuid() != expected_uid or os.geteuid() != expected_uid:
            raise CodexMcpServerError('MCP transport must actually run as non-root kf-agent')
        server = CodexApprovalMcpServer(
            workspace_root=args.workspace_root,
            operator_socket=args.operator_socket,
            input_stream=sys.stdin,
            output_stream=sys.stdout,
        )
        server.serve()
    except (OSError, ValueError, KeyError) as exc:
        print(f"knowledge-forge Codex MCP refused: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
