"""P4-P1-P1-P1: inert sandbox command inspection and blocked-path tests."""
from __future__ import annotations

from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from _platform_support import skip_linux_proc_namespaces_on_windows

from knowledge_forge.portable_isolation_preflight import (
    IsolationPreflightError, preflight, sandbox_command,
)


class IsolationPreflightTests(unittest.TestCase):
    def test_command_unshares_network_and_binds_no_private_paths(self):
        command = sandbox_command('/tmp/kf-native-isolation-EXAMPLE/private-user-canary')
        self.assertEqual(command[0], '/usr/bin/bwrap')
        for flag in ('--unshare-all', '--die-with-parent', '--new-session', '--clearenv', '--tmpfs'):
            self.assertIn(flag, command)
        self.assertIn('/usr/bin/python3', command)
        self.assertIn('-I', command)
        self.assertNotIn('--share-net', command)
        self.assertNotIn('--bind', command)
        self.assertNotIn('--ro-bind-try', command)
        self.assertNotIn('/home/tommy', command)
        self.assertNotIn('/etc', command)
        self.assertNotIn('codex', ' '.join(command).lower())
        self.assertNotIn('claude', ' '.join(command).lower())

    def test_disallowed_command_arguments_refuse(self):
        with self.assertRaises(IsolationPreflightError):
            sandbox_command('/home/tommy/private')
        with self.assertRaises(IsolationPreflightError):
            sandbox_command('/tmp/kf-native-isolation-x/c', bubblewrap='/bin/sh')

    def test_plan_never_executes_program_or_hooks(self):
        with patch('knowledge_forge.portable_isolation_preflight.subprocess.run', side_effect=AssertionError('invoked')):
            for _ in range(2):
                result = preflight()
                self.assertEqual(result['state'], 'PLANNED_NOT_EXECUTED')
                self.assertFalse(result['native_dispatch_verified'])
                self.assertFalse(result['safe_to_activate_hooks'])
                self.assertFalse(result['host_session_started'])

    def test_missing_bubblewrap_blocks_no_subprocess(self):
        original = Path.is_file
        def fake(path):
            if str(path) == '/usr/bin/bwrap':
                return False
            return original(path)
        with patch.object(Path, 'is_file', fake):
            with patch('knowledge_forge.portable_isolation_preflight.subprocess.run', side_effect=AssertionError('invoked')):
                result = preflight(execute_probe=True)
        self.assertEqual(result['state'], 'BLOCKED_ISOLATION_NOT_PROVEN')
        self.assertFalse(result['network_namespace_verified'])

    @skip_linux_proc_namespaces_on_windows
    def test_positive_synthetic_result_cannot_attest_host(self):
        # Synthetic runner result DOES NOT count as native dispatch verification.
        import os
        record = {'net_namespace_inode': os.stat('/proc/self/ns/net').st_ino + 1024,
                  'interfaces': ['lo'], 'canary_visible': False,
                  'home_is_tmp': True, 'python_isolated_mode': True}
        cp = subprocess.CompletedProcess(['bwrap'], 0, stdout=json.dumps(record).encode(), stderr=b'')
        with patch.object(Path, 'is_file', return_value=True):
            with patch('knowledge_forge.portable_isolation_preflight.subprocess.run', return_value=cp) as called:
                result = preflight(execute_probe=True)
        self.assertEqual(result['state'], 'PROBE_NAMESPACE_PASS_NOT_HOST_ATTESTATION')
        self.assertFalse(result['native_dispatch_verified'])
        self.assertFalse(result['safe_to_activate_hooks'])
        self.assertEqual(called.call_args.kwargs['env'], {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'})

    @skip_linux_proc_namespaces_on_windows
    def test_fake_shared_network_rejected(self):
        import os
        record = {'net_namespace_inode': os.stat('/proc/self/ns/net').st_ino,
                  'interfaces': ['eth0'], 'canary_visible': False,
                  'home_is_tmp': True, 'python_isolated_mode': True}
        cp = subprocess.CompletedProcess([], 0, stdout=json.dumps(record).encode(), stderr=b'')
        with patch.object(Path, 'is_file', return_value=True):
            with patch('knowledge_forge.portable_isolation_preflight.subprocess.run', return_value=cp):
                result = preflight(execute_probe=True)
        self.assertEqual(result['state'], 'BLOCKED_ISOLATION_NOT_PROVEN')
        self.assertFalse(result['network_namespace_verified'])

    def test_cli_no_host_session(self):
        done = subprocess.run([sys.executable, '-m',
                               'knowledge_forge.portable_isolation_preflight',
                               '--host', 'codex-cli'], capture_output=True, timeout=10)
        self.assertEqual(done.returncode, 0, done.stderr)
        result = json.loads(done.stdout)
        self.assertEqual(result['state'], 'PLANNED_NOT_EXECUTED')
        self.assertFalse(result['native_dispatch_verified'])
        self.assertFalse(result['hook_activated'])


if __name__ == '__main__':
    unittest.main()
