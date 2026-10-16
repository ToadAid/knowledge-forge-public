"""Unprivileged human client for the Linux protected promotion service."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import socket
import sys
from typing import Any, Sequence

from .protected_operator_service import (
    CHALLENGE_SECONDS,
    INITIAL_REQUEST_SECONDS,
    MAX_LIFECYCLE_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILES,
    ProtectedOperatorError,
    SOCKET_PATH,
    recv_frame,
    send_frame,
)


def _read_confirmation(input_stream: Any) -> str:
    try:
        confirmation = input_stream.readline()
    except (EOFError, KeyboardInterrupt) as exc:
        raise ProtectedOperatorError("promotion cancelled") from exc
    if not confirmation:
        raise ProtectedOperatorError("promotion cancelled")
    return confirmation


def snapshot_bundle(root_value: str) -> dict[str, Any]:
    root = Path(root_value)
    package = root / "package"
    lifecycle = root / "lifecycle.validated.json"
    if root.is_symlink() or not root.is_dir():
        raise ProtectedOperatorError("bundle must be an existing real directory")
    if package.is_symlink() or not package.is_dir():
        raise ProtectedOperatorError("bundle must contain a real package directory")
    if lifecycle.is_symlink() or not lifecycle.is_file():
        raise ProtectedOperatorError("bundle must contain lifecycle.validated.json")
    files: dict[str, str] = {}
    total = 0
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ProtectedOperatorError("bundle package cannot contain symlinks")
        if not path.is_file():
            continue
        payload = path.read_bytes()
        relative = path.relative_to(package).as_posix()
        files[relative] = base64.b64encode(payload).decode("ascii")
        total += len(payload)
        if len(files) > MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
            raise ProtectedOperatorError("bundle exceeds package limits")
    if not files:
        raise ProtectedOperatorError("bundle package is empty")
    lifecycle_payload = lifecycle.read_bytes()
    if len(lifecycle_payload) > MAX_LIFECYCLE_BYTES:
        raise ProtectedOperatorError("bundle lifecycle exceeds size limit")
    return {
        "schema_version": "0.1",
        "files": files,
        "lifecycle": base64.b64encode(lifecycle_payload).decode("ascii"),
    }


def promote(bundle_root: str, *, socket_path: Path = SOCKET_PATH,
            input_stream: Any = sys.stdin, output_stream: Any = sys.stdout) -> dict[str, Any]:
    if not input_stream.isatty() or not output_stream.isatty():
        raise ProtectedOperatorError("promotion requires an interactive human TTY")
    bundle = snapshot_bundle(bundle_root)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.settimeout(INITIAL_REQUEST_SECONDS)
        connection.connect(str(socket_path))
        send_frame(connection, {"operation": "PROMOTE", "bundle": bundle})
        challenge = recv_frame(connection)
        if challenge.get("status") == "REFUSED":
            raise ProtectedOperatorError(str(challenge.get("error", "promotion refused")))
        required = {
            "status", "challenge_id", "confirmation", "expires_at", "principal",
            "skill_id", "skill_version", "unit_count", "claim_count",
            "package_receipt_sha256", "registry_before_sha256",
        }
        if set(challenge) != required or challenge.get("status") != "AWAITING_HUMAN_CONFIRMATION":
            raise ProtectedOperatorError("protected service returned an invalid challenge")
        connection.settimeout(CHALLENGE_SECONDS + 5)
        output_stream.write(
            "KNOWLEDGE FORGE — PROTECTED PROMOTION\n"
            f"Authenticated local user: {challenge['principal']}\n"
            f"Skill: {challenge['skill_id']}\n"
            f"Version: {challenge['skill_version']}\n"
            f"Units: {challenge['unit_count']} | Claims: {challenge['claim_count']}\n"
            f"Package receipt: {challenge['package_receipt_sha256']}\n"
            f"Current registry: {challenge['registry_before_sha256']}\n"
            f"Expires: {challenge['expires_at']}\n"
            "Promotion changes governed registry truth.\n"
            f"Type exactly: {challenge['confirmation']}\n> "
        )
        output_stream.flush()
        confirmation = _read_confirmation(input_stream)
        while True:
            send_frame(connection, {
                "challenge_id": challenge["challenge_id"],
                "confirmation": confirmation.strip(),
            })
            result = recv_frame(connection)
            if result.get("status") != "MISMATCH":
                break
            output_stream.write(
                f"Mismatch; {result.get('remaining')} attempt(s) remain.\n"
                f"Type exactly: {challenge['confirmation']}\n> "
            )
            output_stream.flush()
            confirmation = _read_confirmation(input_stream)
        if result.get("status") != "PROMOTED":
            raise ProtectedOperatorError(str(result.get("error", "promotion refused")))
        promoted_fields = {
            "status", "skill_id", "skill_version", "principal",
            "receipt_sha256", "paths",
        }
        if set(result) != promoted_fields:
            raise ProtectedOperatorError("protected service returned an invalid promotion receipt")
        output_stream.write(
            f"PROMOTED: {result['skill_id']} version {result['skill_version']}\n"
            f"Receipt: {result['receipt_sha256']}\n"
        )
        output_stream.flush()
        return result
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-forge-promote",
        description="Human client for a separately installed Linux protected operator.",
    )
    parser.add_argument("bundle", help="directory containing package/ and lifecycle.validated.json")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        promote(args.bundle)
        return 0
    except (ProtectedOperatorError, OSError, ValueError) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc), "promoted": False}, sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
