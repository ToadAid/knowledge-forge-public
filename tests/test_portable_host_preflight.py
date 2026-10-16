"""P3D-P1: two CLI host labels, OFF no calls, DRY_RUN no bodies or authority."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import subprocess
import sys
import unittest
from _platform_support import skip_unless_symlinks

from knowledge_forge.portable_host_preflight import (
    PortableHostPreflightError, probe, _local_directory,
)
from knowledge_forge.registry import create_skill_registry, register_promoted_skill
from tests.test_registry import promoted_fixture


class PortableHostPreflightTests(unittest.TestCase):
    def setUp(self):
        self.fixture = promoted_fixture(
            knowledge_id="knowledge:portable", value="NEVER-LEAK-THIS-VALUE",
            subject="portable.topic",
        )
        self.registry = register_promoted_skill(
            create_skill_registry(created_at="t0"), *self.fixture, registered_at="t1"
        )

    def _probe(self, temp: str, *, host="claude-code", registry=None, question="portable topic"):
        return probe(mode="DRY_RUN", host=host, question=question,
                     registry_supplier=lambda: registry if registry is not None else self.registry,
                     package_resolver=lambda _skill, _version: temp)

    def test_off_no_source_and_no_resolver_calls(self):
        def forbidden(*args):
            raise AssertionError("OFF touched host data")
        for host in ("claude-code", "codex-cli"):
            result = probe(mode="OFF", host=host, question="bad", registry_supplier=forbidden, package_resolver=forbidden)
            self.assertEqual(result["selected"], [])
            self.assertEqual(result["state"], "NO_FORGE_CALLS")
            self.assertFalse(result["automatic_consumption_active"])

    def test_off_ignores_bad_query_and_invalid_max_without_calls(self):
        result = probe(mode="OFF", host="codex-cli", question=None, max_skills=999)
        self.assertEqual(result["reasoning_context"], [])

    def test_on_refuses_for_both_hosts(self):
        for host in ("claude-code", "codex-cli"):
            with self.subTest(host=host), self.assertRaises(PortableHostPreflightError):
                probe(mode="ON", host=host)

    def test_invalid_mode_and_host_refuse(self):
        for mode,host in (("DRY_RUN", "agent0"), ("approve", "codex-cli"), ("on", "claude-code")):
            with self.subTest(mode=mode, host=host), self.assertRaises(PortableHostPreflightError):
                probe(mode=mode, host=host)

    def test_dry_run_routes_both_without_loading_bodies(self):
        with TemporaryDirectory() as tmp:
            for host in ("claude-code", "codex-cli"):
                with patch("knowledge_forge.temporal_integration.load_current_skill_context", side_effect=AssertionError("loaded")):
                    result = self._probe(tmp, host=host)
                self.assertEqual(result["mode"], "DRY_RUN")
                self.assertEqual(len(result["selected"]), 1)
                self.assertIn("DIRECTORY_EXISTS_ONLY", result["selected"][0]["package_location_check"])
                self.assertEqual(result["reasoning_context"], [])
                self.assertFalse(result["knowledge_bodies_loaded"])
                self.assertFalse(result["model_context_injected"])
                self.assertFalse(result["host_authentication_verified"])
                self.assertFalse(result["promotion_authorized"])
                self.assertFalse(result["operator_invoked"])
                self.assertFalse(result["registry_mutated"])
                self.assertEqual(result["tool_grants"], [])
                self.assertNotIn("NEVER-LEAK-THIS-VALUE", json.dumps(result))

    def test_dry_run_no_match_refuses(self):
        with TemporaryDirectory() as tmp, self.assertRaises(PortableHostPreflightError):
            self._probe(tmp, question="unrelated zebra spaceship")

    def test_empty_registry_refuses_without_fake_promotion(self):
        with TemporaryDirectory() as tmp, self.assertRaises(PortableHostPreflightError):
            self._probe(tmp, registry=create_skill_registry(created_at="t0"))

    def test_bad_max_refuses(self):
        for max_skills in (0, 4, True, 1.1, "1"):
            with self.subTest(max_skills=max_skills), self.assertRaises(PortableHostPreflightError):
                probe(mode="DRY_RUN", host="codex-cli", question="portable topic",
                      registry_supplier=lambda: self.registry, package_resolver=lambda *a:"/tmp", max_skills=max_skills)

    def test_missing_callables_refuses(self):
        with self.assertRaises(PortableHostPreflightError):
            probe(mode="DRY_RUN", host="claude-code", question="portable topic")

    def test_missing_question_refuses(self):
        with self.assertRaises(PortableHostPreflightError):
            probe(mode="DRY_RUN", host="claude-code", question=" ",
                  registry_supplier=lambda: self.registry, package_resolver=lambda *a:"/tmp")

    def test_missing_package_path_refuses(self):
        with TemporaryDirectory() as tmp, self.assertRaises(PortableHostPreflightError):
            self._probe(str(Path(tmp)/"not-present"))

    def test_file_instead_of_dir_refuses(self):
        with TemporaryDirectory() as tmp:
            f = Path(tmp)/"file"; f.write_text("data")
            with self.assertRaises(PortableHostPreflightError):
                self._probe(str(f))

    @skip_unless_symlinks
    def test_symlink_package_refuses(self):
        with TemporaryDirectory() as tmp:
            link=Path(tmp)/"linked";link.symlink_to(tmp, target_is_directory=True)
            with self.assertRaises(PortableHostPreflightError):
                self._probe(str(link))

    @skip_unless_symlinks
    def test_symlink_ancestor_refuses(self):
        with TemporaryDirectory() as tmp:
            link=Path(tmp)/"parent";link.symlink_to(tmp, target_is_directory=True)
            with self.assertRaises(PortableHostPreflightError):
                _local_directory(str(link / "child"))

    def test_relative_package_refuses(self):
        for path in ("../tmp", "tmp", "/tmp/../tmp", "", "\x00"):
            with self.subTest(path=path), self.assertRaises(PortableHostPreflightError):
                _local_directory(path)

    def test_registry_source_refusal(self):
        with TemporaryDirectory() as tmp, self.assertRaises(PortableHostPreflightError):
            probe(mode="DRY_RUN", host="claude-code", question="portable topic",
                  registry_supplier=lambda: {"bad":"registry"}, package_resolver=lambda *_:tmp)

    def test_mapping_version_is_label_not_authority(self):
        with TemporaryDirectory() as tmp:
            result=self._probe(tmp)
        self.assertIs(result["selected"][0]["trusted_registry_verified"], False)
        self.assertIs(result["selected"][0]["promoted_package_revalidated"], False)

    def test_cli_off_no_file_access(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_host_preflight","--mode","OFF",
            "--host","claude-code","--registry","/does/not/exist","--packages-map","/does/not/exist"],
            text=True,capture_output=True,timeout=8)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)["state"],"NO_FORGE_CALLS")

    def test_cli_on_refusal(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_host_preflight","--mode","ON",
            "--host","codex-cli"],text=True,capture_output=True,timeout=8)
        self.assertEqual(run.returncode,2)
        self.assertIn("BLOCKED",run.stderr)

    def test_cli_dry_run_bad_files_refuses(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_host_preflight","--mode","DRY_RUN",
            "--host","codex-cli","--question","portable topic","--registry","/bad/missing",
            "--packages-map","/bad/missing"],text=True,capture_output=True,timeout=8)
        self.assertEqual(run.returncode,2)
        self.assertNotIn("NEVER-LEAK-THIS-VALUE",run.stdout)

    def test_bodies_never_in_output_shape(self):
        with TemporaryDirectory() as tmp:
            result=self._probe(tmp)
            self.assertEqual(result["reasoning_context"],[])
            self.assertEqual(result["execution_authority"],"NONE")
            self.assertEqual(result["instruction_authority"],"NONE")


if __name__ == "__main__":
    unittest.main()
