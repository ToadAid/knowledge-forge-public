"""Native-only socket handler, isolated from terminal/UI authority routes."""
from dataclasses import replace
from pathlib import Path
import socket
import sys
from . import protected_operator_service as core

NATIVE_CODE_ROOT = Path('/opt/toadaid-kf-agent')
OPERATOR_CODE_ROOT = Path('/opt/toadaid-kf-operator')
STATE_ROOT = Path('/var/lib/toadaid-kf-operator')


def serve(connection):
    try:
        if connection.getsockname() != str(core.CODEX_SOCKET_PATH):
            raise core.ProtectedOperatorError('Native handler requires the exact Codex socket; no fallback')
        base = core._load_policy()
        if (base.code_root != OPERATOR_CODE_ROOT or base.state_root != STATE_ROOT or
                base.agent_user != 'kf-agent' or base.operator_user != 'kf-operator'):
            raise core.ProtectedOperatorError('Unknown existing protected configuration')
        # Config and state are unchanged. This additive lane uses its own pinned
        # root-owned code snapshot, validated by the same strict runtime checks.
        policy = replace(base, code_root=NATIVE_CODE_ROOT)
        operator_uid, agent_uid = core.verify_runtime(policy)
        peer = core.agent_peer_identity(connection, operator_uid=operator_uid, agent_uid=agent_uid)
        connection.settimeout(core.INITIAL_REQUEST_SECONDS)
        request = core.recv_frame(connection)
        if request.get('operation') != 'PREPARE_CODEX':
            raise core.ProtectedOperatorError('Proof-only native socket refuses typed/TUI/generic operations')
        core.handle_connection(connection, policy=policy, peer=peer, initial_request=request)
    except Exception as exc:
        try:
            core.send_frame(connection, {'status': 'REFUSED', 'error': str(exc)[:500]})
        except OSError:
            pass
        raise


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args:
        print('Native operator accepts only a systemd socket connection', file=sys.stderr)
        return 2
    try:
        serve(socket.socket(fileno=0))
    except (OSError, ValueError) as exc:
        print('Native operator refused: ' + str(exc)[:500], file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
