"""Archive/bootstrap tests never execute a deployment or mutate real Git history."""
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge import native_deployment as n

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT/'scripts/migrate_codex_native.py'
COMMIT = 'a'*40


@unittest.skipUnless(os.name == "posix", "Linux bootstrap uses POSIX UID fixtures")
class NativeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = runpy.run_path(str(BOOTSTRAP))

    def archive(self, *, unsafe=None, changed=None):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            directory = tarfile.TarInfo('scripts/')
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
            for name in ('scripts/migrate_codex_native.py','src/knowledge_forge/native_deployment.py','install.sh'):
                data = (ROOT/name).read_bytes()
                if name == changed:
                    data += b'\n# different from reviewed bytes\n'
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
            if unsafe:
                info = tarfile.TarInfo(unsafe)
                info.type = tarfile.SYMTYPE if unsafe == 'symlink' else tarfile.REGTYPE
                info.linkname = '/etc/forbidden'
                archive.addfile(info, io.BytesIO(b''))
        return buffer.getvalue()

    def test_pinned_archive_accepts_directory_entries_and_exact_bytes(self):
        with patch('subprocess.check_output', side_effect=[(COMMIT+'\n').encode(), self.archive()]) as git:
            files = self.bootstrap['snapshot'](ROOT, COMMIT)
        self.assertEqual(files['install.sh'], (ROOT/'install.sh').read_bytes())
        for call in git.call_args_list:
            self.assertIn('safe.directory='+str(ROOT), call.args[0])
            self.assertEqual(call.kwargs['env']['GIT_CONFIG_GLOBAL'], '/dev/null')
            self.assertEqual(call.kwargs['env']['GIT_NO_LAZY_FETCH'], '1')
        self.assertIn('archive', git.call_args_list[1].args[0])

    def test_changed_reviewed_hash_or_live_installer_refuses(self):
        with patch('subprocess.check_output', return_value=('b'*40).encode()):
            with self.assertRaisesRegex(ValueError, 'commit mismatch'):
                self.bootstrap['snapshot'](ROOT, COMMIT)
        for changed in ('scripts/migrate_codex_native.py','src/knowledge_forge/native_deployment.py','install.sh'):
            with patch('subprocess.check_output', side_effect=[COMMIT.encode(), self.archive(changed=changed)]):
                with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
                    self.bootstrap['snapshot'](ROOT, COMMIT)

    def test_symlink_traversal_and_duplicate_archive_entries_refuse(self):
        for name in ('symlink', '../escape', '/absolute', 'install.sh'):
            with self.subTest(name=name), patch('subprocess.check_output', side_effect=[COMMIT.encode(), self.archive(unsafe=name)]):
                with self.assertRaises(ValueError):
                    self.bootstrap['snapshot'](ROOT, COMMIT)

    def test_non_root_apply_refuses_before_git_or_host_inspection(self):
        with patch('os.geteuid', return_value=1000), patch('subprocess.check_output') as git, patch('sys.stdout', io.StringIO()) as output:
            result = self.bootstrap['main'](['--commit', COMMIT, '--reviewer','synthetic','--apply'])
        self.assertEqual(result, 2)
        git.assert_not_called()
        self.assertFalse(json.loads(output.getvalue())['promotion_performed'])

    def test_unknown_or_enabled_units_are_not_absent(self):
        missing = subprocess.CompletedProcess([], 4, 'not-found\n', '')
        enabled = subprocess.CompletedProcess([], 0, 'enabled\n', '')
        disabled = subprocess.CompletedProcess([], 4, 'not-found\n', '')
        for response, expected in ((enabled,False),(disabled,True)):
            with patch('subprocess.run', side_effect=[missing,response]):
                self.assertEqual(n.Host().unit_absent(n.SOCKET_NAME), expected)

    @unittest.skipUnless(shutil.which('systemd-analyze') and os.name == 'posix', 'systemd-analyze unavailable')
    def test_matching_native_unit_pair_validates_offline(self):
        with tempfile.TemporaryDirectory(prefix='kf-native-unit-verify-') as temporary:
            root = Path(temporary)
            for name in (n.SOCKET_NAME, n.SERVICE_NAME):
                text = (ROOT/'deploy/linux-protected-operator'/name).read_text()
                text = text.replace('ExecStart=/opt/toadaid-kf-agent/bin/knowledge-forge-codex-operator', 'ExecStart=/usr/bin/true')
                text = text.replace('User=kf-operator', 'User='+str(os.getuid())).replace('Group=kf-operator','Group='+str(os.getgid()))
                text = text.replace('SocketUser=kf-operator','SocketUser='+str(os.getuid())).replace('SocketGroup=kf-agent','SocketGroup='+str(os.getgid()))
                text = text.replace('/run/toadaid-knowledge-forge/codex-operator.sock',str(root/'native.sock'))
                (root/name).write_text(text)
            result = subprocess.run(['systemd-analyze','verify','--man=no',str(root/n.SERVICE_NAME),str(root/n.SOCKET_NAME)],
                capture_output=True, text=True, env={'PATH':'/usr/bin:/bin','LC_ALL':'C','SYSTEMD_UNIT_PATH':str(root)+':'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root/'native.sock').exists())


if __name__ == '__main__':
    unittest.main()
