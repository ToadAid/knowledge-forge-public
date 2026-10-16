"""P4-P1-P1-P3-P1: bounded static executable inventory and no-launch law."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from _platform_support import skip_posix_executable_fixture_on_windows, skip_posix_path_labels_on_windows, skip_unless_symlinks

from knowledge_forge.portable_native_executable_inventory import (
    MAX_BYTES, NativeExecutableInventoryError, _display, inventory, main,
)


class NativeExecutableInventoryTests(unittest.TestCase):
    @staticmethod
    def _make_candidate(directory: Path, command: str, contents: bytes = b'#!/usr/bin/env node\n// inert\n') -> Path:
        file = directory / command
        file.write_bytes(contents)
        file.chmod(0o700)
        return file

    @skip_posix_executable_fixture_on_windows
    def test_both_hosts_passive_identity_and_hash(self):
        import hashlib
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            for host, name in [('claude-code', 'claude'), ('codex-cli', 'codex')]:
                code = b'#!/usr/bin/env node\n// synthetic no-exec\n'
                file = self._make_candidate(root, name, code)
                result = inventory(host, search_path=scratch, home=scratch)
                self.assertEqual(result['state'], 'PASSIVE_FILE_IDENTITY_ONLY')
                self.assertEqual(result['resolved_executable_path'], '$HOME/' + name)
                self.assertEqual(result['executable_file_identity']['sha256'], hashlib.sha256(code).hexdigest())
                self.assertEqual(result['executable_file_identity']['bytes'], len(code))
                self.assertEqual(result['executable_file_identity']['runtime_dependency_hint']['interpreter_basename_hint'], 'env')
                self.assertFalse(result['native_host_sessions_started'])
                self.assertFalse(result['native_launch_authorized'])
                self.assertFalse(result['runtime_dependency_tree_verified'])
                self.assertFalse(result['native_host_version_verified'])
                self.assertEqual(result['tool_grants'], [])
                self.assertTrue(file.exists())

    @skip_posix_executable_fixture_on_windows
    def test_symlink_chain_is_bounded_and_read_only(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            real = self._make_candidate(root, 'real-binary')
            (root / 'claude').symlink_to('link-a')
            (root / 'link-a').symlink_to(real.name)
            result = inventory('claude-code', search_path=scratch, home=scratch)
            self.assertEqual(result['state'], 'PASSIVE_FILE_IDENTITY_ONLY')
            self.assertEqual(len(result['symlink_chain']), 2)
            self.assertEqual(result['resolved_executable_path'], '$HOME/real-binary')

    @skip_unless_symlinks
    def test_symlink_cycle_refused(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / 'claude').symlink_to('node-a')
            (root / 'node-a').symlink_to('claude')
            # shutil.which refuses a broken/cyclic executable itself; either way no run.
            self.assertEqual(inventory('claude-code', search_path=scratch)['state'], 'UNRESOLVED_NO_EXECUTION')

    def test_missing_executable_is_no_exec_not_a_success(self):
        with TemporaryDirectory() as scratch:
            for host in ('claude-code', 'codex-cli'):
                result = inventory(host, search_path=scratch)
                self.assertEqual(result['state'], 'UNRESOLVED_NO_EXECUTION')
                self.assertFalse(result['native_launch_authorized'])
                self.assertNotIn('executable_file_identity', result)

    @skip_posix_executable_fixture_on_windows
    def test_size_cap_and_non_executable_file_refuse(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            file = self._make_candidate(root, 'claude', b'X' * 40)
            self.assertEqual(inventory('claude-code', search_path=scratch, max_bytes=8)['state'], 'BLOCKED_CANDIDATE_NOT_PROVEN')
            file.chmod(0o600)
            self.assertEqual(inventory('claude-code', search_path=scratch)['state'], 'UNRESOLVED_NO_EXECUTION')

    @skip_posix_executable_fixture_on_windows
    def test_oversized_candidate_classified_without_hash_or_path(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            self._make_candidate(root, 'codex', b'not-code' * 20)
            result = inventory('codex-cli', search_path=scratch, max_bytes=8)
            self.assertEqual(result['state'], 'BLOCKED_CANDIDATE_NOT_PROVEN')
            self.assertEqual(result['refusal_category'], 'FILE_EXCEEDS_INVENTORY_LIMIT')
            self.assertNotIn('executable_file_identity', result)
            self.assertNotIn('resolved_executable_path', result)
            self.assertFalse(result['native_launch_authorized'])

    @skip_posix_executable_fixture_on_windows
    def test_empty_candidate_classified_no_native_execution(self):
        with TemporaryDirectory() as scratch:
            self._make_candidate(Path(scratch), 'codex', b'')
            result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['state'], 'BLOCKED_CANDIDATE_NOT_PROVEN')
            self.assertEqual(result['refusal_category'], 'EMPTY_FILE')

    @skip_unless_symlinks
    def test_symlink_cycle_category_without_path_disclosure(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            first = root / 'codex'
            first.symlink_to('next')
            (root / 'next').symlink_to('codex')
            with patch('knowledge_forge.portable_native_executable_inventory.shutil.which', return_value=str(first)):
                result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['state'], 'BLOCKED_CANDIDATE_NOT_PROVEN')
            self.assertEqual(result['refusal_category'], 'SYMLINK_CYCLE')
            self.assertNotIn(scratch, json.dumps(result))

    @skip_unless_symlinks
    def test_symlink_depth_category(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            for i in range(18):
                (root / ('link-' + str(i))).symlink_to('link-' + str(i+1))
            (root / 'link-18').symlink_to('codex')
            start = root / 'link-0'
            with patch('knowledge_forge.portable_native_executable_inventory.shutil.which', return_value=str(start)):
                result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['refusal_category'], 'SYMLINK_DEPTH_LIMIT')

    @skip_posix_executable_fixture_on_windows
    def test_unavailable_file_and_link_are_distinct_safe_categories(self):
        from knowledge_forge import portable_native_executable_inventory as mod
        with TemporaryDirectory() as scratch:
            found = self._make_candidate(Path(scratch), 'codex')
            with patch.object(mod, '_bounded_digest', side_effect=PermissionError('/private/secret')):
                result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['refusal_category'], 'FILE_OPEN_READ_OR_STAT_UNAVAILABLE')
            with patch.object(mod, '_links', side_effect=PermissionError('/private/secret')):
                result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['refusal_category'], 'SYMLINK_METADATA_UNAVAILABLE')
            self.assertNotIn('/private/secret', json.dumps(result))
            self.assertTrue(found.exists())

    @skip_posix_executable_fixture_on_windows
    def test_changed_during_digest_distinct_and_not_success(self):
        from knowledge_forge import portable_native_executable_inventory as mod
        with TemporaryDirectory() as scratch:
            self._make_candidate(Path(scratch), 'codex')
            with patch.object(mod, '_bounded_digest', side_effect=NativeExecutableInventoryError('candidate identity changed during read')):
                result = inventory('codex-cli', search_path=scratch)
            self.assertEqual(result['refusal_category'], 'IDENTITY_CHANGED_DURING_READ')
            self.assertFalse(result['native_launch_authorized'])
            self.assertNotIn('executable_file_identity', result)

    def test_unknown_host_and_bad_limits_refused(self):
        for name in ('', 'unknown', 'claude --version', None, True):
            with self.subTest(name=str(name)), self.assertRaises(NativeExecutableInventoryError):
                inventory(name)
        for bad in (0, MAX_BYTES+1, True, '64'):
            with self.assertRaises(NativeExecutableInventoryError):
                inventory('claude-code', max_bytes=bad)

    def test_no_spawn_ever_in_inventory_main(self):
        with patch('subprocess.run', side_effect=AssertionError('spawn forbidden')):
            with patch('builtins.print') as captured:
                self.assertEqual(main(['--host','claude-code','--mode','INVENTORY_ONLY']), 0)
            result = json.loads(captured.call_args.args[0])
            self.assertFalse(result['native_dispatch_verified'])
            self.assertFalse(result['safe_to_activate_hooks'])

    def test_executable_not_read_as_user_settings(self):
        # Only candidate bytes and its direct symlinks may be accessed.
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            self._make_candidate(root, 'codex')
            secret = root / 'credentials.json'
            secret.write_text('TOP_SECRET_CANARY', encoding='utf-8')
            with patch('subprocess.run', side_effect=AssertionError('spawn forbidden')):
                result = inventory('codex-cli', search_path=scratch)
            self.assertNotIn('TOP_SECRET_CANARY', json.dumps(result))
            self.assertEqual(secret.read_text(encoding='utf-8'), 'TOP_SECRET_CANARY')

    def test_cli_rejects_execution_without_stdout(self):
        for host, mode in [('claude-code','ON'),('codex-cli','NATIVE_TEST'),
                           ('unknown','INVENTORY_ONLY')]:
            done = subprocess.run([sys.executable,'-m','knowledge_forge.portable_native_executable_inventory',
                                  '--host',host,'--mode',mode],capture_output=True,timeout=8)
            self.assertEqual(done.returncode, 2)
            self.assertEqual(done.stdout, b'')

    @skip_posix_path_labels_on_windows
    def test_path_redaction_is_boundary_aware(self):
        self.assertEqual(_display('/home/example/a', '/home/example'), '$HOME/a')
        self.assertEqual(_display('/home/example2/a', '/home/example'), '/home/example2/a')
        self.assertEqual(_display('/home/example', '/home/example'), '$HOME')


if __name__ == '__main__':
    unittest.main()
