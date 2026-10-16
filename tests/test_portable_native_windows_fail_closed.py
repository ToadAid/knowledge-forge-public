"""P3B-CP1 R2: Linux-native portable modules fail CLOSED on native Windows.

The P4/P6/P7 portable-native modules inspect POSIX host executables and Linux
namespaces. On native Windows the Linux-specific tests for them are skipped by
name (tests/_platform_support.py). This file replaces that lost signal with
evidence: on Windows each module, given the same synthetic fixtures, must refuse
or stay unresolved, never hash, never resolve, never start a native host and
never report any authority. These are refusals, not fabricated successes.

The env-allowlist test at the end runs on EVERY OS: it keeps the
credential/loader-stripping contract of the native version probe covered on
Windows without executing a POSIX shebang script.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from _platform_support import NATIVE_WINDOWS

from knowledge_forge import portable_native_host_probe as host_probe_mod
from knowledge_forge.portable_isolation_preflight import preflight as isolation_preflight
from knowledge_forge.portable_native_binary_dossier import dossier
from knowledge_forge.portable_native_dependency_evidence import evidence
from knowledge_forge.portable_native_executable_inventory import inventory
from knowledge_forge import portable_native_hermetic_preflight as hermetic_mod
from knowledge_forge.portable_native_hook_fixture import NativeHookFixtureError, stage_fixture
from knowledge_forge.portable_native_host_probe import NativeHostProbeError, native_version
from knowledge_forge.portable_native_provenance_hints import provenance_hints

FAIL_CLOSED_PREFIXES = ("UNRESOLVED_", "BLOCKED_")
HOSTS = (("codex-cli", "codex"), ("claude-code", "claude"))
ELF_BYTES = b"\x7fELF" + b"\x00" * 500


def _authority_keys(result: dict) -> list[str]:
    return [k for k in result if k.endswith(("_authorized", "_verified", "_started", "_dispatched"))
            or k in ("safe_to_activate_hooks", "credentials_accessed", "network_traffic_sent", "model_called")]


@unittest.skipUnless(NATIVE_WINDOWS, "native Windows fail-closed evidence; Linux runs the positive tests")
class WindowsPosixExecutableModulesFailClosedTests(unittest.TestCase):
    """Same POSIX fixtures the skipped Linux tests build: no resolution, no hash, no authority."""

    def _fixtures(self, root: Path, command: str) -> list[Path]:
        made = []
        for name in (command, command + ".exe", command + ".cmd"):
            path = root / name
            path.write_bytes(ELF_BYTES)
            path.chmod(0o700)
            made.append(path)
        return made

    def _assert_fail_closed(self, result: dict, scratch: str) -> None:
        self.assertTrue(result["state"].startswith(FAIL_CLOSED_PREFIXES), result["state"])
        for key in _authority_keys(result):
            self.assertIs(result[key], False, key)
        if "tool_grants" in result:
            self.assertEqual(result["tool_grants"], [])
        text = json.dumps(result)
        self.assertNotIn(scratch, text)
        self.assertNotIn(scratch.replace("\\", "\\\\"), text)
        for key, value in result.items():
            if "sha256" in key:
                self.assertFalse(isinstance(value, str) and len(value) == 64, key)

    def test_all_four_inspectors_never_resolve_or_hash_posix_fixtures(self):
        inspectors = {
            "binary_dossier": lambda host, d: dossier(host, search_path=d, home=d),
            "dependency_evidence": lambda host, d: evidence(host, search_path=d, home=d),
            "executable_inventory": lambda host, d: inventory(host, search_path=d, home=d),
            "provenance_hints": lambda host, d: provenance_hints(host, search_path=d, home=d),
        }
        for name, call in inspectors.items():
            for host, command in HOSTS:
                with self.subTest(module=name, host=host), TemporaryDirectory() as scratch:
                    self._fixtures(Path(scratch), command)
                    with patch("subprocess.run", side_effect=AssertionError("must not spawn")):
                        result = call(host, scratch)
                    self._assert_fail_closed(result, scratch)


@unittest.skipUnless(NATIVE_WINDOWS, "native Windows fail-closed evidence; Linux runs the namespace tests")
class WindowsNamespaceProbesFailClosedTests(unittest.TestCase):
    """A perfect synthetic probe result must still be refused without Linux /proc namespaces."""

    def test_isolation_preflight_refuses_perfect_synthetic_record(self):
        record = {"net_namespace_inode": 4026532999, "interfaces": ["lo"], "canary_visible": False,
                  "home_is_tmp": True, "python_isolated_mode": True}
        done = subprocess.CompletedProcess(["bwrap"], 0, stdout=json.dumps(record).encode(), stderr=b"")
        with patch.object(Path, "is_file", return_value=True), \
             patch("knowledge_forge.portable_isolation_preflight.subprocess.run", return_value=done):
            result = isolation_preflight(execute_probe=True)
        self.assertEqual(result["state"], "BLOCKED_ISOLATION_NOT_PROVEN")
        self.assertFalse(result["network_namespace_verified"])
        self.assertFalse(result["native_dispatch_verified"])
        self.assertFalse(result["safe_to_activate_hooks"])

    def test_hermetic_preflight_refuses_perfect_synthetic_receipt(self):
        receipt = {
            "namespaces": {"net": 1999, "mnt": 2999, "pid": 3999}, "interfaces": ["lo"],
            "user_home_absent": True, "host_canary_absent": True, "empty_project_and_config": True,
            "private_scratch_writable": True, "read_only_usr_mount": True,
            "scrubbed_environment": True, "home_is_private": True,
        }
        done = subprocess.CompletedProcess(["bwrap"], 0, stdout=json.dumps(receipt).encode(), stderr=b"")
        with patch.object(hermetic_mod, "_mounts", return_value=[hermetic_mod.BWRAP, "--unshare-all", "--clearenv"]), \
             patch.object(hermetic_mod.subprocess, "run", return_value=done):
            result = hermetic_mod.preflight("codex-cli", execute_fixture=True)
        self.assertEqual(result["state"], hermetic_mod.STATUS_BLOCKED)
        self.assertEqual(result["native_runtime_gates_proven"], 0)
        self.assertFalse(result["native_launch_authorized"])
        self.assertFalse(result["native_host_started"])
        self.assertEqual(result["tool_grants"], [])


@unittest.skipUnless(NATIVE_WINDOWS, "native Windows fail-closed evidence; Linux runs the fixture tests")
class WindowsHookFixtureFailClosedTests(unittest.TestCase):
    def test_stage_fixture_refuses_without_posix_ownership_and_writes_nothing(self):
        for host, _ in HOSTS:
            with self.subTest(host=host), TemporaryDirectory(prefix="kf-native-hook-fixture-") as scratch:
                with self.assertRaises(NativeHookFixtureError):
                    stage_fixture(host, Path(scratch))
                self.assertEqual(list(Path(scratch).iterdir()), [])

    def test_cli_refuses_every_mode_with_no_stdout(self):
        for host, _ in HOSTS:
            for mode in ("PREPARE_ONLY", "ON"):
                with self.subTest(host=host, mode=mode):
                    done = subprocess.run([sys.executable, "-m", "knowledge_forge.portable_native_hook_fixture",
                                           "--host", host, "--mode", mode],
                                          capture_output=True, timeout=10, check=False)
                    self.assertEqual(done.returncode, 2)
                    self.assertEqual(done.stdout, b"")
                    self.assertIn(b"native host not started", done.stderr)


@unittest.skipUnless(NATIVE_WINDOWS, "native Windows fail-closed evidence; Linux executes the shebang host")
class WindowsHostProbeFailClosedTests(unittest.TestCase):
    def test_posix_shebang_host_is_refused_not_reported(self):
        with TemporaryDirectory() as scratch:
            binary = Path(scratch) / "fake-native-version"
            binary.write_text("#!/usr/bin/env python3\nprint('2.1.296 (Claude Code)')\n", encoding="utf-8")
            for host, _ in HOSTS:
                with self.subTest(host=host), self.assertRaises(NativeHostProbeError):
                    native_version(host, binary=str(binary))


class NativeVersionProbeEnvAllowlistAllPlatformsTests(unittest.TestCase):
    """Runs on EVERY OS: the version probe never forwards credentials or loader options."""

    SECRETS = {
        "OPENAI_API_KEY": "private-openai-canary", "ANTHROPIC_API_KEY": "private-anthropic-canary",
        "GH_TOKEN": "private-gh-canary", "GITHUB_TOKEN": "private-github-canary",
        "AWS_SECRET_ACCESS_KEY": "private-aws-canary", "AZURE_API_KEY": "private-azure-canary",
        "CUSTOM_SECRET_SENTINEL": "private-custom-canary",
        "PYTHONPATH": "/nonexistent/injection/path", "NODE_OPTIONS": "--no-warnings",
    }

    def test_env_is_an_allowlist_with_private_scratch_home(self):
        observed = []

        def capture(argv, **kwargs):
            observed.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, stdout=b"2.1.296 (Claude Code)\n", stderr=b"")

        with TemporaryDirectory() as scratch:
            binary = Path(scratch) / "fake-native-version"
            binary.write_bytes(b"synthetic, never executed")
            for host, _ in HOSTS:
                with self.subTest(host=host):
                    with patch.dict(os.environ, self.SECRETS), \
                         patch.object(host_probe_mod.subprocess, "run", side_effect=capture):
                        result = native_version(host, binary=str(binary))
                    argv, kwargs = observed[-1]
                    env = kwargs["env"]
                    self.assertEqual(argv, [str(binary), "--version"])
                    for name in self.SECRETS:
                        self.assertNotIn(name, env)
                    self.assertNotIn("canary", json.dumps(env))
                    self.assertEqual(env["HOME"], kwargs["cwd"])
                    self.assertNotEqual(env["HOME"], os.path.expanduser("~"))
                    self.assertTrue(env["CODEX_HOME"].startswith(env["HOME"]))
                    self.assertTrue(env["CLAUDE_CONFIG_DIR"].startswith(env["HOME"]))
                    self.assertEqual(result["version"], "2.1.296")
                    self.assertFalse(result["native_hook_dispatched"])
                    self.assertFalse(result["version_is_hook_capability_attestation"])


if __name__ == "__main__":
    unittest.main()
