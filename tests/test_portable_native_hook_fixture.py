"""P4-P1-P1-P0: offline native-config fixture; real hook dispatch remains blocked."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from _platform_support import skip_posix_owner_fixture_on_windows, skip_unless_symlinks

from knowledge_forge.portable_native_hook_fixture import (
    HANDLER_SOURCE, NativeHookFixtureError, replay_fixture, stage_fixture,
)


class NativeHookFixtureTests(unittest.TestCase):
    @skip_posix_owner_fixture_on_windows
    def test_both_host_config_shapes_and_narrow_paths(self):
        for host in ("claude-code", "codex-cli"):
            with self.subTest(host=host), TemporaryDirectory(prefix="kf-native-hook-fixture-") as scratch:
                paths = stage_fixture(host, Path(scratch))
                config = json.loads(paths["config"].read_text(encoding="utf-8"))
                self.assertEqual(set(config), {"hooks"})
                group = config["hooks"]["UserPromptSubmit"]
                self.assertEqual(len(group), 1)
                hook = group[0]["hooks"][0]
                self.assertEqual(hook["type"], "command")
                self.assertIn(str(paths["handler"]), hook["command"])
                self.assertNotIn("portable_hook_bridge", hook["command"])
                self.assertEqual(hook["timeout"], 10)
                self.assertEqual(paths["config"].name, "settings.json" if host == "claude-code" else "hooks.json")
                self.assertTrue(paths["config"].is_relative_to(paths["root"]))

    @skip_posix_owner_fixture_on_windows
    def test_duplicate_synthetic_replay_is_metadata_only(self):
        for host in ("claude-code", "codex-cli"):
            result = replay_fixture(host)
            self.assertEqual(result["synthetic_dispatch_count"], 2)
            self.assertEqual(result["synthetic_return_codes"], [0, 0])
            self.assertFalse(result["native_hook_dispatched"])
            self.assertFalse(result["model_context_injected"])
            self.assertFalse(result["safe_to_activate_hooks"])
            self.assertEqual(result["all_stdout_bytes"], 0)
            self.assertEqual(result["tool_grants"], [])

    @skip_posix_owner_fixture_on_windows
    def test_offline_subprocess_has_no_credentials_or_python_loader(self):
        # Probe subprocess env by patching subprocess.run. No native host process involved.
        from knowledge_forge import portable_native_hook_fixture as mod
        real = mod.subprocess.run
        observed = []
        def capture(*args, **kwargs):
            env = kwargs["env"]
            observed.append(dict(env))
            self.assertNotIn("GH_TOKEN", env)
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("PYTHONPATH", env)
            self.assertNotIn("NODE_OPTIONS", env)
            return real(*args, **kwargs)
        with patch.dict(os.environ, {"GH_TOKEN": "SECRET", "OPENAI_API_KEY": "SECRET", "PYTHONPATH": "/bad", "NODE_OPTIONS": "--bad"}), patch.object(mod.subprocess, "run", side_effect=capture):
            result = replay_fixture("claude-code")
        self.assertEqual(len(observed), 2)
        self.assertEqual(result["synthetic_dispatch_count"], 2)

    def test_refuses_nonempty_or_wrong_root(self):
        with TemporaryDirectory(prefix="kf-native-hook-fixture-") as scratch:
            root = Path(scratch)
            (root / "keep.txt").write_text("do-not-modify", encoding="utf-8")
            with self.assertRaises(NativeHookFixtureError):
                stage_fixture("codex-cli", root)
            self.assertEqual((root / "keep.txt").read_text(), "do-not-modify")
        with TemporaryDirectory(prefix="wrong-root-") as scratch:
            with self.assertRaises(NativeHookFixtureError):
                stage_fixture("claude-code", Path(scratch))

    @skip_unless_symlinks
    def test_symlink_root_refused_without_touching_target(self):
        with TemporaryDirectory(prefix="kf-native-hook-fixture-") as scratch:
            p = Path(scratch) / "kf-native-hook-fixture-link"
            p.symlink_to(scratch, target_is_directory=True)
            with self.assertRaises(NativeHookFixtureError):
                stage_fixture("claude-code", p)

    @skip_posix_owner_fixture_on_windows
    def test_cli_offline_smoke_and_on_refusal(self):
        for host in ("claude-code", "codex-cli"):
            done = subprocess.run([sys.executable, "-m", "knowledge_forge.portable_native_hook_fixture",
                                   "--host", host, "--mode", "PREPARE_ONLY"],
                                  capture_output=True, timeout=10, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertFalse(json.loads(done.stdout)["native_dispatch_verified"])
            on = subprocess.run([sys.executable, "-m", "knowledge_forge.portable_native_hook_fixture",
                                 "--host", host, "--mode", "ON"],
                                capture_output=True, timeout=10, check=False)
            self.assertEqual(on.returncode, 2)
            self.assertEqual(on.stdout, b"")

    @skip_posix_owner_fixture_on_windows
    def test_handler_discards_bad_event_and_no_stdout(self):
        with TemporaryDirectory(prefix="kf-native-hook-fixture-") as scratch:
            fixture = stage_fixture("claude-code", Path(scratch))
            for bad in (b"not json", b"[]", b"{}", b"x" * 16385):
                done = subprocess.run([sys.executable, str(fixture["handler"]), str(fixture["record"])],
                                      input=bad, capture_output=True, timeout=5)
                self.assertEqual(done.returncode, 0)
                self.assertEqual(done.stdout, b"")
                self.assertEqual(done.stderr, b"")
            self.assertFalse(fixture["record"].exists())


if __name__ == "__main__":
    unittest.main()
