from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from _platform_support import skip_bash_launcher_on_windows


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "bin" / "knowledge-forge-install-agent"


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(INSTALLER), *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


@skip_bash_launcher_on_windows
class AgentInstallerTests(unittest.TestCase):
    def _env(self, root: Path) -> dict[str, str]:
        env = dict(os.environ)
        home = root / "home"
        home.mkdir()
        env["HOME"] = str(home)
        env["XDG_STATE_HOME"] = str(root / "xdg-state")
        return env

    def test_check_only_is_no_write_and_reports_zero_operator_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env(root)
            prefix = root / "prefix"
            state = root / "state"

            run = _run(
                "--check-only",
                "--prefix", str(prefix),
                "--state-root", str(state),
                env=env,
            )

            self.assertEqual(run.returncode, 0, run.stderr)
            payload = json.loads(run.stdout)
            self.assertEqual(payload["status"], "CHECK_OK")
            self.assertFalse(payload["consumer_cli_installed"])
            self.assertFalse(payload["authoring_cli_installed"])
            self.assertFalse(payload["operator_cli_installed"])
            self.assertEqual(
                payload["promotion_authority"],
                "EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY",
            )
            self.assertFalse(prefix.exists())
            self.assertFalse(state.exists())

    def test_install_creates_only_consumer_authoring_shims_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env(root)
            prefix = root / "prefix"
            state = root / "state"

            run = _run(
                "--prefix", str(prefix),
                "--state-root", str(state),
                env=env,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            payload = json.loads(run.stdout)
            self.assertEqual(payload["status"], "READY")
            self.assertTrue(payload["consumer_cli_installed"])
            self.assertTrue(payload["authoring_cli_installed"])
            self.assertFalse(payload["operator_cli_installed"])

            consumer = prefix / "bin" / "knowledge-forge"
            authoring = prefix / "bin" / "knowledge-forge-authoring"
            operator = prefix / "bin" / "knowledge-forge-operator"
            manifest = state / "agent-integration.json"

            self.assertTrue(consumer.is_file())
            self.assertTrue(authoring.is_file())
            self.assertFalse(operator.exists())
            self.assertTrue(os.access(consumer, os.X_OK))
            self.assertTrue(os.access(authoring, os.X_OK))

            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual(value["trust_class"], "UNTRUSTED_KNOWLEDGE_DATA")
            self.assertEqual(value["authority"], "KNOWLEDGE_ONLY")
            self.assertEqual(value["instruction_authority"], "NONE")
            self.assertEqual(value["execution_authority"], "NONE")
            self.assertFalse(value["executable"])
            self.assertFalse(value["operator_cli_installed"])
            self.assertEqual(value["operator_surface"], "HOST_OWNED_NOT_INSTALLED")

            consumer_help = subprocess.run(
                [str(consumer), "--help"],
                cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False,
            )
            authoring_help = subprocess.run(
                [str(authoring), "--help"],
                cwd=REPO_ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(consumer_help.returncode, 0, consumer_help.stderr)
            self.assertEqual(authoring_help.returncode, 0, authoring_help.stderr)

    def test_exact_reinstall_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env(root)
            prefix = root / "prefix"
            state = root / "state"
            args = ("--prefix", str(prefix), "--state-root", str(state))

            first = _run(*args, env=env)
            second = _run(*args, env=env)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(second.stdout)["status"], "READY")

    def test_refuses_prefix_that_exposes_operator_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env(root)
            prefix = root / "prefix"
            state = root / "state"
            bin_dir = prefix / "bin"
            bin_dir.mkdir(parents=True)
            (bin_dir / "knowledge-forge-operator").write_text(
                "#!/bin/sh\n", encoding="utf-8",
            )

            run = _run(
                "--prefix", str(prefix),
                "--state-root", str(state),
                env=env,
            )

            self.assertEqual(run.returncode, 2)
            self.assertIn("already exposes knowledge-forge-operator", run.stderr)

    def test_refuses_overwrite_of_different_existing_consumer_shim(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = self._env(root)
            prefix = root / "prefix"
            state = root / "state"
            bin_dir = prefix / "bin"
            bin_dir.mkdir(parents=True)
            target = bin_dir / "knowledge-forge"
            original = "#!/bin/sh\necho foreign\n"
            target.write_text(original, encoding="utf-8")

            run = _run(
                "--prefix", str(prefix),
                "--state-root", str(state),
                env=env,
            )

            self.assertEqual(run.returncode, 2)
            self.assertIn("refusing to overwrite different existing file", run.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
