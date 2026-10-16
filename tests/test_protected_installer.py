"""Administrator installer regression using disposable paths and fake OS commands."""
from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('protected_installer_under_test', REPO / 'scripts/install_protected.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class FakeHost:
    def __init__(self, existing=True):
        self.calls = []
        self.accounts = {'human': {'name': 'human', 'uid': 1000, 'gid': 1000,
                                  'shell': '/bin/bash', 'home': '/home/human', 'groups': ['human']}}
        self.groups = {}
        self.locked = {'kf-agent': True, 'kf-operator': True}
        self.fail_at = None
        self.loaded_unit = None
        if existing:
            for index, name in enumerate(('kf-agent', 'kf-operator')):
                self.groups[name] = {'gid': 800 + index, 'members': []}
                self.accounts[name] = {'name': name, 'uid': 900 + index, 'gid': 800 + index,
                                       'shell': '/usr/sbin/nologin', 'home': '/nonexistent', 'groups': [name]}
            self.groups['kf-reviewers'] = {'gid': 802, 'members': []}

    def account(self, name):
        return self.accounts.get(name)

    def group(self, name):
        return self.groups.get(name)

    def password_locked(self, name):
        return self.locked[name]

    def primary_users(self, gid):
        return {name for name, account in self.accounts.items() if account['gid'] == gid}

    def owner(self, path, uid, gid):
        self.calls.append(['chown', str(path), uid, gid])

    def unit_load_state(self, name):
        return 'loaded' if name == self.loaded_unit else 'not-found'

    def run(self, args):
        self.calls.append(args)
        if self.fail_at and self.fail_at in ' '.join(args):
            raise subprocess.CalledProcessError(1, args)
        if args[0].endswith('/groupadd'):
            self.groups[args[-1]] = {'gid': 800 + len(self.groups), 'members': []}
        elif args[0].endswith('/useradd'):
            name = args[-1]
            self.accounts[name] = {'name': name, 'uid': 900 + len(self.accounts),
                                   'gid': self.groups[name]['gid'], 'shell': '/usr/sbin/nologin',
                                   'home': '/nonexistent', 'groups': [name]}
        elif args[0].endswith('/usermod'):
            self.groups['kf-reviewers']['members'].append(args[-1])


@unittest.skipUnless(sys.platform == 'linux', 'Linux deployment and Git snapshot tests')
class ProtectedInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name, suffix in {'CODE': 'opt/code', 'STATE': 'var/lib/state', 'CONFIG': 'etc/kf/operator.json',
                             'SYSTEM': 'etc/systemd/system', 'USER': 'etc/systemd/user',
                             'TMPFILES': 'etc/tmpfiles.d/kf.conf', 'RUNTIME': 'run/forge'}.items():
            context = patch.object(installer, name, self.root / suffix)
            context.start()
            self.addCleanup(context.stop)
        installer.STATE.parent.mkdir(parents=True)
        installer.RUNTIME.parent.mkdir(parents=True)
        self.host = FakeHost()
        self.files = {'src/knowledge_forge/__init__.py': b'', 'LICENSE': b'test license', 'NOTICE': b'test notice'}
        for name in (*installer.UNITS, installer.REVIEWER_UNIT, 'knowledge-forge-runtime.conf'):
            self.files['deploy/linux-protected-operator/' + name] = (REPO / 'deploy/linux-protected-operator' / name).read_bytes()
        self.pin = 'a' * 40
        self.ancestry_check = installer.safe_ancestors
        context = patch.object(installer, 'safe_ancestors', return_value=None)
        context.start()
        self.addCleanup(context.stop)
        self.access = patch.object(installer.os, 'access', return_value=True)
        self.access.start()
        self.addCleanup(self.access.stop)

    def test_existing_accounts_deploy_and_explicit_socket_activation(self):
        previous_umask = os.umask(0o077)
        try:
            report = installer.deploy(self.host, self.files, self.pin, 'human', enable=True)
        finally:
            os.umask(previous_umask)
        commands = [' '.join(map(str, call)) for call in self.host.calls]
        self.assertFalse(any('/useradd ' in call or '/groupadd ' in call for call in commands))
        self.assertIn('/usr/sbin/usermod --append --groups kf-reviewers human', commands)
        self.assertTrue(any('runuser --user kf-operator -- ' in call and 'initialize-state' in call for call in commands))
        self.assertTrue(any('systemctl enable --now ' in call for call in commands))
        self.assertFalse(any('systemctl --user' in call for call in commands))
        self.assertFalse(any('codex-operator.socket' in call for call in commands))
        self.assertFalse(report['protected_deployment_verified'])
        self.assertFalse(report['reviewer_broker_started'])
        self.assertFalse(report['agent_settings_changed'])
        self.assertFalse(report['promotion_performed'])
        self.assertEqual(stat.S_IMODE(installer.STATE.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(installer.CONFIG.parent.stat().st_mode), 0o755)
        self.assertEqual(json.loads(installer.CONFIG.read_text())['agent_user'], 'kf-agent')
        for path in (installer.CODE, installer.CODE / 'src', installer.CODE / 'src/knowledge_forge'):
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
        for name in ('knowledge-forge-protected-operator', 'knowledge-forge-review-daemon',
                     'knowledge-forge-request', 'knowledge-forge-approval-mcp'):
            path = installer.CODE / 'bin' / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o755)
            self.assertIn('/usr/bin/python3 -I -B', path.read_text())
        manifest = json.loads((installer.CODE / 'DEPLOYMENT.json').read_text())
        self.assertEqual(manifest['reviewed_commit'], self.pin)

    def test_missing_accounts_are_created_without_login_or_supplementary_groups(self):
        self.host = FakeHost(existing=False)
        installer.deploy(self.host, self.files, self.pin, 'human')
        creates = [call for call in self.host.calls if str(call[0]).endswith('/useradd')]
        self.assertEqual(len(creates), 2)
        for call in creates:
            self.assertEqual(call[call.index('--password') + 1], '!')
            self.assertEqual(call[call.index('--groups') + 1], '')
            self.assertEqual(call[call.index('--shell') + 1], '/usr/sbin/nologin')
            self.assertIn('--no-create-home', call)
        self.assertFalse(any('enable' in call for call in self.host.calls))

    def test_rejects_unsafe_roles_before_any_commands(self):
        cases = [('uid', 0), ('uid', 1000), ('shell', '/bin/bash'), ('home', '/home/agent'),
                 ('groups', ['kf-agent', 'sudo']), ('gid', 0)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                host = FakeHost()
                host.accounts['kf-agent'][key] = value
                with self.assertRaises(installer.DeploymentError):
                    installer.deploy(host, self.files, self.pin, 'human')
                self.assertEqual(host.calls, [])
        self.host.locked['kf-agent'] = False
        with self.assertRaises(installer.DeploymentError):
            installer.deploy(self.host, self.files, self.pin, 'human')
        self.assertEqual(self.host.calls, [])

    def test_rejects_other_reviewers_and_primary_group_members(self):
        self.host.groups['kf-reviewers']['members'] = ['someone-else']
        with self.assertRaises(installer.DeploymentError):
            installer.deploy(self.host, self.files, self.pin, 'human')
        self.host.groups['kf-reviewers']['members'] = []
        self.host.accounts['other'] = dict(self.host.accounts['human'], gid=802)
        with self.assertRaises(installer.DeploymentError):
            installer.deploy(self.host, self.files, self.pin, 'human')
        self.assertEqual(self.host.calls, [])

    def test_existing_state_or_symlink_is_preserved(self):
        installer.STATE.mkdir()
        marker = installer.STATE / 'registry.json'
        marker.write_text('preserve-me')
        with self.assertRaises(installer.DeploymentError):
            installer.deploy(self.host, self.files, self.pin, 'human')
        self.assertEqual(marker.read_text(), 'preserve-me')
        self.assertEqual(self.host.calls, [])
        marker.unlink()
        installer.STATE.rmdir()
        installer.CODE.parent.mkdir(parents=True)
        installer.CODE.symlink_to(self.root / 'missing')
        with self.assertRaises(installer.DeploymentError):
            installer.deploy(self.host, self.files, self.pin, 'human')
        self.assertTrue(installer.CODE.is_symlink())

    def test_initialization_failure_never_enables_sockets(self):
        self.host.fail_at = 'initialize-state'
        with self.assertRaises(subprocess.CalledProcessError):
            installer.deploy(self.host, self.files, self.pin, 'human', enable=True)
        self.assertFalse(any('enable' in call for call in self.host.calls))
        self.assertTrue(installer.CODE.exists())  # Partial installation preserved for inspection.

    def test_preexisting_loaded_unit_refuses_before_mutation(self):
        self.host.loaded_unit = installer.UNITS[0]
        mutations = []
        with self.assertRaisesRegex(installer.DeploymentError, 'Systemd already knows'):
            installer.deploy(self.host, self.files, self.pin, 'human', enable=True,
                             on_mutation=lambda: mutations.append(True))
        self.assertEqual(self.host.calls, [['/usr/bin/systemctl', 'show', '--property=Version', '--value']])
        self.assertFalse(installer.CODE.exists())
        self.assertEqual(mutations, [])

    def test_systemd_adapter_queries_instance_for_template_without_starting_it(self):
        host = installer.LinuxHost()
        for requested, expected in [('knowledge-forge-operator@.service',
                                      'knowledge-forge-operator@installer-check.service'),
                                     ('knowledge-forge-operator.socket', 'knowledge-forge-operator.socket')]:
            with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    [], 0, stdout='not-found\n', stderr='')) as run:
                self.assertEqual(host.unit_load_state(requested), 'not-found')
                self.assertEqual(run.call_args.args[0], ['/usr/bin/systemctl', 'show',
                                 '--property=LoadState', '--value', expected])
        with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                [], 0, stdout='loaded\n', stderr='')):
            self.assertEqual(host.unit_load_state(installer.UNITS[0]), 'loaded')

    def test_systemd_adapter_refuses_bus_errors_and_unknown_output(self):
        for code, stdout, stderr in [(1, '', 'Failed to connect to bus'),
                                     (0, '', ''), (0, 'unexpected-state\n', ''),
                                     (1, 'not-found\n', 'Failed to connect to bus')]:
            with self.subTest(code=code, stdout=stdout):
                with patch.object(installer.subprocess, 'run', return_value=subprocess.CompletedProcess(
                        [], code, stdout=stdout, stderr=stderr)):
                    with self.assertRaises(installer.DeploymentError):
                        installer.LinuxHost().unit_load_state(installer.UNITS[0])

    def test_cli_read_only_systemd_failure_reports_no_partial_changes(self):
        output, errors = io.StringIO(), io.StringIO()
        self.host.unit_load_state = lambda name: (_ for _ in ()).throw(installer.DeploymentError('systemd read failed'))
        with patch.object(installer, 'pinned_files', return_value=self.files), \
             patch.object(installer, 'LinuxHost', return_value=self.host), \
             patch.object(installer, 'preflight', return_value=self.host.account('human')), \
             patch.object(installer.os, 'geteuid', return_value=0), \
             redirect_stdout(output), redirect_stderr(errors), \
             patch.object(installer.sys.stdin, 'isatty', return_value=True), \
             patch.object(output, 'isatty', return_value=True), \
             patch('builtins.input', return_value='DEPLOY PROTECTED FORGE'):
            code = installer.main(['--source-root', str(REPO), '--reviewer', 'human', '--commit', self.pin])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(errors.getvalue())['partial_changes_possible'])
        self.assertFalse(installer.CODE.exists())
        self.assertEqual(self.host.calls, [['/usr/bin/systemctl', 'show', '--property=Version', '--value']])

    def test_write_new_refuses_overwrite(self):
        path = self.root / 'file'
        installer.write_new(path, b'original')
        with self.assertRaises(FileExistsError):
            installer.write_new(path, b'replacement')
        self.assertEqual(path.read_bytes(), b'original')

    def test_ancestry_refuses_symlinks_writable_and_nonroot_directories(self):
        for bad in (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_uid=0),
                    SimpleNamespace(st_mode=stat.S_IFDIR | 0o775, st_uid=0),
                    SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=1000)):
            def metadata(path):
                return bad if path == Path('/opt') else SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0)
            with patch.object(Path, 'lstat', metadata):
                with self.assertRaises(installer.DeploymentError):
                    self.ancestry_check(Path('/opt/test-code'))

    def test_plan_is_read_only_and_cancellation_never_deploys(self):
        with patch.object(installer, 'pinned_files', return_value=self.files), \
             patch.object(installer, 'preflight') as preflight, \
             patch.object(installer, 'deploy') as deploy, \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            args = ['--source-root', str(REPO), '--reviewer', 'human', '--commit', self.pin]
            self.assertEqual(installer.main(args + ['--plan']), 0)
            preflight.assert_called_once()
            self.assertFalse(preflight.call_args.kwargs['mutation'])
            deploy.assert_not_called()
            with patch.object(installer.os, 'geteuid', return_value=0), \
                 patch.object(installer.sys.stdin, 'isatty', return_value=True), \
                 patch.object(installer.sys.stdout, 'isatty', return_value=True), \
                 patch('builtins.input', return_value='no'):
                self.assertEqual(installer.main(args), 1)
                deploy.assert_not_called()

    def test_cli_requires_root_and_tty_before_snapshot(self):
        with patch.object(installer.os, 'geteuid', return_value=1000), \
             patch.object(installer, 'pinned_files') as snapshot, redirect_stderr(io.StringIO()):
            code = installer.main(['--source-root', str(REPO), '--reviewer', 'human', '--commit', self.pin])
            self.assertEqual(code, 2)
            snapshot.assert_not_called()

    def test_git_snapshot_is_pinned_and_refuses_modified_helper(self):
        root = self.root / 'source'
        root.mkdir()
        files = dict(self.files)
        for name in ('protected_operator_cli.py', 'generic_approval_reviewer.py',
                     'generic_approval_client.py', 'generic_approval_mcp.py'):
            files['src/knowledge_forge/' + name] = b'# synthetic fixture\n'
        files['deploy/linux-protected-operator/operator.json.example'] = b'{}\n'
        for name in ('scripts/install_protected.py', 'install.sh'):
            files[name] = (REPO / name).read_bytes()
        for name, payload in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        def git(*args):
            return subprocess.check_output(['/usr/bin/git', '-C', str(root), *args],
                                           env=installer.ENV, stderr=subprocess.DEVNULL)
        git('init')
        git('add', '.')
        git('-c', 'user.name=Synthetic Test', '-c', 'user.email=test@example.invalid',
            '-c', 'commit.gpgsign=false', 'commit', '-m', 'synthetic deployment')
        pin = git('rev-parse', 'HEAD').decode().strip()
        self.assertEqual(installer.pinned_files(root, pin), files)
        # Reproduce sudo's different-owner refusal without root/chown or changing
        # any real account. Git's own test switch makes this repo "foreign".
        foreign_env = dict(installer.ENV, GIT_TEST_ASSUME_DIFFERENT_OWNER='1')
        before = (root / '.git/config').read_bytes()
        refused = subprocess.run(['/usr/bin/git', '-C', str(root), 'rev-parse', '--verify', pin],
                                 env=foreign_env, capture_output=True, text=True)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('dubious ownership', refused.stderr)
        with patch.object(installer, 'ENV', foreign_env), \
             patch.object(installer.subprocess, 'check_output', wraps=subprocess.check_output) as calls:
            self.assertEqual(installer.pinned_files(root, pin), files)
            for call in calls.call_args_list:
                command = call.args[0]
                self.assertIn('safe.directory=' + str(root.resolve()), command)
                self.assertNotIn('safe.directory=*', command)
                self.assertNotIn('--global', command)
        self.assertEqual((root / '.git/config').read_bytes(), before)
        (root / 'scripts/install_protected.py').write_text('# changed\n')
        with self.assertRaisesRegex(installer.DeploymentError, 'differs'):
            installer.pinned_files(root, pin)
        with self.assertRaisesRegex(installer.DeploymentError, '40-character'):
            installer.pinned_files(root, 'HEAD')


if __name__ == '__main__':
    unittest.main()
