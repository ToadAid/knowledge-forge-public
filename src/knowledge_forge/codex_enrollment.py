"""One-time Codex native credential enrollment for a protected host."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any

from .codex_p256 import _public_key
from .codex_credential_registry import (
    build_credential_registry,
    save_credential_registry,
)


class CodexEnrollmentError(ValueError):
    """The Codex app-server did not produce enrollable public metadata."""


def provider_readiness(result):
    if not isinstance(result, dict) or set(result) - {'credentialId', 'unavailableReason', 'unavailableMessage'}:
        raise CodexEnrollmentError('Unsupported userVerification/status response contract')
    reason = result.get('unavailableReason')
    if reason not in (None, 'credentialMissing', 'biometricsUnavailable', 'providerUnavailable'):
        raise CodexEnrollmentError('Unknown native provider status')
    return {'available': reason is None and isinstance(result.get('credentialId'), str) and bool(result['credentialId']),
            'unavailable_reason': reason or ('credentialMissing' if not result.get('credentialId') else None),
            'protocol_supported': True, 'promotion_performed': False}


def enrollment_output(value):
    path = Path(value).absolute()
    if '..' in path.parts or any(str(path).startswith(prefix) for prefix in ('/opt/', '/etc/', '/var/lib/', '/run/', '/root/')):
        raise CodexEnrollmentError('Enrollment output must be unprivileged public metadata, never protected host state')
    for node in (path, *path.parents):
        if node.is_symlink():
            raise CodexEnrollmentError('Enrollment output may not contain symlinks')
    if path.exists():
        raise CodexEnrollmentError('Enrollment output already exists; refusing to overwrite')
    return path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CodexAppServerClient:
    def __init__(self, command: str = "codex", timeout_seconds: int = 60) -> None:
        self.timeout_seconds = timeout_seconds
        self.process = subprocess.Popen(  # noqa: S603 - explicit administrator choice
            [command, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.messages: queue.Queue[str | None] = queue.Queue()
        self.diagnostics: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self._send(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {
                        "name": "knowledge_forge",
                        "title": "Knowledge Forge",
                        "version": "0.1.0",
                    },
                    "capabilities": {"experimentalApi": True},
                },
            }
        )
        try:
            self._response(1)
            self._send({"jsonrpc": "2.0", "method": "initialized"})
        except Exception:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        self.messages.put(None)

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        total = 0
        for line in self.process.stderr:
            if total < 4096:
                bounded = line[: 4096 - total]
                self.diagnostics.append(bounded)
                total += len(bounded)

    def _send(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise CodexEnrollmentError("Codex app-server stdin is unavailable")
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def _response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise queue.Empty
                line = self.messages.get(timeout=remaining)
            except queue.Empty as exc:
                raise CodexEnrollmentError("Codex user verification timed out") from exc
            if line is None:
                raise CodexEnrollmentError('Codex app-server closed before responding')
            if len(line.encode('utf-8')) > 1024 * 1024:
                raise CodexEnrollmentError('App-server response exceeded one MiB')
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CodexEnrollmentError("Codex app-server returned invalid JSON") from exc
            if not isinstance(message, dict) or message.get("id") != request_id:
                continue
            if "error" in message:
                error = message["error"]
                if isinstance(error, dict):
                    data = error.get('data', {})
                    reason = data.get('reason') if isinstance(data, dict) else None
                    detail = 'native verification refused: ' + (reason if reason in {
                        'providerUnavailable', 'credentialMissing', 'biometricsUnavailable',
                        'userCancelled', 'interrupted', 'timeout', 'providerError',
                        'serviceError', 'authenticationFailed', 'invalidParams'} else 'protocol error')
                else:
                    detail = "native verification refused"
                raise CodexEnrollmentError(detail[:500])
            result = message.get("result")
            if not isinstance(result, dict):
                raise CodexEnrollmentError("Codex app-server response has no result")
            return result

    def request(self, method: str, request_id: int) -> dict[str, Any]:
        self._send(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": {}}
        )
        return self._response(request_id)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


def enroll(
    registry_path: str | Path,
    *,
    principal: str,
    codex_command: str = "codex",
    enrolled_at: str | None = None,
) -> dict[str, Any]:
    registry_path = enrollment_output(registry_path)
    client = CodexAppServerClient(codex_command)
    try:
        result = client.request("userVerification/enroll", 2)
    finally:
        client.close()
    if set(result) - {"credentialId", "publicKey", "algorithm"}:
        raise CodexEnrollmentError("Codex enrollment returned unexpected fields")
    credential_id = result.get("credentialId")
    public_key = result.get("publicKey")
    algorithm = result.get("algorithm")
    if not isinstance(credential_id, str) or not credential_id:
        raise CodexEnrollmentError("Codex enrollment returned no credential ID")
    if not isinstance(public_key, str) or not public_key:
        raise CodexEnrollmentError(
            "Codex app-server did not return the public key required for verification"
        )
    if algorithm != "ES256":
        raise CodexEnrollmentError("Codex enrollment algorithm is not ES256")
    # Validate public SPKI/curve encoding before producing administrator metadata.
    _public_key(public_key)
    registry = build_credential_registry(
        credential_id=credential_id,
        public_key=public_key,
        principal=principal,
        enrolled_at=enrolled_at or _now(),
    )
    save_credential_registry(registry, registry_path)
    return {
        "enrolled": True,
        "credential_id": credential_id,
        "principal": principal,
        "credential_registry": str(registry_path),
        "promotion_performed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enroll the local Codex reviewer credential for Knowledge Forge"
    )
    parser.add_argument('--status-only', action='store_true', help='Read native provider status only; no enrollment or approval')
    parser.add_argument("--credential-registry")
    parser.add_argument("--principal")
    parser.add_argument("--codex-command", default="codex")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.status_only:
            if args.credential_registry or args.principal:
                raise CodexEnrollmentError('Status-only cannot write enrollment metadata')
            client = CodexAppServerClient(args.codex_command)
            try:
                result = provider_readiness(client.request('userVerification/status', 2))
            finally:
                client.close()
            print(json.dumps(result, sort_keys=True))
            return 0 if result['available'] else 2
        if not args.credential_registry or not args.principal:
            raise CodexEnrollmentError('Enrollment requires --credential-registry and --principal')
        result = enroll(
            args.credential_registry,
            principal=args.principal,
            codex_command=args.codex_command,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"enrolled": False, "error": str(exc)}))
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
