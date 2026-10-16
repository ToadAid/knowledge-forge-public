"""Human-session popup broker for the protected generic approval queue.

The broker must be root-installed and launched as a distinct human reviewer,
outside agent execution permissions. It holds no signing key or registry.
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import time

from .approve_card import ZenityCard
from .generic_approval_client import exchange
from .protected_operator_service import (
    CHALLENGE_SECONDS, INITIAL_REQUEST_SECONDS, SOCKET_PATH,
    ProtectedOperatorError, recv_frame, send_frame,
)


def review_request(identity, *, card, socket_path=SOCKET_PATH):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(INITIAL_REQUEST_SECONDS)
        connection.connect(str(socket_path))
        send_frame(connection, {'operation': 'REVIEW_GENERIC', 'request_id': identity})
        challenge = recv_frame(connection)
        if challenge.get('status') != 'AWAITING_HUMAN_CONFIRMATION' or challenge.get('request_id') != identity:
            raise ProtectedOperatorError(str(challenge.get('error', 'review challenge refused')))
        required = {'status', 'challenge_id', 'confirmation', 'expires_at', 'principal',
            'skill_id', 'skill_version', 'unit_count', 'claim_count',
            'package_receipt_sha256', 'registry_before_sha256', 'request_id',
            'bundle_sha256', 'source_corpus_coverage', 'coverage_warning'}
        if set(challenge) != required:
            raise ProtectedOperatorError('review challenge fields mismatch')
        connection.settimeout(CHALLENGE_SECONDS + 5)
        card.show('\n'.join([
            'Knowledge Forge — protected promotion request', '',
            f'Request: {identity}',
            f'Skill: {challenge["skill_id"]}',
            f'Version: {challenge["skill_version"]}',
            f'Units: {challenge["unit_count"]}; claims: {challenge["claim_count"]}',
            f'Package receipt: {challenge["package_receipt_sha256"]}',
            f'Immutable bundle: {challenge["bundle_sha256"]}',
            f'Current registry: {challenge["registry_before_sha256"]}',
            f'Source coverage: {challenge["source_corpus_coverage"]}',
            f'Coverage warning: {challenge["coverage_warning"]}',
            f'Authenticated reviewer: {challenge["principal"]}',
            f'Approval expires: {challenge["expires_at"]}', '',
            'Validation checks integrity; source support may be model-judged.',
            'VALIDATED != PROMOTED. Chat text cannot authorize.',
            'The separate operator UID owns registry and approval state.',
            'Close this evidence view to choose Approve & Promote or Reject.',
        ]))
        decision = card.ask_decision('Promote exactly this validated package once?')
        if decision not in ('APPROVE', 'REJECT', None):
            raise ProtectedOperatorError('unsupported desktop decision')
        if decision == 'APPROVE':
            confirmed = card.ask_confirm(
                f'Confirm one promotion?\nSkill: {challenge["skill_id"]}\n'
                f'Package: {challenge["package_receipt_sha256"]}\n'
                f'Request: {identity}')
            if confirmed is not True:
                decision = None
        send_frame(connection, {'operation': 'DECIDE_GENERIC', 'request_id': identity,
            'challenge_id': challenge['challenge_id'], 'decision': decision or 'CANCEL'})
        result = recv_frame(connection)
        if result.get('request_id') != identity or result.get('status') not in ('PROMOTED', 'REJECTED', 'CANCELLED'):
            raise ProtectedOperatorError(str(result.get('error', 'invalid review result; inspect operator status')))
        card.show(json.dumps(result, indent=2, sort_keys=True))
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(prog='knowledge-forge-review-daemon', description=__doc__)
    parser.add_argument('--once', action='store_true', help='review at most one pending request, then exit')
    args = parser.parse_args(argv)
    try:
        card = ZenityCard()
        while True:
            pending = exchange({'operation': 'LIST_GENERIC'}, socket_path=SOCKET_PATH)
            if pending.get('status') != 'PENDING_REQUESTS' or not isinstance(pending.get('requests'), list):
                raise ProtectedOperatorError('invalid pending-list response')
            if pending['requests']:
                result = review_request(pending['requests'][0]['request_id'], card=card)
                print(json.dumps(result, sort_keys=True), flush=True)
            if args.once:
                return 0
            time.sleep(5)
    except (OSError, ValueError) as exc:
        # The connection could fail after the daemon persisted promotion.
        # Report uncertainty; protected request status/receipts settle truth.
        print(json.dumps({'status': 'REFUSED', 'error': str(exc),
                          'promotion_performed': None, 'reconciliation_required': True}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
