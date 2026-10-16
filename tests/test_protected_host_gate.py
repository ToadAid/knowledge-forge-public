"""P3B-P2A: OS separation diagnostic refuses spoofable layouts."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
import os
from pathlib import Path
import stat
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('protected_host_gate')

from knowledge_forge.protected_host_gate import (
    ProtectedHostGateError, ProtectedHostLayout,
    inspect_prospective_operator_boundary,
)


class ProtectedHostGateTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory()
        self.addCleanup(self.sandbox.cleanup)
        root = Path(self.sandbox.name)
        (root / 'code').mkdir()
        (root / 'state').mkdir()
        self.code = root / 'code'
        self.state = root / 'state'
        self.layout = ProtectedHostLayout('agent', 'kf-operator', str(self.code), str(self.state))

    @staticmethod
    def _uid_name(name: str):
        return SimpleNamespace(pw_uid={'agent': 1001, 'kf-operator': 1002, 'root': 0}[name])

    def _inside_mocked_host(self, *, euid=1002, owner_override=None, mode_override=None):
        """Synthetic ownership only; cannot prove real TNG account separation."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch('knowledge_forge.protected_host_gate.pwd.getpwnam', side_effect=self._uid_name))
        stack.enter_context(patch('knowledge_forge.protected_host_gate.os.geteuid', return_value=euid))
        orig = Path.lstat
        code = self.code
        state = self.state
        def fake_lstat(path, *args, **kwargs):
            actual = orig(path, *args, **kwargs)
            p = Path(path)
            uid = 0 if p == code else 1002 if p == state else 0
            if owner_override and p in owner_override:
                uid = owner_override[p]
            mode = actual.st_mode
            if p == state:
                mode = (mode & ~0o777) | 0o700
            elif p == code:
                mode = (mode & ~0o777) | 0o755
            else:
                mode = (mode & ~0o777) | 0o755
            if mode_override and p in mode_override:
                mode = (mode & ~0o777) | mode_override[p]
            return SimpleNamespace(st_mode=mode, st_uid=uid)
        stack.enter_context(patch('knowledge_forge.protected_host_gate.Path.lstat', new=fake_lstat))

    def test_separated_layout_reports_diagnostics_only(self):
        self._inside_mocked_host()
        v = inspect_prospective_operator_boundary(self.layout)
        self.assertEqual(v['status'], 'OS_LAYOUT_PRECHECK_ONLY_NOT_AUTHENTICATION')
        self.assertTrue(v['distinct_uid_observed'])
        for key in ('host_authentication_verified', 'authorization_granted',
                    'promotion_authorized', 'one_time_decision_consumed',
                    'operator_cli_invoked', 'production_registry_mutated'):
            self.assertFalse(v[key])
        self.assertEqual(v['execution_authority'], 'NONE')
        self.assertEqual(v['tool_grants'], [])

    def test_same_uid_is_refused(self):
        with patch('knowledge_forge.protected_host_gate.pwd.getpwnam',
                   return_value=SimpleNamespace(pw_uid=1000)):
            with self.assertRaisesRegex(ProtectedHostGateError, 'same UID'):
                inspect_prospective_operator_boundary(self.layout)

    def test_wrong_euid_is_refused(self):
        self._inside_mocked_host(euid=1001)
        with self.assertRaisesRegex(ProtectedHostGateError, 'distinct operator UID'):
            inspect_prospective_operator_boundary(self.layout)

    def test_root_as_operator_is_refused(self):
        with patch('knowledge_forge.protected_host_gate.pwd.getpwnam',
                   side_effect=lambda name: SimpleNamespace(pw_uid=0 if name == 'kf-operator' else 1001)):
            with self.assertRaisesRegex(ProtectedHostGateError, 'root'):
                inspect_prospective_operator_boundary(self.layout)

    def test_nonexistent_account_is_refused(self):
        with patch('knowledge_forge.protected_host_gate.pwd.getpwnam', side_effect=KeyError('missing')):
            with self.assertRaisesRegex(ProtectedHostGateError, 'not provisioned'):
                inspect_prospective_operator_boundary(self.layout)

    def test_agent_owned_code_is_refused(self):
        self._inside_mocked_host(owner_override={self.code: 1001})
        with self.assertRaisesRegex(ProtectedHostGateError, 'not root/operator-owned'):
            inspect_prospective_operator_boundary(self.layout)

    def test_operator_owned_code_is_refused(self):
        self._inside_mocked_host(owner_override={self.code: 1002})
        with self.assertRaisesRegex(ProtectedHostGateError, 'root-owned'):
            inspect_prospective_operator_boundary(self.layout)

    def test_world_writable_ancestor_is_refused(self):
        self._inside_mocked_host(mode_override={self.code.parent: 0o777})
        with self.assertRaisesRegex(ProtectedHostGateError, 'writable'):
            inspect_prospective_operator_boundary(self.layout)

    def test_state_not_operator_owned_is_refused(self):
        self._inside_mocked_host(owner_override={self.state: 0})
        with self.assertRaisesRegex(ProtectedHostGateError, 'operator-owned'):
            inspect_prospective_operator_boundary(self.layout)

    def test_state_group_readable_is_refused(self):
        self._inside_mocked_host(mode_override={self.state: 0o750})
        with self.assertRaisesRegex(ProtectedHostGateError, 'mode 0700'):
            inspect_prospective_operator_boundary(self.layout)

    def test_symlink_ancestor_is_refused(self):
        other = self.code.parent / 'linked'
        other.symlink_to(self.code, target_is_directory=True)
        self._inside_mocked_host()
        with self.assertRaisesRegex(ProtectedHostGateError, 'non-directory or symlink'):
            inspect_prospective_operator_boundary(replace(self.layout, operator_code_root=str(other)))

    def test_paths_must_be_distinct_and_absolute(self):
        self._inside_mocked_host()
        for value in ('relative/path', '/tmp/../etc', '/'):
            with self.assertRaises(ProtectedHostGateError):
                inspect_prospective_operator_boundary(replace(self.layout, operator_code_root=value))
        with self.assertRaisesRegex(ProtectedHostGateError, 'separate'):
            inspect_prospective_operator_boundary(replace(self.layout, operator_state_root=str(self.code)))

    def test_caller_supplied_flags_cannot_be_accepted_as_layout(self):
        self._inside_mocked_host()
        with self.assertRaisesRegex(ProtectedHostGateError, 'ProtectedHostLayout'):
            inspect_prospective_operator_boundary({'verified_by_host': True})


if __name__ == '__main__':
    unittest.main()
