"""P4-P1-P1-P3-P2: executable metadata only, header bounds, no native launch."""
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
from _platform_support import skip_posix_executable_fixture_on_windows, skip_without_os_pread

from knowledge_forge.portable_native_executable_inventory import MAX_BYTES
from knowledge_forge.portable_native_dependency_evidence import (
    HEADER_SAMPLE_BYTES, MAX_STATIC_BYTES_READ, NativeDependencyEvidenceError,
    evidence, main,
)


class NativeDependencyEvidenceTests(unittest.TestCase):
    @staticmethod
    def _file(root: Path, name='codex', data=b'\x7fELF' + b'\x00' * 60):
        path = root / name
        path.write_bytes(data)
        path.chmod(0o700)
        return path

    @skip_without_os_pread
    @skip_posix_executable_fixture_on_windows
    def test_oversized_sparse_binary_avoids_full_digest(self):
        with TemporaryDirectory() as scratch:
            path = self._file(Path(scratch))
            with open(path, 'r+b') as handle:
                handle.truncate(292309256)  # sparse synthetic analogue of TNG Codex
            with patch('knowledge_forge.portable_native_dependency_evidence.os.pread', wraps=os.pread) as read:
                result = evidence('codex-cli', search_path=scratch, home=scratch)
            self.assertEqual(result['state'], 'PASSIVE_OVERSIZED_METADATA_ONLY')
            self.assertEqual(result['bounded_file_metadata']['file_size_bytes'], 292309256)
            self.assertTrue(result['bounded_file_metadata']['exceeds_original_256_mib_digest_cap'])
            self.assertLessEqual(sum(c.args[1] for c in read.call_args_list), MAX_STATIC_BYTES_READ)
            self.assertFalse(result['complete_executable_sha256_verified_by_this_cut'])
            self.assertFalse(result['native_launch_authorized'])
            self.assertFalse(result['dependency_tree_verified'])
            self.assertFalse(result['package_provenance_verified'])
            self.assertNotIn('executable_file_identity', result)
            self.assertNotIn('sha256', result['bounded_file_metadata'])

    @skip_posix_executable_fixture_on_windows
    def test_bounded_small_script_header_only(self):
        with TemporaryDirectory() as scratch:
            path = self._file(Path(scratch), name='claude', data=b'#!/usr/bin/env node\n' + b'A' * 10000)
            r = evidence('claude-code', search_path=scratch, home=scratch)
            self.assertEqual(r['state'], 'PASSIVE_BOUNDED_METADATA_ONLY')
            meta = r['bounded_file_metadata']
            self.assertEqual(meta['header_sample_bytes_read'], HEADER_SAMPLE_BYTES)
            self.assertEqual(meta['header_sample_sha256_not_full_file_hash'], hashlib.sha256(path.read_bytes()[:HEADER_SAMPLE_BYTES]).hexdigest())
            self.assertEqual(meta['static_runtime_loader_hint']['kind'], 'SCRIPT')
            self.assertEqual(meta['static_runtime_loader_hint']['interpreter_basename_hint'], 'env')

    @skip_posix_executable_fixture_on_windows
    def test_inferred_loader_hint_is_not_dependency_attestation(self):
        with TemporaryDirectory() as scratch:
            self._file(Path(scratch))
            r = evidence('codex-cli', search_path=scratch)
            self.assertEqual(r['state'], 'PASSIVE_BOUNDED_METADATA_ONLY')
            self.assertFalse(r['dependency_tree_verified'])
            self.assertFalse(r['complete_executable_sha256_verified_by_this_cut'])
            self.assertEqual(r['bounded_file_metadata']['static_runtime_loader_hint']['dependency_tree_verified'], 'false')

    @skip_posix_executable_fixture_on_windows
    def test_symlink_path_redaction(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            self._file(root, name='large-bin')
            (root/'codex').symlink_to('large-bin')
            r=evidence('codex-cli', search_path=scratch, home=scratch)
            self.assertEqual(r['resolved_executable_path'], '$HOME/large-bin')
            self.assertEqual(r['symlink_chain'][0]['at'], '$HOME/codex')
            self.assertFalse(r['host_session_started'])

    def test_missing_or_unexecutable_file_not_success(self):
        with TemporaryDirectory() as scratch:
            self.assertEqual(evidence('claude-code', search_path=scratch)['state'], 'UNRESOLVED_NO_EXECUTION')
            self._file(Path(scratch), name='claude').chmod(0o600)
            self.assertEqual(evidence('claude-code', search_path=scratch)['state'], 'UNRESOLVED_NO_EXECUTION')

    @skip_posix_executable_fixture_on_windows
    def test_world_writable_candidate_refused(self):
        with TemporaryDirectory() as scratch:
            self._file(Path(scratch)).chmod(0o707)
            r=evidence('codex-cli', search_path=scratch, home=scratch)
            self.assertEqual(r['state'], 'BLOCKED_METADATA_NOT_PROVEN')
            self.assertEqual(r['refusal_category'], 'CANDIDATE_UNSAFE_MODE')
            self.assertNotIn('resolved_executable_path', r)

    @skip_posix_executable_fixture_on_windows
    def test_missing_or_moving_candidate_fail_closed(self):
        with TemporaryDirectory() as scratch:
            self._file(Path(scratch))
            with patch('knowledge_forge.portable_native_dependency_evidence._inspect', side_effect=FileNotFoundError('/private/token')):
                r=evidence('codex-cli', search_path=scratch)
            self.assertEqual(r['state'], 'BLOCKED_METADATA_NOT_PROVEN')
            self.assertEqual(r['refusal_category'], 'FILE_OR_LINK_METADATA_UNAVAILABLE')
            self.assertNotIn('/private/token', json.dumps(r))
            with patch('knowledge_forge.portable_native_dependency_evidence._inspect', side_effect=NativeDependencyEvidenceError('CANDIDATE_CHANGED_DURING_READ')):
                r=evidence('codex-cli', search_path=scratch)
            self.assertEqual(r['refusal_category'], 'CANDIDATE_CHANGED_DURING_READ')

    @skip_without_os_pread
    @skip_posix_executable_fixture_on_windows
    def test_candidate_header_read_is_bounded_for_oversized_file(self):
        with TemporaryDirectory() as scratch:
            path = self._file(Path(scratch))
            with open(path, 'r+b') as handle: handle.truncate(MAX_BYTES + 1)
            with patch('knowledge_forge.portable_native_dependency_evidence.os.pread', wraps=os.pread) as calls:
                r=evidence('codex-cli', search_path=scratch)
            self.assertEqual(r['state'], 'PASSIVE_OVERSIZED_METADATA_ONLY')
            self.assertLessEqual(sum(c.args[1] for c in calls.call_args_list), MAX_STATIC_BYTES_READ)

    def test_no_process_spawn_on_plan(self):
        with patch('subprocess.run', side_effect=AssertionError('never spawn agents')):
            with patch('builtins.print') as sink:
                self.assertEqual(main(['--host','codex-cli','--mode','EVIDENCE_ONLY']),0)
        r=json.loads(sink.call_args.args[0])
        self.assertFalse(r['native_launch_authorized'])

    def test_cli_refuses_native_modes_no_stdout(self):
        for host, mode in (('claude-code','ON'),('codex-cli','NATIVE_TEST'),('unknown','EVIDENCE_ONLY')):
            p=subprocess.run([sys.executable,'-m','knowledge_forge.portable_native_dependency_evidence',
                              '--host',host,'--mode',mode],capture_output=True,timeout=8)
            self.assertEqual(p.returncode,2)
            self.assertEqual(p.stdout,b'')

    def test_bad_host_and_search_path_refused(self):
        for host in ('',None,True,'codex --version'):
            with self.assertRaises(NativeDependencyEvidenceError): evidence(host)
        for path in ([], True, 99):
            with self.assertRaises(NativeDependencyEvidenceError): evidence('codex-cli',search_path=path)

    def test_credentials_and_settings_not_read(self):
        with TemporaryDirectory() as scratch:
            root=Path(scratch)
            self._file(root)
            secret=root/'credentials.json'
            secret.write_text('PRIVATE_SECRET_CONTENT',encoding='utf-8')
            with patch('subprocess.run',side_effect=AssertionError('external command')):
                r=evidence('codex-cli',search_path=scratch,home=scratch)
            self.assertNotIn('PRIVATE_SECRET_CONTENT',json.dumps(r))
            self.assertEqual(secret.read_text(encoding='utf-8'),'PRIVATE_SECRET_CONTENT')

if __name__ == '__main__': unittest.main()
