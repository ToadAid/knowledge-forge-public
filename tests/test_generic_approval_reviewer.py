from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('generic_approval_reviewer')

from knowledge_forge import generic_approval_reviewer as reviewer
from knowledge_forge import generic_approval_client as client
from knowledge_forge.approve_card import ScriptedCard
from knowledge_forge.protected_operator_service import ProtectedOperatorError


class GenericApprovalReviewerTests(unittest.TestCase):
    def setUp(self):
        self.identity = 'a'*64
        self.challenge = {
            'status': 'AWAITING_HUMAN_CONFIRMATION', 'request_id': self.identity,
            'challenge_id': 'b'*64, 'confirmation': 'APPROVE abcdef123456',
            'expires_at': '2026-10-16T13:05:00Z', 'principal': 'human-reviewer',
            'skill_id': 'knowledge-forge:synthetic', 'skill_version': 1,
            'unit_count': 1, 'claim_count': 1, 'package_receipt_sha256': 'sha256:'+'c'*64,
            'registry_before_sha256': 'sha256:'+'d'*64, 'bundle_sha256': 'sha256:'+'e'*64,
            'source_corpus_coverage': 'UNKNOWN', 'coverage_warning': 'Do not assume full source coverage.',
        }

    def run_review(self, decision='APPROVE', confirmed=True, result=None, challenge=None):
        card = ScriptedCard([decision], [confirmed])
        result = result or {'status': 'PROMOTED', 'request_id': self.identity}
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with patch.object(reviewer.socket, 'socket', return_value=connection), \
             patch.object(reviewer, 'send_frame') as send, \
             patch.object(reviewer, 'recv_frame', side_effect=[challenge or self.challenge, result]):
            value = reviewer.review_request(self.identity, card=card)
        return value, send.call_args_list, card

    def test_popup_and_confirmation_send_one_bound_decision(self):
        result, sent, card = self.run_review()
        self.assertEqual(result['status'], 'PROMOTED')
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0].args[1], {'operation': 'REVIEW_GENERIC', 'request_id': self.identity})
        self.assertEqual(sent[1].args[1], {'operation': 'DECIDE_GENERIC', 'request_id': self.identity,
            'challenge_id': self.challenge['challenge_id'], 'decision': 'APPROVE'})
        shown = card.shown[0]
        for value in (self.identity, self.challenge['package_receipt_sha256'],
                      self.challenge['registry_before_sha256'], 'UNKNOWN', 'model-judged'):
            self.assertIn(value, shown)

    def test_reject_does_not_require_confirmation(self):
        result, sent, _ = self.run_review(decision='REJECT', result={'status': 'REJECTED', 'request_id': self.identity})
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(sent[-1].args[1]['decision'], 'REJECT')

    def test_close_and_unconfirmed_approval_cancel(self):
        for decision, confirmed in [(None, True), ('APPROVE', False), ('APPROVE', None)]:
            _, sent, _ = self.run_review(decision=decision, confirmed=confirmed,
                result={'status': 'CANCELLED', 'request_id': self.identity})
            self.assertEqual(sent[-1].args[1]['decision'], 'CANCEL')

    def test_invalid_challenge_is_refused_before_ui(self):
        for changes in ({'request_id': '0'*64}, {'status': 'REFUSED'}, {'extra': True}):
            with self.assertRaises(ProtectedOperatorError):
                self.run_review(challenge={**self.challenge, **changes})

    def test_wrong_final_request_or_uncertain_result_is_never_success(self):
        for result in ({'status': 'PROMOTED', 'request_id': '0'*64},
                       {'status': 'REFUSED', 'error': 'registry changed'},
                       {'status': 'RECONCILIATION_REQUIRED', 'request_id': self.identity}):
            with self.assertRaises(ProtectedOperatorError):
                self.run_review(result=result)

    def test_display_failure_cannot_send_decision(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        card = MagicMock()
        card.show.side_effect = ValueError('no graphical display')
        with patch.object(reviewer.socket, 'socket', return_value=connection), \
             patch.object(reviewer, 'send_frame') as send, \
             patch.object(reviewer, 'recv_frame', return_value=self.challenge):
            with self.assertRaisesRegex(ValueError, 'graphical display'):
                reviewer.review_request(self.identity, card=card)
            self.assertEqual(send.call_count, 1)
            card.ask_decision.assert_not_called()

    def test_reviewer_has_no_signing_or_in_process_promotion(self):
        code = Path(reviewer.__file__).read_text()
        for forbidden in ('build_host_decision', 'read_host_key_file',
                          'complete_generic_host_approval', 'OperatorAuthorization',
                          'persist_operator_promotion', 'subprocess', 'eval('):
            self.assertNotIn(forbidden, code)

    def test_broker_once_queries_only_human_socket(self):
        with patch.object(reviewer, 'ZenityCard'), \
             patch.object(reviewer, 'exchange', return_value={'status': 'PENDING_REQUESTS', 'requests': []}) as exchange:
            self.assertEqual(reviewer.main(['--once']), 0)
            exchange.assert_called_once_with({'operation': 'LIST_GENERIC'}, socket_path=reviewer.SOCKET_PATH)

    def test_broker_display_unavailable_fails_without_list_or_decision(self):
        with patch.object(reviewer, 'ZenityCard', side_effect=ValueError('display unavailable')), \
             patch.object(reviewer, 'exchange') as exchange, redirect_stderr(StringIO()):
            self.assertEqual(reviewer.main(['--once']), 2)
            exchange.assert_not_called()

    def test_agent_cli_has_no_decision_command_and_status_sends_only_identity(self):
        output = StringIO()
        with patch.object(client, 'exchange', return_value={'status': 'PENDING'}) as exchange, redirect_stdout(output):
            self.assertEqual(client.main(['status', self.identity]), 0)
            exchange.assert_called_once_with({'operation': 'STATUS_GENERIC', 'request_id': self.identity}, socket_path=client.REQUEST_SOCKET)
        self.assertEqual(json.loads(output.getvalue())['status'], 'PENDING')
        for operation in ('approve', 'reject', 'review', 'promote'):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                client.main([operation, self.identity])

    def test_agent_submission_snapshots_once_and_reports_pending(self):
        with patch.object(client, 'snapshot_bundle', return_value={'synthetic': 'bundle'}) as snapshot, \
             patch.object(client, 'exchange', return_value={'status': 'PENDING', 'promotion_performed': False}) as exchange, \
             redirect_stdout(StringIO()):
            self.assertEqual(client.main(['submit', '/synthetic/bundle']), 0)
            snapshot.assert_called_once_with('/synthetic/bundle')
            self.assertEqual(exchange.call_args.args[0]['operation'], 'SUBMIT_GENERIC')


if __name__ == '__main__':
    unittest.main()
