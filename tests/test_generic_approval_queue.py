from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import struct
import unittest
from unittest.mock import patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('generic_approval_queue')

from knowledge_forge import generic_approval_queue as queue_module
from knowledge_forge.generic_approval_queue import ApprovalQueue, handle_generic_connection
from knowledge_forge.protected_operator_service import (
    PeerIdentity, ProtectedOperatorError, _json_bytes, _sha, serve_connected_socket,
)
from knowledge_forge.registry import load_skill_registry, save_skill_registry, create_skill_registry
from tests import test_protected_operator_service as fixtures

CLOCK = fixtures.CLOCK


class FakeConnection:
    def __init__(self, uid=2001):
        self.uid = uid

    def getsockopt(self, *_args):
        return struct.pack('3i', 1234, self.uid, self.uid)

    def settimeout(self, value):
        pass


class GenericApprovalQueueTests(unittest.TestCase):
    def setUp(self):
        fixtures.ProtectedOperatorServiceTests.setUp(self)
        self.agent = PeerIdentity(4321, 2001, 2001, 'untrusted-agent-label')
        self.queue = ApprovalQueue(self.state / 'approval-queue.sqlite3')

    def submit(self):
        return self.queue.submit(self.bundle, peer=self.agent, now=CLOCK)

    def handle(self, request, *, agent=False, response=None, now_fn=lambda: CLOCK):
        sent = []
        def reply(_connection):
            return response(sent[-1]) if callable(response) else response
        with patch.object(queue_module, 'send_frame', side_effect=lambda c, v: sent.append(v)), \
             patch.object(queue_module, 'recv_frame', side_effect=reply):
            handle_generic_connection(FakeConnection(), policy=self.policy,
                peer=self.agent if agent else self.peer,
                request=request, agent=agent, now_fn=now_fn)
        return sent

    @staticmethod
    def decision(challenge, decision='APPROVE'):
        return {'operation': 'DECIDE_GENERIC', 'request_id': challenge['request_id'],
                'challenge_id': challenge['challenge_id'], 'decision': decision}

    def assert_not_promoted(self):
        self.assertEqual(load_skill_registry(self.state / 'registry.json')['skills'], {})
        self.assertEqual(list((self.state / 'receipts').iterdir()), [])

    def test_submit_is_durable_non_authorizing_snapshot(self):
        result = self.submit()
        self.assertEqual(result['status'], 'PENDING')
        self.assertFalse(result['promotion_performed'])
        self.assertNotIn('confirmation', result)
        self.assertNotIn('challenge_id', result)
        self.assertNotIn('paths', result)
        self.assert_not_promoted()
        restarted = ApprovalQueue(self.queue.path)
        self.assertEqual(restarted.status(result['request_id'], peer_uid=self.agent.uid, now=CLOCK), result)
        self.assertEqual(self.queue.path.stat().st_mode & 0o777, 0o600)

    def test_duplicate_pending_bundle_refuses(self):
        self.submit()
        with self.assertRaisesRegex(ProtectedOperatorError, 'already queued'):
            self.submit()
        self.assert_not_promoted()

    def test_reject_and_cancel_are_terminal_without_registry_mutation(self):
        for decision, expected in [('REJECT', 'REJECTED'), ('CANCEL', 'CANCELLED')]:
            identity = self.submit()['request_id']
            sent = self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity},
                response=lambda c: self.decision(c, decision))
            self.assertEqual(sent[-1]['status'], expected)
            self.assertEqual(self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)['status'], expected)
            with self.assertRaisesRegex(ProtectedOperatorError, 'not pending'):
                self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity})
        self.assert_not_promoted()

    def test_approved_snapshot_uses_existing_operator_and_receipt(self):
        identity = self.submit()['request_id']
        sent = self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)
        self.assertEqual(sent[-1]['status'], 'PROMOTED')
        self.assertIn(self.manifest['skill_id'], load_skill_registry(self.state / 'registry.json')['skills'])
        status = self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)
        self.assertTrue(status['promotion_performed'])
        self.assertEqual(status['receipt_sha256'], sent[-1]['receipt_sha256'])
        self.assertNotIn('paths', status)
        self.assertTrue(Path(sent[-1]['paths']['receipt']).is_file())
        with self.assertRaisesRegex(ProtectedOperatorError, 'not pending'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)

    def test_source_workspace_mutation_cannot_change_queued_snapshot(self):
        identity = self.submit()['request_id']
        self.bundle['files']['manifest.json'] = 'e30='
        sent = self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)
        self.assertEqual(sent[-1]['skill_id'], self.manifest['skill_id'])
        self.assertEqual(sent[-1]['status'], 'PROMOTED')

    def test_queue_snapshot_digest_tamper_refuses(self):
        identity = self.submit()['request_id']
        with sqlite3.connect(self.queue.path) as db:
            db.execute('UPDATE approval_requests SET bundle=? WHERE request_id=?', ('{}', identity))
        with self.assertRaisesRegex(ProtectedOperatorError, 'digest mismatch'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)
        self.assert_not_promoted()

    def test_expiry_survives_restart(self):
        identity = self.submit()['request_id']
        expired = CLOCK + timedelta(seconds=queue_module.QUEUE_SECONDS)
        restarted = ApprovalQueue(self.queue.path)
        self.assertEqual(restarted.status(identity, peer_uid=self.agent.uid, now=expired)['status'], 'EXPIRED')
        with self.assertRaisesRegex(ProtectedOperatorError, 'not pending'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, now_fn=lambda: expired)
        self.assert_not_promoted()

    def test_status_is_submitter_scoped_and_ids_are_bounded(self):
        identity = self.submit()['request_id']
        for invalid, uid in [(identity, 3001), ('../registry.json', self.agent.uid), ('a'*65, self.agent.uid)]:
            with self.assertRaises(ProtectedOperatorError):
                self.queue.status(invalid, peer_uid=uid, now=CLOCK)

    def test_agent_cannot_list_review_or_decide(self):
        identity = self.submit()['request_id']
        for op in ('LIST_GENERIC', 'REVIEW_GENERIC', 'DECIDE_GENERIC', 'PROMOTE', 'APPROVE', 'COMPLETE_CODEX'):
            with self.assertRaisesRegex(ProtectedOperatorError, 'peer role'):
                self.handle({'operation': op, 'request_id': identity}, agent=True)
        self.assert_not_promoted()

    def test_human_queue_channel_cannot_submit_or_read_agent_status(self):
        for op in ('SUBMIT_GENERIC', 'STATUS_GENERIC'):
            with self.assertRaisesRegex(ProtectedOperatorError, 'peer role'):
                self.handle({'operation': op})

    def test_extra_fields_and_chat_authorization_are_refused(self):
        with self.assertRaisesRegex(ProtectedOperatorError, 'fields mismatch'):
            self.handle({'operation': 'SUBMIT_GENERIC', 'bundle': self.bundle, 'approved': True}, agent=True)
        identity = self.submit()['request_id']
        for decision in ('APPROVE always', 'approve-once', 'yes'):
            # A new rejected/cancelled request can be resubmitted, never an
            # active or possibly consumed candidate.
            if decision != 'APPROVE always':
                identity = self.submit()['request_id']
            with self.assertRaisesRegex(ProtectedOperatorError, 'binding mismatch'):
                self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity},
                    response=lambda c: self.decision(c, decision))
        self.assert_not_promoted()

    def test_wrong_request_or_challenge_refuses_and_cancels(self):
        for field in ('request_id', 'challenge_id'):
            identity = self.submit()['request_id']
            def response(c):
                result = self.decision(c)
                result[field] = '0'*64
                return result
            with self.assertRaisesRegex(ProtectedOperatorError, 'binding mismatch'):
                self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=response)
            self.assertEqual(self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)['status'], 'CANCELLED')
        self.assert_not_promoted()

    def test_disconnect_cancels_without_promotion(self):
        identity = self.submit()['request_id']
        with patch.object(queue_module, 'send_frame'), \
             patch.object(queue_module, 'recv_frame', side_effect=ProtectedOperatorError('disconnected')):
            with self.assertRaisesRegex(ProtectedOperatorError, 'disconnected'):
                handle_generic_connection(FakeConnection(), policy=self.policy, peer=self.peer,
                    request={'operation': 'REVIEW_GENERIC', 'request_id': identity}, agent=False, now_fn=lambda: CLOCK)
        self.assertEqual(self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)['status'], 'CANCELLED')
        self.assert_not_promoted()

    def test_challenge_expiry_never_promotes(self):
        identity = self.submit()['request_id']
        clock = [CLOCK]
        def response(challenge):
            clock[0] = CLOCK + timedelta(seconds=301)
            return self.decision(challenge)
        with self.assertRaisesRegex(ProtectedOperatorError, 'expired'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=response, now_fn=lambda: clock[0])
        self.assert_not_promoted()

    def test_changed_registry_and_persistence_failure_require_reconciliation(self):
        identity = self.submit()['request_id']
        def response(c):
            save_skill_registry(create_skill_registry(created_at='2026-10-16T04:01:00Z'), self.state / 'registry.json')
            return self.decision(c)
        with self.assertRaisesRegex(ProtectedOperatorError, 'registry changed'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=response)
        status = self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)
        self.assertEqual(status['status'], 'RECONCILIATION_REQUIRED')
        self.assertTrue(status['reconciliation_required'])
        with self.assertRaisesRegex(ProtectedOperatorError, 'already queued'):
            self.submit()
        self.assert_not_promoted()

    def test_crash_after_promotion_before_queue_receipt_is_not_reported_as_success(self):
        identity = self.submit()['request_id']
        real_transition = ApprovalQueue.transition
        def transition(queue, *args, **kwargs):
            if kwargs['state'] == 'PROMOTED':
                raise ProtectedOperatorError('simulated final write failure')
            return real_transition(queue, *args, **kwargs)
        with patch.object(ApprovalQueue, 'transition', transition):
            with self.assertRaisesRegex(ProtectedOperatorError, 'simulated'):
                self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)
        self.assertIn(self.manifest['skill_id'], load_skill_registry(self.state / 'registry.json')['skills'])
        status = self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)
        self.assertFalse(status['promotion_performed'])
        self.assertTrue(status['reconciliation_required'])
        with self.assertRaisesRegex(ProtectedOperatorError, 'not pending'):
            self.handle({'operation': 'REVIEW_GENERIC', 'request_id': identity}, response=self.decision)

    def test_queue_caps_refuse_without_overwriting(self):
        identity = self.submit()['request_id']
        for name, limit in [('MAX_QUEUE_ENTRIES', 1), ('MAX_PENDING', 1), ('MAX_STORED_BYTES', 1), ('MAX_BUNDLE_BYTES', 1)]:
            with patch.object(queue_module, name, limit):
                with self.assertRaisesRegex(ProtectedOperatorError, 'limit|capacity'):
                    self.submit()
        self.assertEqual(self.queue.status(identity, peer_uid=self.agent.uid, now=CLOCK)['status'], 'PENDING')
        self.assert_not_promoted()

    def test_invalid_lifecycle_and_package_refuse_before_enqueue(self):
        self.bundle['lifecycle'] = 'e30='
        with self.assertRaises(ValueError):
            self.submit()
        self.assertEqual(self.queue.pending(now=CLOCK), [])
        self.assert_not_promoted()

    def test_same_request_cannot_be_claimed_by_two_reviewers(self):
        identity = self.submit()['request_id']
        self.queue.claim(identity, reviewer=self.peer, now=CLOCK)
        other = PeerIdentity(999, 3000, 3000, 'other-human')
        with self.assertRaisesRegex(ProtectedOperatorError, 'not pending'):
            self.queue.claim(identity, reviewer=other, now=CLOCK)
        with self.assertRaisesRegex(ProtectedOperatorError, 'another reviewer'):
            self.queue.transition(identity, reviewer=other, expected='REVIEWING', state='REJECTED', now=CLOCK)

    def test_runtime_checks_kernel_uid_before_dispatch(self):
        from knowledge_forge import protected_operator_service as service
        for uid in (0, 2002, 1000):
            with patch.object(service, '_load_policy', return_value=self.policy), \
                 patch.object(service, 'verify_runtime', return_value=(2002, 2001)), \
                 patch.object(service, 'recv_frame', return_value={'operation': 'SUBMIT_GENERIC', 'bundle': self.bundle}), \
                 patch.object(service, 'send_frame'), \
                 patch.object(queue_module, 'handle_generic_connection') as dispatch:
                with self.assertRaises(ProtectedOperatorError):
                    serve_connected_socket(FakeConnection(uid))
                dispatch.assert_not_called()
        for op in ('LIST_GENERIC', 'REVIEW_GENERIC', 'DECIDE_GENERIC', 'PROMOTE'):
            with patch.object(service, '_load_policy', return_value=self.policy), \
                 patch.object(service, 'verify_runtime', return_value=(2002, 2001)), \
                 patch.object(service, 'recv_frame', return_value={'operation': op}), \
                 patch.object(service, 'send_frame'):
                with self.assertRaisesRegex(ProtectedOperatorError, 'human account'):
                    serve_connected_socket(FakeConnection(2001))

    def test_authorized_agent_dispatch_is_submit_only(self):
        from knowledge_forge import protected_operator_service as service
        with patch.object(service, '_load_policy', return_value=self.policy), \
             patch.object(service, 'verify_runtime', return_value=(2002, 2001)), \
             patch.object(service, 'recv_frame', return_value={'operation': 'STATUS_GENERIC', 'request_id': 'a'*64}), \
             patch.object(service.pwd, 'getpwuid'), \
             patch.object(queue_module, 'handle_generic_connection') as dispatch:
            serve_connected_socket(FakeConnection(2001))
            self.assertTrue(dispatch.call_args.kwargs['agent'])

    def test_deployment_keeps_desktop_and_operator_separate(self):
        root = Path(__file__).resolve().parents[1]
        socket = (root / 'deploy/linux-protected-operator/knowledge-forge-agent-requests.socket').read_text()
        self.assertIn('SocketGroup=kf-agent', socket)
        self.assertIn('SocketMode=0660', socket)
        reviewer = (root / 'bin/knowledge-forge-review-daemon').read_text()
        self.assertIn('/usr/bin/python3 -I -B', reviewer)
        self.assertIn('/opt/toadaid-kf-operator/src', reviewer)
        self.assertNotIn('Path(__file__)', reviewer)


if __name__ == '__main__':
    unittest.main()
