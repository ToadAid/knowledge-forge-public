"""P9B synthetic full CLI acceptance; never a native-agent or promotion test."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import subprocess
import sys
import unittest
from unittest.mock import patch

from knowledge_forge.portable_acceptance_pilot import (
    AcceptancePilotError, CLAIM, CLAIM_KEY, SOURCE, NO_MATCH_DISCOVERY_ERROR,
    _check_repo, _require_discovery_refusal, _options, _support_plan, main, run_pilot,
)


class P9BContractTests(unittest.TestCase):
    def test_fixture_is_synthetic_and_structured_non_executable(self):
        self.assertEqual(CLAIM['claim_key'], CLAIM_KEY)
        self.assertFalse(CLAIM['executable'])
        self.assertIn('Concentrated Liquidity', SOURCE)
        self.assertIn('Unrelated material.', SOURCE)

    def test_plan_does_not_claim_independent_judgment(self):
        p = _support_plan()
        self.assertIn('synthetic:test:assertion-only', p['judge_id'])
        self.assertEqual(p['units'][0]['section_ids'], ['s001'])
        self.assertEqual(p['units'][0]['entailment'], 'SUPPORTED')
        self.assertEqual(_support_plan(entailment='UNSUPPORTED')['units'][0]['entailment'], 'UNSUPPORTED')

    def test_plan_mutation_cannot_alter_global_claim(self):
        prior = copy.deepcopy(CLAIM)
        p = _support_plan()
        p['units'][0]['claim_key'] = 'forged'
        self.assertEqual(CLAIM, prior)

    def test_options_no_operator_or_native_hook(self):
        self.assertNotIn('operator', ' '.join(_options()))
        self.assertNotIn('claude-code', ' '.join(_options()))

    def test_reject_unknown_mode_without_running_pilot(self):
        with patch('knowledge_forge.portable_acceptance_pilot.run_pilot',
                   side_effect=AssertionError('pilot must not run')):
            for mode in ('ON', 'PROMOTE', 'NATIVE_TEST', 'DRY_RUN', 'INSTALL_HOOK'):
                self.assertEqual(main(['--mode', mode]), 2)

    def test_exact_no_match_is_expected_fail_closed_cli_refusal(self):
        payload = {'schema_version': '0.1', 'ok': False,
                   'command': 'discover', 'error': NO_MATCH_DISCOVERY_ERROR}
        with TemporaryDirectory() as tmp:
            with patch('subprocess.run', return_value=SimpleNamespace(
                    returncode=2, stdout=b'', stderr=json.dumps(payload).encode())) as spawned:
                self.assertTrue(_require_discovery_refusal(
                    Path('/synthetic/knowledge-forge'), Path('/synthetic/registry.json'),
                    'What does the source say?', Path(tmp)))
        argv = spawned.call_args.args[0]
        self.assertEqual(argv[1], 'discover')
        self.assertIn('--max-skills', argv)
        self.assertEqual(spawned.call_args.kwargs['input'], b'')

    def test_wrong_refusal_or_claimed_success_cannot_count_as_discovery_miss(self):
        good = {'schema_version': '0.1', 'ok': False,
                'command': 'discover', 'error': NO_MATCH_DISCOVERY_ERROR}
        variants = [
            (2, b'', dict(good, error='registry validation failed')),
            (2, b'', dict(good, command='load-current')),
            (2, b'', dict(good, ok=True)),
            (2, b'', dict(good, added='unexpected')),
            (0, b'', good),
            (2, b'{"unexpected":true}', good),
        ]
        with TemporaryDirectory() as tmp:
            for rc, stdout, packet in variants:
                with self.subTest(rc=rc, data=packet), patch('subprocess.run',
                     return_value=SimpleNamespace(returncode=rc, stdout=stdout,
                                                  stderr=json.dumps(packet).encode())):
                    with self.assertRaises(AcceptancePilotError):
                        _require_discovery_refusal(
                            Path('/synthetic/knowledge-forge'),
                            Path('/synthetic/registry.json'), 'question', Path(tmp))

    def test_discovery_no_match_contract_is_expressly_not_empty_success(self):
        self.assertIn('no governed knowledge skill matched', NO_MATCH_DISCOVERY_ERROR)
        self.assertNotIn('match_count', NO_MATCH_DISCOVERY_ERROR)

    def test_bad_checkout_refused_without_discovery(self):
        for bad in (Path('.'), Path('/tmp/not-a-checkout-for-p9b')):
            with self.assertRaises(AcceptancePilotError):
                _check_repo(bad)


class P9BRealOrdinaryCliTests(unittest.TestCase):
    """Real CLI subprocesses run ONCE per suite; no mocks for the happy path."""

    @classmethod
    def setUpClass(cls):
        cls.checkout = Path(__file__).resolve().parents[1]
        cls.report = run_pilot(cls.checkout)

    def test_end_to_end_exact_result_state(self):
        self.assertEqual(self.report['state'],
                         'SYNTHETIC_CLI_ACCEPTANCE_PASS_NOT_GOVERNED_CONSUMPTION')
        self.assertEqual(self.report['surface'],
                         'P9B_SYNTHETIC_ORDINARY_CLI_ACCEPTANCE_PILOT_NOT_AGENT_INTEGRATION')

    def test_authoring_and_validation_were_real(self):
        for key in ('prepare_cli_pass', 'finalize_cli_validated_pass', 'validator_cli_pass',
                    'source_bytes_unchanged', 'validated_lifecycle_not_promoted',
                    'emitted_source_provenance_bound'):
            self.assertIs(self.report[key], True, key)

    def test_scope_one_of_two_sections(self):
        self.assertEqual(self.report['synthetic_source_sections'], 2)
        self.assertEqual(self.report['supported_unit_count'], 1)
        self.assertEqual(self.report['omitted_source_sections'], 1)

    def test_current_discovery_refuses_unpromoted(self):
        self.assertEqual(self.report['empty_registry_current_skills'], 0)
        self.assertTrue(self.report['promoted_discovery_no_match_refused'])
        self.assertTrue(self.report['out_of_scope_discovery_no_match_refused'])
        self.assertEqual(self.report['discovery_miss_surface'],
                         'CLI_EXIT_2_STRUCTURED_STDERR_REFUSAL')
        self.assertNotIn('promoted_discovery_match_count', self.report)
        self.assertTrue(self.report['load_current_unpromoted_refused'])

    def test_negative_controls(self):
        for key in ('changed_source_binding_refused', 'unsupported_entailment_refused',
                    'package_tamper_refused'):
            self.assertIs(self.report[key], True, key)

    def test_no_promotion_host_adapter_or_model_claims(self):
        for key in ('separately_promoted_registry_constructed', 'human_approval_authenticated',
                    'native_agent_host_started', 'model_question_answering_tested',
                    'host_adapter_tested', 'operator_invoked', 'knowledge_promoted',
                    'automatically_consumed_knowledge'):
            self.assertIs(self.report[key], False, key)
        self.assertEqual(self.report['execution_authority'], 'NONE')
        self.assertEqual(self.report['tool_grants'], [])

    def test_report_does_not_expose_user_path_or_auth(self):
        data = json.dumps(self.report).lower()
        for fragment in ('/home/tommy', 'bearer ', 'api_key', 'private_key', 'access_token'):
            self.assertNotIn(fragment, data)

    def test_run_cleans_up_synthetic_workspace(self):
        self.assertTrue(self.report['synthetic_fixture_cleanup_complete'])


if __name__ == '__main__':
    unittest.main()
