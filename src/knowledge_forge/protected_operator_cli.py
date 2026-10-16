"""Entrypoint for the root-installed Linux protected operator service."""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
from typing import Sequence

from .protected_operator_service import (
    ProtectedOperatorError,
    _load_policy,
    initialize_state,
    serve_connected_socket,
    verify_runtime,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-forge-protected-operator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("initialize-state", help="initialize fixed protected state; administrator ceremony only")
    sub.add_parser("serve-connection", help="serve one systemd socket-activated peer on stdin")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        os.umask(0o077)
        if args.command == "initialize-state":
            policy = _load_policy()
            verify_runtime(policy)
            value = initialize_state(policy)
            sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
            return 0
        connection = socket.socket(fileno=0)
        serve_connected_socket(connection)
        return 0
    except (ProtectedOperatorError, OSError, ValueError) as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc), "promoted": False}, sort_keys=True) + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
