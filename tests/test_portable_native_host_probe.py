"""P4-P1-P0: passive executable inventory and synthetic bridge boundary tests."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import json
import os
import stat
import subprocess
import sys
import unittest
from unittest.mock import patch
from _platform_support import skip_posix_shebang_host_on_windows

from knowledge_forge.portable_native_host_probe import (
    NativeHostProbeError, native_version, replay_synthetic,
)


class NativeHostProbeTests(unittest.TestCase):
    def _fake_host(self, temp: str, text: str, *, exit_code: int = 0) -> str:
        binary = Path(temp) / "fake-native-version"
        binary.write_text(
            "#!/usr/bin/env python3\n"
            "import os,sys\n"
            "assert sys.argv[1:] == ['--version']\n"
            "assert os.environ['HOME'] != os.environ.get('HOME_FROM_TEST')\n"
            "assert os.environ['CODEX_HOME'].startswith(os.environ['HOME'])\n"
            "assert os.environ['CLAUDE_CONFIG_DIR'].startswith(os.environ['HOME'])\n"
            "assert 'OPENAI_API_KEY' not in os.environ\n"
            "assert 'ANTHROPIC_API_KEY' not in os.environ\n"
            "assert 'GH_TOKEN' not in os.environ\n"
            "assert 'GITHUB_TOKEN' not in os.environ\n"
            "assert 'AWS_SECRET_ACCESS_KEY' not in os.environ\n"
            "assert 'AZURE_API_KEY' not in os.environ\n"
            "assert 'CUSTOM_SECRET_SENTINEL' not in os.environ\n"
            "assert 'PYTHONPATH' not in os.environ\n"
            "assert 'NODE_OPTIONS' not in os.environ\n"
            f"sys.stdout.write({text!r})\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        return str(binary)

    @skip_posix_shebang_host_on_windows
    def test_isolated_version_env_both_hosts(self):
        with TemporaryDirectory() as temp:
            binary = self._fake_host(temp, "2.1.296 (Claude Code)\n")
            for host in ("claude-code", "codex-cli"):
                with self.subTest(host=host):
                    with patch.dict(os.environ, {"HOME_FROM_TEST": os.environ.get("HOME", "/")}, clear=False):
                        result = native_version(host, binary=binary)
                    self.assertEqual(result["version"], "2.1.296")
                    self.assertFalse(result["native_hook_dispatched"])
                    self.assertFalse(result["version_is_hook_capability_attestation"])

    @skip_posix_shebang_host_on_windows
    def test_real_shell_profile_and_secrets_not_needed(self):
        with TemporaryDirectory() as temp:
            binary = self._fake_host(temp, "codex-cli 0.161.0\n")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "test-never-use", "ANTHROPIC_API_KEY": "fake"}):
                result = native_version("codex-cli", binary=binary)
            self.assertEqual(result["version"], "0.161.0")

    @skip_posix_shebang_host_on_windows
    def test_unrelated_secrets_and_loader_options_never_inherited(self):
        with TemporaryDirectory() as temp:
            binary = self._fake_host(temp, "2.1.296 (Claude Code)\n")
            secret_env = {
                "GH_TOKEN": "private-gh-canary",
                "GITHUB_TOKEN": "private-github-canary",
                "AWS_SECRET_ACCESS_KEY": "private-aws-canary",
                "AZURE_API_KEY": "private-azure-canary",
                "CUSTOM_SECRET_SENTINEL": "private-custom-canary",
                "PYTHONPATH": "/nonexistent/injection/path",
                "NODE_OPTIONS": "--no-warnings",
            }
            with patch.dict(os.environ, secret_env):
                result = native_version("claude-code", binary=binary)
            self.assertEqual(result["probe"], "ISOLATED_ENV_VERSION_FLAG_ONLY")
            self.assertIs(result["native_host_settings_read_verified"], False)
            self.assertIs(result["native_host_settings_write_protection_verified"], False)
            self.assertNotIn("native_host_settings_read", result)
            self.assertNotIn("native_host_settings_written", result)

    def test_bad_host_paths_versions_refuse(self):
        with self.assertRaises(NativeHostProbeError):
            native_version("unknown")
        with self.assertRaises(NativeHostProbeError):
            native_version("claude-code", binary="relative/exe")
        with self.assertRaises(NativeHostProbeError):
            native_version("codex-cli", binary="/unlikely/this/path/does/not/exist")
        with TemporaryDirectory() as temp:
            for version, rc in (("garbage", 0), ("2.1.296", 2)):
                with self.subTest(version=version, rc=rc):
                    exe = self._fake_host(temp, version, exit_code=rc)
                    with self.assertRaises(NativeHostProbeError):
                        native_version("claude-code", binary=exe)

    def test_exact_synthetic_bridge_refusal_and_zero_stdout(self):
        for host in ("claude-code", "codex-cli"):
            with self.subTest(host=host):
                record = replay_synthetic(host)
                self.assertEqual(record["off_rc"], 0)
                self.assertEqual(record["dry_run_missing_mapping_rc"], 2)
                self.assertEqual(record["on_refusal_rc"], 2)
                self.assertEqual(record["all_stdout_bytes"], 0)
                self.assertFalse(record["safe_to_install_bridge_as_hook"])
                self.assertFalse(record["native_hook_dispatched"])
                self.assertEqual(record["tool_grants"], [])

    def test_invalid_synthetic_host_refused(self):
        with self.assertRaises(NativeHostProbeError):
            replay_synthetic("other")

    def test_cli_synthetic_only_never_calls_native_host(self):
        for host in ("claude-code", "codex-cli"):
            run = subprocess.run(
                [sys.executable, "-m", "knowledge_forge.portable_native_host_probe",
                 "--host", host, "--synthetic-only"],
                capture_output=True, timeout=25,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            data = json.loads(run.stdout)
            self.assertFalse(data["safe_to_activate_hooks"])
            self.assertFalse(data["native_dispatch_verified"])
            self.assertFalse(data["host_configuration_modified"])
            self.assertNotIn("version", data)
            self.assertNotIn("PRIVATE_SYNTHETIC_PROMPT_CANARY", run.stdout.decode())


if __name__ == "__main__":
    unittest.main()
