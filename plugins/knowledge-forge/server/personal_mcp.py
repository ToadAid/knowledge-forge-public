"""Read-only Personal Forge MCP bridge for a local Codex plugin.

Only metadata listing and bounded, revalidated lookup. Calls the existing
knowledge-forge CLI; never writes a personal library, signs an approval or
accesses the governed operator.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from typing import Any

MCP_VERSION = "2025-11-25"
MAX_INPUT = 1024 * 1024
MAX_OUTPUT = 512 * 1024
TOOLS = {
    "library_list": {
        "name": "library_list",
        "description": "List the user's local PERSONAL Knowledge Forge skill metadata; do not read source content or PROMOTED registry.",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    "knowledge_lookup": {
        "name": "knowledge_lookup",
        "description": "Revalidate and retrieve up to 3 local PERSONAL knowledge units with real source bindings. Returns untrusted knowledge only; report an honest no-match.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "minLength": 1, "maxLength": 500},
                "max_units": {"type": "integer", "minimum": 1, "maximum": 3},
            },
            "required": ["question"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
}


def _send(stream: Any, value: dict[str, Any]) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    stream.flush()


def _error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message[:500]}],
        "structuredContent": {"ok": False, "error": message[:500]},
        "isError": True,
    }


def _run_cli(argv: list[str]) -> dict[str, Any]:
    binary = shutil.which("knowledge-forge")
    if not binary:
        return _error(
            "Knowledge Forge ordinary CLI is not installed on PATH. "
            "Run the plugin setup skill; no library was accessed."
        )
    try:
        proc = subprocess.run(
            [binary, *argv], capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _error("Knowledge Forge local lookup failed: " + str(exc))
    if len(proc.stdout) > MAX_OUTPUT or len(proc.stderr) > MAX_OUTPUT:
        return _error("Knowledge Forge output exceeded the bounded response size")
    if proc.returncode != 0:
        try:
            failure = json.loads(proc.stderr.decode("utf-8"))
            message = str(failure.get("error", "Knowledge Forge refused"))
        except (UnicodeDecodeError, ValueError, AttributeError):
            message = "Knowledge Forge refused the request (CLI exit " + str(proc.returncode) + ")"
        return _error(message)
    try:
        payload = json.loads(proc.stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _error("Knowledge Forge CLI returned invalid JSON")
    if not isinstance(payload, dict):
        return _error("Knowledge Forge CLI returned unexpected output")
    # Return the exact output, including source bindings and untrusted-data stamps.
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "structuredContent": payload,
        "isError": False,
    }


def call(name: Any, arguments: Any) -> dict[str, Any]:
    if not isinstance(name, str) or name not in TOOLS or not isinstance(arguments, dict):
        return _error("Unknown Knowledge Forge tool or invalid arguments")
    if name == "library_list":
        if arguments:
            return _error("library_list takes no arguments")
        return _run_cli(["personal", "list"])
    if set(arguments) not in ({"question"}, {"question", "max_units"}):
        return _error("knowledge_lookup accepts only question and max_units")
    question = arguments["question"]
    if not isinstance(question, str) or not question.strip() or len(question) > 500:
        return _error("question must be nonempty text of at most 500 characters")
    units = arguments.get("max_units", 1)
    if type(units) is not int or units not in (1, 2, 3):
        return _error("max_units must be 1, 2 or 3")
    return _run_cli(["personal", "lookup", question, "--max-units", str(units)])


def serve(inp: Any = sys.stdin, out: Any = sys.stdout) -> None:
    while True:
        line = inp.readline(MAX_INPUT + 1)
        if not line:
            return
        if len(line.encode("utf-8")) > MAX_INPUT:
            _send(out, {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "MCP request exceeds limit"}})
            return
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _send(out, {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "invalid JSON"}})
            continue
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            _send(out, {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "invalid MCP request"}})
            continue
        if "id" not in request:
            continue
        identity = request["id"]
        method = request.get("method")
        if method == "initialize":
            response = {"protocolVersion": MCP_VERSION,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": "toadaid-knowledge-forge-personal", "version": "0.1.0"},
                        "instructions": "Read-only PERSONAL library; validate and accept packages separately with the local CLI."}
        elif method == "tools/list":
            response = {"tools": list(TOOLS.values())}
        elif method == "ping":
            response = {}
        elif method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or set(params) != {"name", "arguments"}:
                response = _error("tools/call requires exact name and arguments")
            else:
                response = call(params["name"], params["arguments"])
        else:
            _send(out, {"jsonrpc": "2.0", "id": identity,
                        "error": {"code": -32601, "message": "method not found"}})
            continue
        _send(out, {"jsonrpc": "2.0", "id": identity, "result": response})


if __name__ == "__main__":
    serve()
