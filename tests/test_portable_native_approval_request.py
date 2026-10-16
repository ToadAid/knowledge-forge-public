"""P9: a review request cannot become authenticated or launch-capable."""
from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

from knowledge_forge.portable_native_approval_request import (
    ALLOWED_CLAIM_LABELS, HOSTS, LIMITS, REQUIREMENTS, SNAPSHOTS,
    NativeApprovalRequestError, main, packet,
)
from knowledge_forge.portable_native_sandbox_contract import PROOF_GATES, contract
from knowledge_forge.portable_native_hook_trust_review import review


class NativeApprovalRequestTests(unittest.TestCase):
    def test_both_hosts_exact_parent_and_nine_gates(self):
        for host in HOSTS:
            r = packet(host)
            self.assertEqual(r['parent_contract_sha256'], contract(host)['contract_sha256'])
            self.assertEqual(r['p8_hook_taxonomy_fingerprint_not_runtime_trust'],
                             review(host)['source_state_fingerprint_not_host_attestation'])
            self.assertEqual([g['gate'] for g in r['proof_gates']], list(PROOF_GATES))
            self.assertEqual(r['unverified_proof_gate_count'], 9)
            self.assertTrue(all(g['actual_native_status'] == 'UNVERIFIED_NATIVE_EVIDENCE_REQUIRED'
                                for g in r['proof_gates']))

    def test_p6_snapshot_sha256_and_size_are_observation_not_fresh_pin(self):
        refs = {'claude-code': ('2.1.296', 257068216, '24972e3bc859fab2b46ed4c1e51f7d6130f06d3bd550811a114640de3370d0de'),
                'codex-cli': ('0.161.0', 292309256, '9a820c17865fa825d04db416818679a9d63bd72e50835c396f496e5684626c9c')}
        for host, (version, size, digest) in refs.items():
            s = packet(host)['p6_historical_executable_snapshot_not_fresh_attestation']
            self.assertEqual((s['version_reference_at_snapshot'],s['file_size_bytes_at_snapshot'],
                              s['sha256_at_snapshot_only']), (version, size, digest))
            self.assertFalse(packet(host)['installer_provenance_verified'])
            self.assertFalse(packet(host)['dependency_closure_verified'])

    def test_never_turns_plan_into_permission(self):
        for host in HOSTS:
            r = packet(host)
            for flag in ('request_token_issued','approval_signature_issued',
                         'single_use_mechanism_implemented_or_proven','human_identity_authenticated',
                         'human_authorization_received','native_launch_authorized',
                         'native_host_started','native_hook_dispatched','actual_host_isolation_verified',
                         'actual_hook_sources_verified','dependency_closure_verified',
                         'installer_provenance_verified','model_called','tools_executed',
                         'network_traffic_sent','credentials_accessed','hooks_installed',
                         'user_configuration_modified','promotion_authorized'):
                self.assertIs(r[flag], False, (host,flag))
            self.assertIsNone(r['authorization_nonce'])
            self.assertIsNone(r['authorization_expiry'])
            self.assertIsNone(r['approved_host_test_scope'])
            self.assertEqual(r['tool_grants'], [])

    def test_proposal_is_one_shot_but_unenforced(self):
        for host in HOSTS:
            r = packet(host)
            self.assertEqual(r['proposed_single_use_limits_not_enforced'], LIMITS)
            self.assertEqual(LIMITS['max_native_host_launches_if_later_authorized'], 1)
            self.assertEqual(LIMITS['max_synthetic_hook_events_if_later_authorized'], 1)
            self.assertEqual(LIMITS['max_wall_clock_seconds_if_later_authorized'], 20)
            self.assertEqual(LIMITS['max_authorization_valid_seconds_if_later_issued'], 600)
            self.assertTrue(r['single_use_requires_future_host_owned_nonce_and_atomic_consumption'])
            self.assertFalse(r['single_use_mechanism_implemented_or_proven'])

    def test_zero_network_credentials_model_tools(self):
        for k in ('model_calls_allowed','tool_calls_allowed','expected_hook_stdout_bytes',
                  'expected_hook_stderr_bytes'):
            self.assertEqual(LIMITS[k],0)
        for k in ('outbound_network_allowed','user_credentials_mounted_allowed',
                  'user_home_or_project_mounted_allowed','operator_promotion_allowed'):
            self.assertIs(LIMITS[k],False)

    def test_exact_eight_unverified_preconditions(self):
        self.assertEqual(len(REQUIREMENTS),8)
        for host in HOSTS:
            r = packet(host)
            self.assertEqual([x['requirement'] for x in r['blocking_preconditions_unverified']],
                             [x[0] for x in REQUIREMENTS])
            self.assertTrue(all(x['status']=='BLOCKING_UNVERIFIED'
                                for x in r['blocking_preconditions_unverified']))

    def test_default_labels_stay_unverified(self):
        for host in HOSTS:
            self.assertTrue(all(x['claim_unverified_only']=='NOT_OBSERVED' for x in packet(host)['proof_gates']))

    def test_all_unverified_claims_allowed_but_never_promoted(self):
        for host in HOSTS:
            d={g:'CLAIMED_UNVERIFIED' for g in PROOF_GATES}
            r=packet(host,unverified_claims=d)
            self.assertTrue(all(x['actual_native_status']=='UNVERIFIED_NATIVE_EVIDENCE_REQUIRED' for x in r['proof_gates']))
            self.assertFalse(r['native_launch_authorized'])

    def test_forged_verified_or_approved_claim_refused(self):
        for bad in ('PASS','VERIFIED','AUTHENTICATED','SIGNED','APPROVED','AUTHORIZED',True,None,1,[],{}):
            d={g:'NOT_OBSERVED' for g in PROOF_GATES}
            d[PROOF_GATES[0]]=bad
            with self.subTest(bad=bad), self.assertRaises(NativeApprovalRequestError):
                packet('codex-cli',unverified_claims=d)

    def test_partial_or_extra_claims_refused(self):
        d={g:'NOT_OBSERVED' for g in PROOF_GATES}
        d.pop(PROOF_GATES[0])
        with self.assertRaises(NativeApprovalRequestError):packet('codex-cli',unverified_claims=d)
        d={g:'NOT_OBSERVED' for g in PROOF_GATES}
        d['bonus_trusted_gate']='NOT_OBSERVED'
        with self.assertRaises(NativeApprovalRequestError):packet('claude-code',unverified_claims=d)

    def test_wrong_claim_container_refused(self):
        for val in ('NOT_OBSERVED',[],True,123,(),None):
            if val is None:continue  # None requests canonical default template.
            with self.assertRaises(NativeApprovalRequestError):packet('claude-code',unverified_claims=val)

    def test_invalid_host_refused(self):
        for bad in ('','codex --help',None,True,42,{},'Claude'):
            with self.assertRaises(NativeApprovalRequestError):packet(bad)

    def test_deterministic_fingerprint_is_not_signature(self):
        for host in HOSTS:
            a=packet(host)
            b=packet(host)
            self.assertEqual(a,b)
            self.assertEqual(len(a['review_fingerprint_not_signature_or_approval']),64)
            self.assertFalse(a['approval_signature_issued'])

    def test_two_hosts_not_same_fingerprint(self):
        a=packet('claude-code')['review_fingerprint_not_signature_or_approval']
        b=packet('codex-cli')['review_fingerprint_not_signature_or_approval']
        self.assertNotEqual(a,b)

    def test_host_snapshot_data_not_mutated(self):
        saved=copy.deepcopy(SNAPSHOTS)
        got=packet('claude-code')
        got['p6_historical_executable_snapshot_not_fresh_attestation']['sha256_at_snapshot_only']='x'
        self.assertEqual(SNAPSHOTS,saved)

    def test_no_files_network_or_subprocess_during_packet(self):
        with patch('builtins.open',side_effect=AssertionError('filesystem forbidden')), \
             patch('subprocess.run',side_effect=AssertionError('subprocess forbidden')):
            self.assertEqual(packet('codex-cli')['native_launch_authorized'],False)

    def test_native_modes_refuse_with_no_stdout(self):
        for host,mode in [('codex-cli','NATIVE_TEST'),('claude-code','ON'),
                          ('unknown','REQUEST_REVIEW_ONLY'),('codex-cli','APPROVED')]:
            cp=subprocess.run([sys.executable,'-m','knowledge_forge.portable_native_approval_request',
                               '--host',host,'--mode',mode],capture_output=True,timeout=8)
            self.assertEqual(cp.returncode,2)
            self.assertEqual(cp.stdout,b'')

    def test_review_cli_no_secret_or_host_launch(self):
        with patch('subprocess.run',side_effect=AssertionError('spawn forbidden')):
            with patch('builtins.print') as sink:
                self.assertEqual(main(['--host','claude-code','--mode','REQUEST_REVIEW_ONLY']),0)
        r=json.loads(sink.call_args.args[0])
        self.assertFalse(r['native_launch_authorized'])
        self.assertFalse(r['request_token_issued'])
        self.assertEqual(r['unverified_proof_gate_count'],9)

    def test_no_paths_tokens_config_or_identity_in_review(self):
        for host in HOSTS:
            raw=json.dumps(packet(host)).lower()
            for forbidden in ('/home/tommy','private_key','bearer ','access_token','api_key',
                              'approved": true','signature":'):
                self.assertNotIn(forbidden,raw)

    def test_can_only_reach_p10_after_external_gate(self):
        for host in HOSTS:
            r=packet(host)
            self.assertIn('ONLY_IF_SEPARATELY_AUTHORIZED_AND_UNBLOCKED',r['next_cut'])
            self.assertEqual(r['state'],'UNISSUED_PROPOSAL_BLOCKED_PENDING_INDEPENDENT_EVIDENCE')

    def test_p9_review_packet_never_modifies_canonical_gates(self):
        for host in HOSTS:
            before=list(PROOF_GATES)
            packet(host)
            self.assertEqual(before,list(PROOF_GATES))
            self.assertEqual(len(PROOF_GATES),9)


if __name__=='__main__':
    unittest.main()
