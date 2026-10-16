from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge import agent_setup as setup

REPO = Path(__file__).resolve().parents[1]


class AgentSetupTests(unittest.TestCase):
    def linux_report(self, *, agent=None, operator=None, paths_present=False):
        def metadata(path):
            key = next(k for k, v in setup.PROTECTED_PATHS.items() if v == path)
            kind = {'code_root': 'directory', 'state_root': 'directory',
                    'configuration': 'file', 'agent_request_socket': 'socket', 'reviewer_socket': 'socket'}[key]
            return {'path': str(path), 'exists': paths_present, 'kind': kind}
        with patch.object(setup.sys, 'platform', 'linux'), \
             patch.object(setup, '_account', side_effect=lambda name: agent if name == 'kf-agent' else operator), \
             patch.object(setup, '_path_metadata', side_effect=metadata), \
             patch.object(setup.os, 'getuid', return_value=1000, create=True):
            return setup.setup_report()

    def test_missing_accounts_offer_setup_without_claiming_readiness(self):
        result = self.linux_report()
        self.assertEqual(result['status'], 'ADMIN_SETUP_REQUIRED')
        self.assertIn('Missing account: kf-agent', result['blockers'])
        self.assertIn('Missing account: kf-operator', result['blockers'])
        self.assertIn('kf-agent', result['setup_question'])
        self.assertIn('kf-operator', result['setup_question'])
        self.assertIn('human reviewer', result['setup_question'])
        for key in ('protected_deployment_verified', 'accounts_created', 'services_enabled',
                    'agent_settings_changed', 'promotion_performed', 'personal_forge_requires_service'):
            self.assertIs(result[key], False)

    def test_existing_distinct_accounts_do_not_certify_deployment(self):
        result = self.linux_report(agent={'uid': 2001, 'groups': ['kf-agent']},
            operator={'uid': 2002, 'groups': ['kf-operator']}, paths_present=True)
        self.assertEqual(result['status'], 'INDEPENDENT_VERIFICATION_REQUIRED')
        self.assertFalse(result['protected_deployment_verified'])
        self.assertFalse(result['current_process_is_agent_uid'])

    def test_shared_or_root_uids_are_blockers(self):
        for agent_uid, operator_uid in ((0, 2002), (2001, 0), (2001, 2001)):
            result = self.linux_report(agent={'uid': agent_uid, 'groups': []},
                operator={'uid': operator_uid, 'groups': []}, paths_present=True)
            self.assertEqual(result['status'], 'ADMIN_SETUP_REQUIRED')
            self.assertTrue(any('UIDs' in item or 'non-root' in item for item in result['blockers']))

    def test_known_privileged_and_reviewer_groups_are_reported(self):
        for group in ('sudo', 'wheel', 'admin', 'docker', 'lxd', 'kf-reviewers'):
            result = self.linux_report(agent={'uid': 2001, 'groups': [group]},
                operator={'uid': 2002, 'groups': []}, paths_present=True)
            self.assertTrue(any(group in item for item in result['blockers']))
        result = self.linux_report(agent={'uid': 2001, 'groups': []},
            operator={'uid': 2002, 'groups': ['kf-reviewers']}, paths_present=True)
        self.assertIn('kf-operator must not belong to kf-reviewers', result['blockers'])

    def test_other_platforms_do_not_probe_linux_accounts_or_offer_account_creation(self):
        for platform in ('win32', 'darwin'):
            with patch.object(setup.sys, 'platform', platform), \
                 patch.object(setup, '_account') as account, patch.object(setup, '_path_metadata') as metadata:
                result = setup.setup_report()
            account.assert_not_called()
            metadata.assert_not_called()
            self.assertEqual(result['status'], 'PROTECTED_SETUP_UNSUPPORTED_ON_THIS_PLATFORM')
            self.assertIsNone(result['setup_question'])
            self.assertFalse(result['protected_deployment_verified'])

    def test_metadata_reports_symlinks_without_following_or_reading_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'missing'
            self.assertFalse(setup._path_metadata(path)['exists'])
            data = Path(tmp) / 'data'
            data.write_text('not read by helper')
            with patch.object(Path, 'read_text', side_effect=AssertionError('must not read content')):
                self.assertEqual(setup._path_metadata(data)['kind'], 'file')
            try:
                path.symlink_to(data)
            except (OSError, NotImplementedError):
                return  # Native Windows can lack symlink creation rights.
            self.assertEqual(setup._path_metadata(path)['kind'], 'symlink')

    def test_cli_defaults_to_read_only_and_has_no_install_or_accept_flag(self):
        for args in ([], ['--check-only']):
            out = StringIO()
            with patch.object(setup, 'setup_report', return_value={'status': 'ADMIN_SETUP_REQUIRED'}), redirect_stdout(out):
                self.assertEqual(setup.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())['status'], 'ADMIN_SETUP_REQUIRED')
        for arg in ('--install', '--accept', '--create-accounts', '--enable-services'):
            with patch('sys.stderr', StringIO()), self.assertRaises(SystemExit):
                setup.main([arg])

    def test_fresh_checkout_launcher_writes_no_bytecode_or_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'bin').mkdir()
            package = root / 'src/knowledge_forge'
            package.mkdir(parents=True)
            (package / '__init__.py').write_text('')
            shutil.copyfile(REPO / 'bin/knowledge-forge-setup', root / 'bin/knowledge-forge-setup')
            shutil.copyfile(REPO / 'src/knowledge_forge/agent_setup.py', package / 'agent_setup.py')
            def snapshot():
                return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            before = snapshot()
            # Deliberately do not use -B: the launcher itself must disable caches.
            env = dict(os.environ)
            env.pop('PYTHONDONTWRITEBYTECODE', None)
            result = subprocess.run([sys.executable, str(root / 'bin/knowledge-forge-setup'), '--check-only'],
                capture_output=True, text=True, env=env, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['operation'], 'READ_ONLY_AGENT_SETUP')
            self.assertEqual(before, snapshot())
            self.assertEqual(list(root.rglob('__pycache__')), [])


if __name__ == '__main__':
    unittest.main()
