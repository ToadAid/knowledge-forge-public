"""P9A: a pre-merge blocker matrix may never become native-test permission."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from knowledge_forge.portable_native_readiness_reconciliation import (
    ALLOWED_UNVERIFIED, FAMILIES, HOSTS, ReadinessReconciliationError,
    _claims, main, reconcile,
)
from knowledge_forge.portable_native_sandbox_contract import PROOF_GATES, contract
from knowledge_forge.portable_native_hook_trust_review import review


class DellP9AReadinessTests(unittest.TestCase):
    def test_two_hosts_eight_families_and_exact_nine_canonical_gates(self):
        for host in HOSTS:
            result = reconcile(host)
            self.assertEqual(len(result['blocker_families']), 8)
            self.assertEqual(result['blocking_unverified_count'], 8)
            self.assertEqual([x['gate'] for x in result['native_proof_gates']], list(PROOF_GATES))
            self.assertEqual(result['native_proof_gates_unverified'], 9)
            self.assertTrue(all(x['status'] == 'UNVERIFIED_NATIVE_EVIDENCE_REQUIRED'
                                for x in result['native_proof_gates']))

    def test_every_canonical_gate_has_at_least_one_blocker(self):
        self.assertEqual(set(PROOF_GATES),
                         {g for _, gates, _, _ in FAMILIES for g in gates})

    def test_evidence_routes_are_explicitly_not_attestations(self):
        for b in reconcile('claude-code')['blocker_families']:
            self.assertEqual(b['status'], 'BLOCKING_UNVERIFIED')
            self.assertTrue(b['required_future_evidence'])
            self.assertEqual(b['user_claim_unverified_only'], 'NOT_OBSERVED')
            self.assertNotEqual(b['evidence_route_not_attestation'], 'PASS')

    def test_canonical_parent_and_hook_proposal_consistent(self):
        for host in HOSTS:
            r = reconcile(host)
            self.assertEqual(r['parent_contract_sha256'], contract(host)['contract_sha256'])
            self.assertEqual(r['hook_taxonomy_fingerprint_not_attestation'],
                             review(host)['source_state_fingerprint_not_host_attestation'])

    def test_deterministic_non_authorizing_fingerprint(self):
        for host in HOSTS:
            self.assertEqual(reconcile(host), reconcile(host))
            self.assertEqual(len(reconcile(host)['review_fingerprint_not_signature_or_permission']), 64)
        self.assertNotEqual(reconcile('claude-code')['review_fingerprint_not_signature_or_permission'],
                            reconcile('codex-cli')['review_fingerprint_not_signature_or_permission'])

    def test_all_execution_and_authority_flags_false(self):
        for host in HOSTS:
            r = reconcile(host)
            for key in ('native_host_started','hook_dispatched','host_configurations_read',
                        'user_secrets_read','model_called','tools_executed',
                        'network_requested','native_launch_authorized',
                        'human_authorization_received','authorization_token_issued',
                        'approval_journal_implemented','promotion_authorized',
                        'safe_to_activate_hooks'):
                self.assertIs(r[key], False, (host, key))
            self.assertEqual(r['tool_grants'], [])

    def test_p9_pr36_not_imported_or_assumed_merged(self):
        for host in HOSTS:
            r = reconcile(host)
            self.assertIn('PR36_NOT_IMPORTED', r['p9_dependency_at_authoring_base'])
            self.assertIn('18_THEN_36', r['next_action'])

    def test_unverified_input_does_not_earn_credit(self):
        for host in HOSTS:
            given = {n: 'CLAIMED_UNVERIFIED' for n, *_ in FAMILIES}
            r = reconcile(host, unverified_claims=given)
            self.assertTrue(all(b['status'] == 'BLOCKING_UNVERIFIED'
                                for b in r['blocker_families']))
            self.assertFalse(r['native_launch_authorized'])

    def test_refuses_verified_signed_approved_or_permission_claim(self):
        for bad in ('PASS','VERIFIED','SIGNED','APPROVED','TRUSTED','ABSENT',True,None,[],1):
            d = {n: 'NOT_OBSERVED' for n, *_ in FAMILIES}
            d[next(iter(d))] = bad
            with self.subTest(bad=bad), self.assertRaises(ReadinessReconciliationError):
                reconcile('codex-cli', unverified_claims=d)

    def test_refuses_missing_or_extra_family(self):
        d = {n: 'NOT_OBSERVED' for n, *_ in FAMILIES}
        d.pop(next(iter(d)))
        with self.assertRaises(ReadinessReconciliationError):
            reconcile('claude-code', unverified_claims=d)
        d = {n: 'NOT_OBSERVED' for n, *_ in FAMILIES}
        d['extra_approved'] = 'NOT_OBSERVED'
        with self.assertRaises(ReadinessReconciliationError):
            reconcile('claude-code', unverified_claims=d)

    def test_refuses_wrong_claim_container(self):
        for value in ([], True, 'NOT_OBSERVED', tuple(), 1):
            with self.subTest(value=value), self.assertRaises(ReadinessReconciliationError):
                reconcile('claude-code', unverified_claims=value)

    def test_refuses_invalid_hosts(self):
        for host in ('', None, True, 1, {}, 'codex --dangerously-bypass', 'Claude'):
            with self.subTest(host=host), self.assertRaises(ReadinessReconciliationError):
                reconcile(host)

    def test_not_directly_reading_files_network_or_spawning(self):
        with patch('builtins.open', side_effect=AssertionError('no file')), \
             patch('subprocess.run', side_effect=AssertionError('no subprocess')):
            self.assertFalse(reconcile('codex-cli')['native_launch_authorized'])

    def test_cli_review_only_returns_json_not_authority(self):
        with patch('subprocess.run', side_effect=AssertionError('spawn forbidden')), \
             patch('builtins.print') as sink:
            self.assertEqual(main(['--host', 'claude-code', '--mode', 'REVIEW_ONLY']), 0)
        r = json.loads(sink.call_args.args[0])
        self.assertEqual(r['blocking_unverified_count'], 8)
        self.assertFalse(r['native_launch_authorized'])

    def test_cli_native_modes_refused_with_empty_stdout(self):
        for host, mode in (('codex-cli', 'NATIVE_TEST'),('claude-code','ON'),
                           ('invalid','REVIEW_ONLY'),('codex-cli','APPROVED')):
            cp = subprocess.run([sys.executable, '-m',
                                 'knowledge_forge.portable_native_readiness_reconciliation',
                                 '--host',host,'--mode',mode],
                                capture_output=True, timeout=8)
            self.assertEqual(cp.returncode, 2)
            self.assertEqual(cp.stdout, b'')

    def test_no_credential_identity_or_host_path_in_report(self):
        for host in HOSTS:
            raw = json.dumps(reconcile(host)).lower()
            for forbidden in ('/home/tommy', 'private_key', 'bearer ',
                              'access_token', 'api_key', 'authorization_nonce',
                              'approved": true'):
                self.assertNotIn(forbidden, raw)

    def test_caller_cannot_mutate_static_blocker_matrix(self):
        before = copy.deepcopy(FAMILIES)
        result = reconcile('claude-code')
        result['blocker_families'][0]['family'] = 'MUTATED'
        self.assertEqual(FAMILIES, before)


if __name__ == '__main__':
    unittest.main()
