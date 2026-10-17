"""No real provider calls, enrollment, host configuration or socket activation."""
from io import StringIO
import json
import os
from pathlib import Path
import queue
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('codex_native_operator')
from knowledge_forge import codex_enrollment as enroll
from knowledge_forge import codex_native_operator as native
from knowledge_forge import protected_operator_service as core
from knowledge_forge import native_deployment as deployment
from knowledge_forge.codex_mcp_server import CodexApprovalMcpServer, main as mcp_main


class NativeContractTests(unittest.TestCase):
    def connection(self, uid=995, path=str(core.CODEX_SOCKET_PATH)):
        connection = Mock()
        connection.getsockname.return_value = path
        connection.getsockopt.return_value = struct.pack('3i', 12345, uid, uid)
        return connection

    def policy(self):
        return core.OperatorPolicy(code_root=native.OPERATOR_CODE_ROOT, state_root=native.STATE_ROOT,
                                   agent_user='kf-agent', operator_user='kf-operator')

    def test_native_socket_refuses_human_socket_before_loading_state(self):
        with patch.object(core, '_load_policy') as load, patch.object(core, 'send_frame'):
            with self.assertRaises(core.ProtectedOperatorError):
                native.serve(self.connection(path='/run/toadaid-knowledge-forge/operator.sock'))
            load.assert_not_called()

    def test_native_socket_refuses_wrong_kernel_peer(self):
        for uid in (0, 994, 1000):
            with self.subTest(uid=uid), patch.object(core, '_load_policy', return_value=self.policy()), \
                    patch.object(core, 'verify_runtime', return_value=(994,995)), \
                    patch.object(core, 'send_frame'), patch.object(core, 'handle_connection') as handle:
                with self.assertRaises(core.ProtectedOperatorError):
                    native.serve(self.connection(uid=uid))
                handle.assert_not_called()

    def test_native_endpoint_refuses_typed_and_generic_operations(self):
        for operation in ('PREPARE', 'CONFIRM', 'APPROVE', 'SUBMIT', 'POLL', 'COMPLETE_CODEX'):
            with self.subTest(operation=operation), patch.object(core, '_load_policy', return_value=self.policy()), \
                    patch.object(core, 'verify_runtime', return_value=(994,995)), \
                    patch.object(core, 'recv_frame', return_value={'operation':operation}), \
                    patch.object(core, 'send_frame'), patch.object(core, 'handle_connection') as handle:
                with self.assertRaisesRegex(core.ProtectedOperatorError, 'Proof-only'):
                    native.serve(self.connection())
                handle.assert_not_called()

    def test_native_snapshot_changes_code_root_only(self):
        original = self.policy()
        with patch.object(core, '_load_policy', return_value=original), \
                patch.object(core, 'verify_runtime', return_value=(994,995)) as runtime, \
                patch.object(core, 'recv_frame', return_value={'operation':'PREPARE_CODEX'}), \
                patch.object(core, 'handle_connection') as handle:
            native.serve(self.connection())
        self.assertEqual(runtime.call_args.args[0].code_root, native.NATIVE_CODE_ROOT)
        self.assertEqual(original.code_root, native.OPERATOR_CODE_ROOT)
        self.assertEqual(handle.call_args.kwargs['policy'].state_root, native.STATE_ROOT)

    def test_human_socket_refuses_native_proof_from_human_peer(self):
        with patch.object(core, '_load_policy', return_value=self.policy()), \
                patch.object(core, 'verify_runtime', return_value=(994,995)), \
                patch.object(core, 'recv_frame', return_value={'operation':'PREPARE_CODEX'}), \
                patch.object(core, 'send_frame'), patch.object(core, 'handle_connection') as handle:
            with self.assertRaisesRegex(core.ProtectedOperatorError, 'configured agent UID'):
                core.serve_connected_socket(self.connection(uid=1000, path='/run/toadaid-knowledge-forge/operator.sock'))
            handle.assert_not_called()

    def test_mcp_refuses_human_uid_before_serving(self):
        with patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=995)), \
                patch('os.getuid', return_value=1000), patch('os.geteuid', return_value=1000), \
                patch('knowledge_forge.codex_mcp_server.CodexApprovalMcpServer') as server, patch('sys.stderr', StringIO()):
            self.assertEqual(mcp_main(['--workspace-root','/synthetic']), 2)
            server.assert_not_called()

    def test_mcp_never_accepts_fallback_socket(self):
        with self.assertRaises(ValueError):
            CodexApprovalMcpServer(workspace_root=Path(__file__).parent, operator_socket=Path('/run/toadaid-knowledge-forge/operator.sock'),
                                  input_stream=StringIO(), output_stream=StringIO())

    def test_enrollment_protected_and_existing_outputs_refuse_before_provider(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(enroll, 'CodexAppServerClient') as client:
            path = Path(temporary)/'metadata.json'
            path.write_bytes(b'existing')
            link = Path(temporary)/'link'
            link.symlink_to(path)
            for output in (path, link, '/var/lib/toadaid-kf-operator/codex-credentials.json', '/etc/toadaid-kf/credential.json'):
                with self.assertRaises(enroll.CodexEnrollmentError):
                    enroll.enroll(output, principal='synthetic:reviewer')
            client.assert_not_called()
            self.assertEqual(path.read_bytes(), b'existing')

    def test_provider_unavailable_is_nonzero_without_enrollment(self):
        client = Mock()
        client.request.return_value = {'unavailableReason':'providerUnavailable','unavailableMessage':'private diagnostics'}
        with patch.object(enroll, 'CodexAppServerClient', return_value=client), patch('sys.stdout', StringIO()) as output:
            self.assertEqual(enroll.main(['--status-only']), 2)
        self.assertIn('providerUnavailable', output.getvalue())
        self.assertNotIn('private diagnostics', output.getvalue())
        client.request.assert_called_once_with('userVerification/status', 2)
        client.close.assert_called_once()

    def test_provider_rpc_error_is_explicit_and_sanitized(self):
        client = object.__new__(enroll.CodexAppServerClient)
        client.timeout_seconds = 1
        client.messages = queue.Queue()
        client.messages.put(json.dumps({'id':2,'error':{'code':-1,'message':'secret diagnostic',
            'data':{'type':'unavailable','reason':'providerUnavailable'}}}))
        with self.assertRaisesRegex(enroll.CodexEnrollmentError, 'providerUnavailable') as error:
            client._response(2)
        self.assertNotIn('secret', str(error.exception))

    def test_nullable_enrollment_metadata_is_not_enrollable(self):
        with tempfile.TemporaryDirectory() as temporary:
            for response in ({'credentialId':'id'}, {'credentialId':'id','publicKey':None,'algorithm':None},
                             {'credentialId':'id','publicKey':'public','algorithm':None}):
                client = Mock()
                client.request.return_value = response
                with patch.object(enroll, 'CodexAppServerClient', return_value=client):
                    with self.assertRaises(enroll.CodexEnrollmentError):
                        enroll.enroll(Path(temporary)/'new.json', principal='synthetic:reviewer')
                self.assertFalse((Path(temporary)/'new.json').exists())

    def test_status_protocol_support_is_separate_from_provider(self):
        self.assertTrue(enroll.provider_readiness({'unavailableReason':'providerUnavailable'})['protocol_supported'])
        self.assertFalse(enroll.provider_readiness({})['available'])
        with self.assertRaises(enroll.CodexEnrollmentError):
            enroll.provider_readiness({'status':'available'})

    @unittest.skipUnless(os.environ.get('KF_EXPERIMENTAL_SCHEMA'), 'Set KF_EXPERIMENTAL_SCHEMA to locally generated experimental schema')
    def test_contract_against_local_experimental_schema(self):
        self.assertTrue(deployment.schema_support(Path(os.environ['KF_EXPERIMENTAL_SCHEMA'])))

# Exercise the existing protected engine in synthetic state only. No native
# control is displayed; no real operator socket or credential is contacted.
from datetime import timedelta
from tests import test_protected_operator_service as engine_fixtures
CLOCK = engine_fixtures.CLOCK
from knowledge_forge.registry import load_skill_registry, save_skill_registry


class NativeEngineRefusalTests(unittest.TestCase):
    def setUp(self):
        engine_fixtures.ProtectedOperatorServiceTests.setUp(self)
        self.agent = core.PeerIdentity(pid=12345, uid=995, gid=995, principal='codex-agent-transport')
        self.session, challenge = core.prepare_codex_promotion({'operation':'PREPARE_CODEX','bundle':self.bundle},
                policy=self.policy, peer=self.agent, now=CLOCK)
        self.response = {'operation':'COMPLETE_CODEX', 'challenge_id':challenge['challenge_id'],
            'elicitation_response': {'jsonrpc':'2.0','id':challenge['elicitation']['id'],
                 'result':{'action':'accept', 'content':{'credentialId':'synthetic-device','signature':'c2ln'}}}}

    def assert_not_promoted(self):
        self.assertNotIn(self.manifest['skill_id'], load_skill_registry(self.state/'registry.json')['skills'])
        self.assertEqual(list((self.state/'receipts').iterdir()), [])

    def test_missing_credential_refuses_without_promotion(self):
        with self.assertRaises(core.ProtectedOperatorError):
            core.confirm_codex_promotion(self.session, self.response, now=CLOCK)
        self.assert_not_promoted()

    def test_expired_native_proof_cannot_be_reused(self):
        with patch.object(core, 'verify_registered_proof', return_value='synthetic:human'), \
                patch.object(core, 'promote_validated_skill') as promote:
            with self.assertRaises(core.ProtectedOperatorError):
                core.confirm_codex_promotion(self.session, self.response, now=CLOCK+timedelta(minutes=6))
            with self.assertRaises(core.ProtectedOperatorError):
                core.confirm_codex_promotion(self.session, self.response, now=CLOCK+timedelta(minutes=7))
            promote.assert_not_called()
        self.assert_not_promoted()

    def test_changed_registry_native_proof_refuses(self):
        registry = load_skill_registry(self.state/'registry.json')
        registry['updated_at'] = '2026-10-16T04:00:01Z'
        save_skill_registry(registry, self.state/'registry.json')
        with patch.object(core, 'verify_registered_proof', return_value='synthetic:human'), \
                patch.object(core, 'promote_validated_skill') as promote:
            with self.assertRaises(core.ProtectedOperatorError):
                core.confirm_codex_promotion(self.session, self.response, now=CLOCK)
            promote.assert_not_called()
        self.assert_not_promoted()

    def test_cancel_and_malformed_native_proof_do_not_verify_or_promote(self):
        for result in ({'action':'cancel'}, {'action':'accept','content':{'credentialId':'other'}},
                       {'action':'accept','content':{'credentialId':'other','signature':'sig','extra':'bad'}}):
            self.response['elicitation_response']['result'] = result
            with patch.object(core, 'verify_registered_proof') as verifier, patch.object(core, 'promote_validated_skill') as promote:
                with self.assertRaises(core.ProtectedOperatorError):
                    core.confirm_codex_promotion(self.session, self.response, now=CLOCK)
                verifier.assert_not_called()
                promote.assert_not_called()
        self.assert_not_promoted()


if __name__ == '__main__':
    unittest.main()
