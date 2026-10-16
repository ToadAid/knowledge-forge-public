"""Synthetic installer tests: no real account or group mutation is invoked."""
import os
from pathlib import Path
import select
import subprocess
import tempfile
import unittest

try:
    import pty
except ImportError:
    pty = None


REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and pty is not None, 'Bash installer is POSIX-only')
class InstallShellTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        script = (REPO / 'install.sh').read_text()
        # Simulate the effective UID only in the disposable test copy. All OS
        # commands below are Bash functions; none can create real identities.
        (self.root / 'install.sh').write_text(script.replace('$EUID', '${FORGE_TEST_EUID}'))
        (self.root / 'bin').mkdir()
        self.log = self.root / 'calls'
        for name in ('knowledge-forge-install-agent', 'knowledge-forge-setup'):
            path = self.root / 'bin' / name
            path.write_text('#!/bin/bash\nprintf "%s\\n" "$0 $*" >> "$FORGE_TEST_LOG"\nexit "${FORGE_TEST_INSTALL_RC:-0}"\n')
            path.chmod(0o755)
        self.env = dict(os.environ, FORGE_TEST_EUID='1000', FORGE_TEST_LOG=str(self.log))
        self.env.pop('BASH_ENV', None)
        self.harness = r'''
getent() {
  if [[ "${FORGE_TEST_LOOKUP_ERROR:-}" != '' ]]; then return 3; fi
  if [[ "${FORGE_TEST_EXISTING:-}" == "$1:$2" ]]; then return 0; fi
  return 2
}
groupadd() {
  printf 'groupadd %s\n' "$*" >> "$FORGE_TEST_LOG"
  [[ "${FORGE_TEST_FAIL_GROUP:-}" != "${*: -1}" ]]
}
useradd() { printf 'useradd %s\n' "$*" >> "$FORGE_TEST_LOG"; }
id() { if [[ "$2" == kf-agent ]]; then echo 995; else echo 994; fi; }
uname() { echo "${FORGE_TEST_PLATFORM:-Linux}"; }
source "$0" "$@"
'''

    def run_script(self, *args, **env):
        return subprocess.run(['bash', '-c', self.harness, str(self.root / 'install.sh'), *args],
                              env=dict(self.env, **env), capture_output=True, text=True)

    def calls(self):
        return self.log.read_text() if self.log.exists() else ''

    def test_plan_has_no_mutations(self):
        result = self.run_script('--create-protected-accounts', '--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--password '!'", result.stdout)
        self.assertIn('nologin', result.stdout)
        self.assertIn("--groups ''", result.stdout)
        self.assertEqual(self.calls(), '')

    def test_existing_names_and_failed_lookup_refuse_without_changes(self):
        for database in ('passwd', 'group'):
            for name in ('kf-agent', 'kf-operator', 'kf-reviewers'):
                result = self.run_script('--create-protected-accounts', '--plan',
                                         FORGE_TEST_EXISTING=f'{database}:{name}')
                self.assertEqual(result.returncode, 2)
        result = self.run_script('--create-protected-accounts', '--plan', FORGE_TEST_LOOKUP_ERROR='1')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Cannot establish', result.stderr)
        self.assertEqual(self.calls(), '')

    def test_privilege_platform_and_noninteractive_guards(self):
        result = self.run_script('--create-protected-accounts')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Administrator privileges', result.stderr)
        result = self.run_script('--create-protected-accounts', FORGE_TEST_EUID='0')
        self.assertEqual(result.returncode, 2)
        self.assertIn('interactive', result.stderr)
        result = self.run_script('--create-protected-accounts', '--plan', FORGE_TEST_PLATFORM='Darwin')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), '')

    def test_default_delegates_exact_arguments_then_checks_setup(self):
        result = self.run_script('--check-only', '--prefix', str(self.root / 'private prefix'))
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls().splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn('--check-only --prefix ' + str(self.root / 'private prefix'), calls[0])
        self.assertTrue(calls[1].endswith('knowledge-forge-setup --check-only'))

    def test_default_refuses_root_and_stops_on_installer_failure(self):
        self.assertEqual(self.run_script(FORGE_TEST_EUID='0').returncode, 2)
        self.assertEqual(self.calls(), '')
        self.assertEqual(self.run_script(FORGE_TEST_INSTALL_RC='7').returncode, 7)
        self.assertEqual(len(self.calls().splitlines()), 1)

    def test_unexpected_account_arguments_refuse(self):
        self.assertEqual(self.run_script('--create-protected-accounts', '--yes').returncode, 2)
        self.assertEqual(self.calls(), '')

    @unittest.skipUnless(os.path.isfile('/usr/bin/python3'), 'System Python launcher')
    def test_deployment_dispatches_to_isolated_system_python(self):
        scripts = self.root / 'scripts'
        scripts.mkdir()
        (scripts / 'install_protected.py').write_text('import json,sys; print(json.dumps(sys.argv[1:]))\n')
        result = self.run_script('--deploy-protected', '--reviewer', 'human', '--commit', 'a' * 40, '--plan')
        self.assertEqual(result.returncode, 0, result.stderr)
        import json
        self.assertEqual(json.loads(result.stdout), ['--source-root', str(self.root), '--reviewer',
                                                     'human', '--commit', 'a' * 40, '--plan'])
        self.assertEqual(self.calls(), '')

    def interactive(self, response, **env):
        master, slave = pty.openpty()
        process = subprocess.Popen(['bash', '-c', self.harness, str(self.root / 'install.sh'),
                                    '--create-protected-accounts'],
                                   env=dict(self.env, FORGE_TEST_EUID='0', **env),
                                   stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b''
        try:
            # Bounded wait: send the response only after the confirmation prompt.
            for _ in range(100):
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    output += os.read(master, 65536)
                if b'Type CREATE FORGE ACCOUNTS to proceed:' in output:
                    break
            else:
                self.fail('No confirmation prompt')
            os.write(master, (response + '\n').encode())
            process.wait(timeout=5)
            while select.select([master], [], [], 0)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
            return process.returncode, output.decode()
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            os.close(master)

    def test_cancel_does_not_mutate(self):
        code, output = self.interactive('no')
        self.assertEqual(code, 1, output)
        self.assertEqual(self.calls(), '')

    def test_confirmation_runs_only_expected_account_commands(self):
        code, output = self.interactive('CREATE FORGE ACCOUNTS')
        self.assertEqual(code, 0, output)
        calls = self.calls().splitlines()
        self.assertEqual(calls[:3], ['groupadd --system kf-agent',
                                    'groupadd --system kf-operator',
                                    'groupadd --system kf-reviewers'])
        self.assertEqual(len(calls), 5)
        for call in calls[3:]:
            self.assertIn('--password !', call)
            self.assertIn('nologin', call)
            self.assertIn('--no-create-home', call)
        self.assertIn('PROTECTED SETUP INCOMPLETE', output)

    def test_partial_failure_stops_with_recovery_message(self):
        code, output = self.interactive('CREATE FORGE ACCOUNTS', FORGE_TEST_FAIL_GROUP='kf-operator')
        self.assertNotEqual(code, 0)
        self.assertEqual(len(self.calls().splitlines()), 2)
        self.assertIn('no automatic rollback', output)


if __name__ == '__main__':
    unittest.main()
