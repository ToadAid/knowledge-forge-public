"""Agent-neutral submit/status CLI. No approval or completion operation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys

from .protected_operator_client import snapshot_bundle
from .protected_operator_service import (
    INITIAL_REQUEST_SECONDS, ProtectedOperatorError, recv_frame, send_frame,
)

REQUEST_SOCKET = Path('/run/toadaid-knowledge-forge/agent-requests.sock')


def exchange(request, *, socket_path=REQUEST_SOCKET):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(INITIAL_REQUEST_SECONDS)
        connection.connect(str(socket_path))
        send_frame(connection, request)
        result = recv_frame(connection)
    if result.get('status') == 'REFUSED':
        raise ProtectedOperatorError(str(result.get('error', 'request refused')))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='knowledge-forge-request', description=__doc__)
    parser.add_argument('--socket', type=Path, default=REQUEST_SOCKET)
    sub = parser.add_subparsers(dest='command', required=True)
    submit = sub.add_parser('submit', help='submit one VALIDATED bundle; no promotion')
    submit.add_argument('bundle')
    status = sub.add_parser('status', help='read status of a request submitted by this agent UID')
    status.add_argument('request_id')
    args = parser.parse_args(argv)
    try:
        if args.command == 'submit':
            request = {'operation': 'SUBMIT_GENERIC', 'bundle': snapshot_bundle(args.bundle)}
        else:
            request = {'operation': 'STATUS_GENERIC', 'request_id': args.request_id}
        result = exchange(request, socket_path=args.socket)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'REFUSED', 'error': str(exc), 'promotion_performed': None}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
