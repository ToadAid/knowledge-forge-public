"""P4-P1-P1-P3-P6: bounded P6 full-file digest and same-FD ELF hints, never exec."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from _platform_support import skip_posix_executable_fixture_on_windows

from knowledge_forge.portable_native_binary_dossier import (
    CHUNK_BYTES, LEGACY_HASH_LIMIT, P6_MAX_HASH_BYTES,
    BinaryDossierError, dossier, main,
)


class BinaryDossierTests(unittest.TestCase):
    @staticmethod
    def _make(root: Path, host='codex', content=b'\x7fELF' + bytes(60)) -> Path:
        dest = root / host
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        dest.chmod(0o700)
        return dest

    def test_distinct_p6_policy_does_not_modify_legacy_policy(self):
        self.assertEqual(LEGACY_HASH_LIMIT, 256 * 1024 * 1024)
        self.assertEqual(P6_MAX_HASH_BYTES, 320 * 1024 * 1024)
        self.assertGreater(P6_MAX_HASH_BYTES, 292309256)
        self.assertLessEqual(CHUNK_BYTES, 1024*1024)

    @skip_posix_executable_fixture_on_windows
    def test_both_hosts_digest_small_fixture_same_descriptor(self):
        for host, command in [('codex-cli','codex'),('claude-code','claude')]:
            with TemporaryDirectory() as directory:
                root = Path(directory)
                content = b'\x7fELF' + b'\x00' * 500
                self._make(root, command, content)
                got = dossier(host, search_path=directory, home=directory)
                self.assertEqual(got['state'], 'PASSIVE_P6_DIGEST_AND_ELF_SNAPSHOT_NOT_ATTESTATION')
                self.assertEqual(got['full_file_sha256_at_snapshot_only'], hashlib.sha256(content).hexdigest())
                self.assertEqual(got['full_file_bytes_read'], len(content))
                self.assertTrue(got['same_descriptor_elf_hints_snapshot_only'])
                self.assertTrue(got['path_components_stable_only_during_observation'])
                for key in ('immutable_executable_or_path_identity_verified','native_dependency_closure_verified',
                            'installer_provenance_or_signature_verified','effective_directory_write_access_verified',
                            'native_process_isolation_verified','native_launch_authorized','safe_to_activate_hooks'):
                    self.assertFalse(got[key], key)
                self.assertEqual(got['tool_grants'], [])

    @skip_posix_executable_fixture_on_windows
    def test_oversize_refuses_at_configured_bound_without_digest(self):
        with TemporaryDirectory() as directory:
            f = self._make(Path(directory))
            with f.open('r+b') as stream:
                stream.truncate(8192)
            r = dossier('codex-cli', search_path=directory, home=directory, max_hash_bytes=4096)
            self.assertEqual(r['state'], 'BLOCKED_P6_IDENTITY_NOT_PROVEN')
            self.assertEqual(r['refusal_category'], 'FILE_EXCEEDS_P6_SEPARATE_BOUND')
            self.assertNotIn('full_file_sha256_at_snapshot_only', r)
            self.assertFalse(r['native_launch_authorized'])

    def test_sparse_codex_sized_file_is_admissible_to_distinct_policy_not_hashed_here(self):
        # Only test the policy bound: CI must not hash a 292 MB synthetic file.
        with TemporaryDirectory() as directory:
            f = self._make(Path(directory))
            with f.open('r+b') as stream:
                stream.truncate(292309256)
            self.assertEqual(f.stat().st_size, 292309256)
            self.assertGreater(f.stat().st_size, LEGACY_HASH_LIMIT)
            self.assertLess(f.stat().st_size, P6_MAX_HASH_BYTES)

    @skip_posix_executable_fixture_on_windows
    def test_refuses_world_writable_candidate(self):
        with TemporaryDirectory() as directory:
            f = self._make(Path(directory))
            f.chmod(0o707)
            r = dossier('codex-cli', search_path=directory, home=directory)
            self.assertEqual(r['refusal_category'], 'UNSAFE_EXECUTABLE_MODE')
            self.assertFalse(r['full_file_hash_snapshot_only'])

    @skip_posix_executable_fixture_on_windows
    def test_direct_symlink_and_parent_group_mode_hints(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            actual = self._make(root, '.codex/packages/standalone/version-1/bin/codex')
            standalone = actual.parent.parent.parent
            standalone.mkdir(parents=True, exist_ok=True)
            standalone.chmod(0o775)
            (standalone/'current').symlink_to('version-1', target_is_directory=True)
            (root/'codex').symlink_to(standalone/'current/bin/codex')
            got = dossier('codex-cli', search_path=directory, home=directory)
            self.assertEqual(got['state'], 'PASSIVE_P6_DIGEST_AND_ELF_SNAPSHOT_NOT_ATTESTATION')
            self.assertIn('$HOME/.codex/packages/standalone/current', got['symlink_components_observed'])
            self.assertIn('$HOME/.codex/packages/standalone', got['directory_mode_group_or_world_write_bits_observed'])
            self.assertNotIn('$HOME/.codex/packages/standalone/current', got['directory_mode_group_or_world_write_bits_observed'])
            self.assertFalse(got['effective_directory_write_access_verified'])

    @skip_posix_executable_fixture_on_windows
    def test_symlink_swap_during_hash_is_refused(self):
        from knowledge_forge import portable_native_binary_dossier as mod
        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = self._make(root, 'one')
            second = self._make(root, 'two', b'\x7fELF' + bytes(120))
            (root/'codex').symlink_to(first.name)
            old = mod._full_hash
            def move(fd, size, limit):
                hashed = old(fd, size, limit)
                (root/'codex').unlink()
                (root/'codex').symlink_to(second.name)
                return hashed
            with patch.object(mod, '_full_hash', side_effect=move):
                result = dossier('codex-cli', search_path=directory, home=directory)
            self.assertEqual(result['state'], 'BLOCKED_P6_IDENTITY_NOT_PROVEN')
            self.assertEqual(result['refusal_category'], 'PATH_METADATA_CHANGED_DURING_OBSERVATION')
            self.assertNotIn('full_file_sha256_at_snapshot_only', result)

    @skip_posix_executable_fixture_on_windows
    def test_file_swap_during_hash_refused(self):
        from knowledge_forge import portable_native_binary_dossier as mod
        with TemporaryDirectory() as directory:
            root = Path(directory)
            binary = self._make(root)
            old = mod._full_hash
            def replace(fd, size, limit):
                hashed = old(fd, size, limit)
                binary.unlink()
                self._make(root, content=b'\x7fELF'+b'\x01'*100)
                return hashed
            with patch.object(mod, '_full_hash', side_effect=replace):
                result = dossier('codex-cli', search_path=directory, home=directory)
            self.assertEqual(result['refusal_category'], 'FILE_METADATA_CHANGED_DURING_OBSERVATION')

    @skip_posix_executable_fixture_on_windows
    def test_search_path_outside_home_refused_no_leak(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sub = root/'private'
            sub.mkdir()
            self._make(sub)
            r = dossier('codex-cli', search_path=str(sub), home=str(root/'another'))
            self.assertEqual(r['state'], 'BLOCKED_P6_IDENTITY_NOT_PROVEN')
            self.assertEqual(r['refusal_category'], 'PATH_OUTSIDE_ALLOWED_HOME')
            self.assertNotIn(directory, json.dumps(r))

    def test_which_missing_fails_closed(self):
        with TemporaryDirectory() as directory:
            for host in ('codex-cli','claude-code'):
                r = dossier(host, search_path=directory, home=directory)
                self.assertEqual(r['state'], 'UNRESOLVED_NO_EXECUTION')
                self.assertFalse(r['native_launch_authorized'])

    def test_invalid_inputs_refuse(self):
        for host in ('','unknown',True,None,'codex --version'):
            with self.assertRaises(BinaryDossierError):
                dossier(host)
        for limit in (0,P6_MAX_HASH_BYTES+1,True,3.4,'320'):
            with self.assertRaises(BinaryDossierError):
                dossier('codex-cli', max_hash_bytes=limit)
        for bad in ([], True, 3):
            with self.assertRaises(BinaryDossierError):
                dossier('codex-cli',search_path=bad)

    def test_native_modes_exit_two_without_stdout(self):
        for host, mode in [('codex-cli','NATIVE_TEST'),('claude-code','ON'),('unknown','DOSSIER_ONLY')]:
            done = subprocess.run([sys.executable,'-m','knowledge_forge.portable_native_binary_dossier',
                                   '--host',host,'--mode',mode],capture_output=True,timeout=8)
            self.assertEqual(done.returncode,2)
            self.assertEqual(done.stdout,b'')

    @skip_posix_executable_fixture_on_windows
    def test_no_subprocess_or_credentials_when_dossier_called(self):
        with TemporaryDirectory() as directory:
            self._make(Path(directory))
            secret = Path(directory)/'credentials.json'
            secret.write_text('DO_NOT_READ_CANARY')
            with patch('subprocess.run',side_effect=AssertionError('do not spawn')):
                with patch('builtins.print') as sink:
                    # CLI normally observes TNG; exercise the same pure function
                    result = dossier('codex-cli', search_path=directory, home=directory)
                    self.assertTrue(result['full_file_hash_snapshot_only'])
            self.assertNotIn('DO_NOT_READ_CANARY', json.dumps(result))
            self.assertEqual(secret.read_text(), 'DO_NOT_READ_CANARY')

    @skip_posix_executable_fixture_on_windows
    def test_os_read_is_bounded_by_file_size_and_chunk(self):
        with TemporaryDirectory() as directory:
            data = b'\x7fELF' + bytes(200)
            self._make(Path(directory), content=data)
            with patch('knowledge_forge.portable_native_binary_dossier.os.read', wraps=os.read) as reads:
                got = dossier('codex-cli', search_path=directory, home=directory)
            self.assertEqual(got['full_file_bytes_read'], len(data))
            self.assertTrue(all(0 <= call.args[1] <= CHUNK_BYTES for call in reads.call_args_list))


if __name__ == '__main__':
    unittest.main()
