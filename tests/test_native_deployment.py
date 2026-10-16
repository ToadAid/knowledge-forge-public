"""Offline migration fixtures: never invoke host installers or protected sockets."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge import native_deployment as n

ROOT = Path(__file__).resolve().parents[1]
COMMIT = 'a' * 40


class FakeHost(n.Host):
    def __init__(self, root, layout):
        self.root, self.layout = root, layout
        self.overrides, self.calls = {}, []
        self.uid = 1000
        self.ids = {'kf-agent': {'uid': 995, 'gid': 995, 'groups': {'kf-agent'}, 'shell': '/usr/sbin/nologin', 'home': '/nonexistent'},
                    'kf-operator': {'uid': 994, 'gid': 994, 'groups': {'kf-operator'}, 'shell': '/usr/sbin/nologin', 'home': '/nonexistent'},
                    'human': {'uid': 1000, 'gid': 1000, 'groups': {'human', 'kf-reviewers'}}, 'reviewer_gid': 981}

    def metadata(self, path):
        if path in self.overrides:
            return self.overrides[path]
        if path not in (self.root, *self.root.parents) and self.root not in path.parents:
            raise AssertionError('Unexpected host inspection: ' + str(path))
        if path in self.root.parents:
            return {'kind': 'directory', 'uid': 0, 'gid': 0, 'mode': 0o755}
        value = super().metadata(path)
        if value:
            private = path == self.layout.state or self.layout.state in path.parents
            value['uid'] = value['gid'] = 994 if private else 0
        return value

    def identities(self, reviewer):
        if reviewer != 'human':
            raise KeyError(reviewer)
        return deepcopy(self.ids)

    def systemd(self):
        return True

    def observer_uid(self):
        return self.uid

    def unit_absent(self, name):
        return True

    def unit_dormant(self, name):
        return True

    def run(self, args):
        self.calls.append(args)


@unittest.skipUnless(os.name == "posix", "Linux deployment uses POSIX owner/mode and UID fixtures")
class NativeDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kf-native-fixture-')
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        root.chmod(0o755)
        self.layout = n.Layout(operator=root/'opt/operator', native=root/'opt/native',
            config=root/'etc/kf/operator.json', state=root/'var/lib/operator',
            units=root/'etc/systemd/system', runtime=root/'run/kf')
        l = self.layout
        for directory in (l.operator, l.config.parent, l.state, l.units, l.runtime):
            directory.mkdir(parents=True)
            directory.chmod(0o700 if directory == l.state else 0o755)
        for directory in root.rglob('*'):
            if directory.is_dir() and directory != l.state:
                directory.chmod(0o755)
        self.host = FakeHost(root, l)
        self.archive = {'src/knowledge_forge/' + p.name: p.read_bytes()
                        for p in (ROOT/'src/knowledge_forge').glob('*.py')}
        for name in ('knowledge-forge-operator@.service', 'knowledge-forge-operator.socket', n.SOCKET_NAME, n.SERVICE_NAME):
            key = 'deploy/linux-protected-operator/' + name
            self.archive[key] = (ROOT/key).read_bytes()
            if name not in (n.SOCKET_NAME, n.SERVICE_NAME):
                n.put_new(l.units/name, self.archive[key])
        installed = {}
        for key, data in self.archive.items():
            if not key.startswith('src/') or key.endswith(('/codex_native_operator.py', '/native_deployment.py')):
                continue
            n.put_new(l.operator/key, data)
            installed[key] = n.digest(data)
        for name, module in {'protected-operator':'protected_operator_cli', 'review-daemon':'generic_approval_reviewer',
                             'request':'generic_approval_client', 'approval-mcp':'generic_approval_mcp'}.items():
            key = 'bin/knowledge-forge-' + name
            data = n.launch(module).replace(b'/opt/toadaid-kf-agent/src', str(l.operator/'src').encode())
            n.put_new(l.operator/key, data, 0o755)
            installed[key] = n.digest(data)
        n.put_new(l.operator/'DEPLOYMENT.json', json.dumps({'reviewed_commit': 'b'*40, 'installed_sha256': installed}).encode())
        n.put_new(l.config, json.dumps({'schema_version':'0.1', 'agent_user':'kf-agent', 'operator_user':'kf-operator',
            'code_root':str(l.operator), 'state_root':str(l.state)}).encode())
        for name in ('registry.json', 'authorizations.sqlite3'):
            n.put_new(l.state/name, b'valuable-state-do-not-read', 0o600)
        for name in ('receipts', 'lifecycles'):
            (l.state/name).mkdir(mode=0o700)
            n.put_new(l.state/name/'valuable', b'preserve-identically', 0o600)
        self.before = self.state_snapshot()

    def state_snapshot(self):
        return {str(p): (p.read_bytes(), p.stat().st_mode, p.stat().st_mtime_ns)
                for p in [self.layout.config, *self.layout.state.rglob('*')] if p.is_file()}

    def migrate(self, **kwargs):
        return n.migrate(self.archive, COMMIT, 'human', host=self.host, layout=self.layout, **kwargs)

    def apply(self):
        with patch.object(n.os, 'geteuid', return_value=0), patch.object(n.sys, 'platform', 'linux'):
            return self.migrate(apply=True)

    def test_plan_reads_no_state_contents_and_writes_nothing(self):
        original = n.read_public
        def public(path, *args):
            self.assertFalse(path == self.layout.state or self.layout.state in path.parents)
            return original(path, *args)
        with patch.object(n, 'read_public', side_effect=public):
            result = self.migrate()
        self.assertEqual(result['status'], 'ABSENT')
        self.assertFalse(self.layout.native.exists())
        self.assertEqual(self.before, self.state_snapshot())
        self.assertEqual(self.host.calls, [])
        self.assertFalse(result['receipt']['promotion_performed'])

    def test_apply_preserves_every_state_byte_metadata_and_config(self):
        self.assertEqual(self.apply()['status'], 'INSTALLED_DORMANT_NOT_READY')
        self.assertEqual(self.before, self.state_snapshot())
        self.assertEqual([c[1] for c in self.host.calls], ['verify', 'daemon-reload'])
        self.assertFalse((self.layout.native/'.migration-in-progress').exists())
        receipt = json.loads(self.layout.receipt.read_bytes())
        self.assertLess(self.layout.receipt.stat().st_size, 65536)
        self.assertFalse(receipt['state_contents_read'])
        self.assertFalse(receipt['state_reinitialized'])
        self.assertFalse(receipt['socket_enabled'])
        self.assertFalse(receipt['personal_added'])

    def test_repeat_is_exact_read_only_inspection(self):
        self.apply()
        calls = list(self.host.calls)
        self.assertEqual(self.apply()['status'], 'COMPLETED_SAME_MIGRATION')
        self.assertEqual(calls, self.host.calls)
        self.assertEqual(self.before, self.state_snapshot())

    def test_repeat_refuses_changed_native_directory_mode(self):
        self.apply()
        (self.layout.native/'src').chmod(0o700)
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.assertEqual(self.before, self.state_snapshot())

    def test_repeat_refuses_unexpected_activation(self):
        self.apply()
        with patch.object(self.host, 'unit_dormant', return_value=False):
            with self.assertRaises(n.NativeDeploymentError):
                self.migrate()
        self.host.overrides[self.layout.socket] = {'kind':'socket','uid':994,'gid':995,'mode':0o660}
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.assertEqual(self.before, self.state_snapshot())

    def test_changed_commit_and_artifact_refuse_repeat(self):
        self.apply()
        with self.assertRaises(n.NativeDeploymentError):
            n.migrate(self.archive, 'c'*40, 'human', host=self.host, layout=self.layout)
        self.host.ids['kf-agent']['gid'] = 1995
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.host.ids['kf-agent']['gid'] = 995
        path = self.layout.native/'bin/knowledge-forge-codex-mcp'
        path.write_bytes(b'changed')
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()

    def test_partial_failure_is_not_silently_resumed(self):
        with patch.object(self.host, 'run', side_effect=OSError('simulated verify failure')):
            with self.assertRaises(OSError):
                self.apply()
        self.assertTrue((self.layout.native/'.migration-in-progress').exists())
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.assertEqual(self.before, self.state_snapshot())

    def test_partial_prior_units_and_unknown_native_root_refuse(self):
        n.put_new(self.layout.units/n.SOCKET_NAME, b'partial')
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        (self.layout.units/n.SOCKET_NAME).unlink()
        self.layout.native.mkdir()
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()

    def test_wrong_owner_group_modes_and_missing_state_refuse(self):
        for path, field, value in ((self.layout.config, 'uid', 1000), (self.layout.operator, 'mode', 0o775),
               (self.layout.state, 'gid', 995), (self.layout.state/'registry.json', 'mode', 0o644),
               (self.layout.runtime, 'mode', 0o750)):
            with self.subTest(path=path, field=field):
                meta = self.host.metadata(path)
                self.host.overrides[path] = {**meta, field: value}
                with self.assertRaises(n.NativeDeploymentError):
                    self.migrate()
                self.host.overrides.clear()
        (self.layout.state/'authorizations.sqlite3').unlink()
        with self.assertRaises(n.NativeDeploymentError):
            self.apply()
        self.assertFalse(self.layout.native.exists())

    def test_distinct_identities_no_human_agent_membership(self):
        for role, field, value in (('kf-agent', 'uid', 1000), ('human', 'groups', {'human','kf-reviewers','kf-agent'}),
                                   ('kf-operator', 'groups', {'kf-operator','sudo'}), ('kf-agent', 'shell', '/bin/bash')):
            old = self.host.ids[role][field]
            self.host.ids[role][field] = value
            with self.assertRaises(n.NativeDeploymentError):
                self.migrate()
            self.host.ids[role][field] = old

    def test_symlink_and_writable_ancestry_refuse(self):
        path = self.layout.operator/'src/knowledge_forge/registry.py'
        data = path.read_bytes()
        path.unlink()
        elsewhere = Path(self.temp.name)/'elsewhere'
        elsewhere.write_bytes(data)
        path.symlink_to(elsewhere)
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        path.unlink()
        n.put_new(path, data)
        path.parent.chmod(0o777)
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()

    def test_truncated_but_self_consistent_manifest_is_partial(self):
        manifest = self.layout.operator/'DEPLOYMENT.json'
        value = json.loads(manifest.read_bytes())
        missing = 'bin/knowledge-forge-request'
        (self.layout.operator/missing).unlink()
        del value['installed_sha256'][missing]
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(n.NativeDeploymentError, 'Incomplete existing'):
            self.migrate()
        self.assertFalse(self.layout.native.exists())

    def test_incompatible_engine_and_unknown_code_refuse(self):
        self.archive['src/knowledge_forge/registry.py'] += b'\n# incompatible'
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.archive['src/knowledge_forge/registry.py'] = (ROOT/'src/knowledge_forge/registry.py').read_bytes()
        n.put_new(self.layout.operator/'unknown.py', b'unknown')
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()

    def test_enablement_is_blocked_before_any_write(self):
        with self.assertRaisesRegex(n.NativeDeploymentError, 'enablement blocked'):
            self.migrate(enable=True)
        self.assertFalse(self.layout.native.exists())
        self.assertEqual(self.host.calls, [])

    def test_commit_must_be_exact(self):
        for commit in ('main', 'abc', 'a'*41, 'A'*40):
            with self.assertRaises(n.NativeDeploymentError):
                n.migrate(self.archive, commit, 'human', host=self.host, layout=self.layout)

    def test_preflight_unknowns_and_provider_unavailable_never_ready(self):
        self.apply()
        self.host.overrides[self.layout.socket] = {'kind':'socket','uid':994,'gid':995,'mode':0o660}
        self.host.overrides[self.layout.state/'codex-credentials.json'] = {'kind':'file','uid':994,'gid':994,'mode':0o600}
        self.host.uid = 995
        for provider in (None, {'credentialId':'public-id','unavailableReason':'providerUnavailable'}, {'credentialId':'public-id'}):
            result = n.preflight(self.archive, COMMIT, 'human', workspace=Path('/synthetic/workspace'),
                provider_status=provider, host=self.host, layout=self.layout)
            self.assertEqual(result['status'], 'BLOCKED')
            self.assertFalse(result['promotion_performed'])
            self.assertEqual(result['gates']['preflight_process_uid']['status'], 'PASS')
            self.assertEqual(result['gates']['live_expiry_and_replay_rejection']['status'], 'UNKNOWN')
        self.assertIsNone(result['observed_transport_uid'])
        self.assertEqual(result['gates']['current_transport_uid']['status'], 'UNKNOWN')
        self.host.uid = 1000
        result = n.preflight(self.archive, COMMIT, 'human', workspace=Path('/synthetic/workspace'), host=self.host, layout=self.layout)
        self.assertEqual(result['gates']['preflight_process_uid']['status'], 'FAIL')

    def test_unknown_loaded_or_enabled_unit_blocks_plan(self):
        with patch.object(self.host, 'unit_absent', return_value=False):
            with self.assertRaises(n.NativeDeploymentError):
                self.migrate()
        self.assertFalse(self.layout.native.exists())

    def test_state_symlink_and_credential_mode_refuse_without_reads(self):
        credential = self.layout.state/'codex-credentials.json'
        credential.symlink_to(self.layout.config)
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        credential.unlink()
        n.put_new(credential, b'synthetic-public-metadata', 0o644)
        with self.assertRaises(n.NativeDeploymentError):
            self.migrate()
        self.assertFalse(self.layout.native.exists())

    def test_duplicate_socket_directive_and_human_socket_binding_refuse(self):
        key = 'deploy/linux-protected-operator/' + n.SOCKET_NAME
        for data in (self.archive[key].replace(b'Accept=yes', b'Accept=yes\nSocketMode=0666'),
                     self.archive[key].replace(b'codex-operator.sock', b'operator.sock')):
            with self.assertRaises(n.NativeDeploymentError):
                n.artifacts({**self.archive, key: data})

    def test_preflight_exact_arguments_missing_credential_and_wrong_acl_block(self):
        self.apply()
        mcp = {'mcp_servers': {'knowledge_forge_approval': {
            'command': '/opt/toadaid-kf-agent/bin/knowledge-forge-codex-mcp',
            'args': ['--workspace-root', '/synthetic', '--operator-socket', '/run/toadaid-knowledge-forge/codex-operator.sock']}}}
        result = n.preflight(self.archive, COMMIT, 'human', workspace=Path('/synthetic'),
                            mcp_config=mcp, host=self.host, layout=self.layout)
        self.assertEqual(result['gates']['exact_mcp_configuration']['status'], 'PASS')
        self.assertEqual(result['gates']['credential_metadata_only']['status'], 'UNKNOWN')
        self.host.overrides[self.layout.socket] = {'kind':'socket','uid':994,'gid':981,'mode':0o666}
        mcp['mcp_servers']['knowledge_forge_approval']['args'][-1] = '/run/toadaid-knowledge-forge/operator.sock'
        result = n.preflight(self.archive, COMMIT, 'human', workspace=Path('/synthetic'),
                            mcp_config=mcp, host=self.host, layout=self.layout)
        self.assertEqual(result['gates']['exact_mcp_configuration']['status'], 'FAIL')
        self.assertNotEqual(result['gates']['native_socket_exact_acl']['status'], 'PASS')
        self.assertEqual(self.before, self.state_snapshot())
        self.assertEqual(self.host.calls[-1], ['/usr/bin/systemctl', 'daemon-reload'])

    def test_socket_identity_and_acl_cannot_be_widened(self):
        for old, new in ((b'SocketMode=0660',b'SocketMode=0666'), (b'SocketGroup=kf-agent',b'SocketGroup=kf-reviewers')):
            archive = dict(self.archive)
            key = 'deploy/linux-protected-operator/' + n.SOCKET_NAME
            archive[key] = archive[key].replace(old, new)
            with self.assertRaises(n.NativeDeploymentError):
                n.artifacts(archive)
        launcher = n.launch('codex_mcp_server', transport=True)
        self.assertIn(b'os.getuid()!=u', launcher)
        self.assertNotIn(b'sudo', launcher)
        self.assertNotIn(b'operator.sock', launcher)


if __name__ == '__main__':
    unittest.main()
