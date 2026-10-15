"""P3D-P2: real trust+temporal verification, metadata-only output, ON blocked."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import json
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from knowledge_forge.portable_verify_only import PortableVerifyError, verify
from knowledge_forge.registry import create_skill_registry
from tests.test_temporal_integration import _temporal_package

AS_OF = "2026-09-24T20:00:00Z"
QUESTION = "What approval quorum is required?"


class PortableVerifyOnlyTests(unittest.TestCase):
    def _run(self, root, *, registry=None, question=QUESTION, world_as_of=AS_OF,
             source_reverification=None, host="claude-code", tools=(), validity=None):
        package, real_registry, manifest, source = _temporal_package(Path(root), validity or {})
        value = verify(mode="VERIFY_ONLY", host=host, question=question,
                       registry_supplier=lambda: real_registry if registry is None else registry,
                       package_resolver=lambda _s, _v: str(package),
                       world_as_of=world_as_of, runtime_tools=tools,
                       source_reverification=source_reverification)
        return value, package, real_registry, manifest, source

    def test_off_never_calls_suppliers(self):
        def forbidden(*_args):
            raise AssertionError("OFF accessed callback")
        for host in ("claude-code", "codex-cli"):
            out = verify(mode="OFF", host=host, registry_supplier=forbidden,
                         package_resolver=forbidden, question=None, world_as_of=None)
            self.assertEqual(out["state"], "NO_FORGE_CALLS")
            self.assertEqual(out["verified"], [])
            self.assertEqual(out["reasoning_context"], [])

    def test_on_disabled_both_hosts(self):
        for host in ("claude-code", "codex-cli"):
            with self.assertRaises(PortableVerifyError):
                verify(mode="ON", host=host)

    def test_bad_host_and_mode(self):
        for mode, host in (("VERIFY_ONLY", "agent0"), ("DRY_RUN", "claude-code")):
            with self.assertRaises(PortableVerifyError):
                verify(mode=mode, host=host)

    def test_real_package_verified_but_never_injected_both_hosts(self):
        for host in ("claude-code", "codex-cli"):
            with self.subTest(host=host), TemporaryDirectory() as d:
                report, *_ = self._run(d, host=host)
                self.assertEqual(report["verified_skill_count"], 1)
                self.assertGreaterEqual(report["verified"][0]["verified_unit_count"], 1)
                self.assertFalse(report["registry_trust_attested"])
                self.assertFalse(report["trusted_clock_verified"])
                self.assertFalse(report["model_context_injected"])
                self.assertFalse(report["automatic_consumption_active"])
                self.assertEqual(report["reasoning_context"], [])
                self.assertEqual(report["tool_grants"], [])
                self.assertEqual(report["execution_authority"], "NONE")
                self.assertNotIn("approval quorum is exactly 7", json.dumps(report).lower())
                self.assertNotIn("The approval quorum", json.dumps(report))

    def test_stale_time_refuses(self):
        with TemporaryDirectory() as d, self.assertRaises(PortableVerifyError):
            self._run(d, world_as_of="2030-01-01T00:00:00Z",
                      validity={"valid_until":"2026-09-24T21:00:00Z"})

    def test_missing_or_naive_clock_refuses(self):
        for clk in (None, "2026-09-24", "2026-09-24T20:00:00"):
            with self.subTest(clk=clk), TemporaryDirectory() as d, self.assertRaises(PortableVerifyError):
                self._run(d, world_as_of=clk)

    def test_tampered_package_refuses(self):
        with TemporaryDirectory() as d:
            package, registry, manifest, _ = _temporal_package(Path(d), {})
            (package / "manifest.json").write_bytes(b'{"tampered":true}')
            with self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,
                       registry_supplier=lambda:registry,package_resolver=lambda *_:str(package),world_as_of=AS_OF)

    def test_source_changed_refuses(self):
        with TemporaryDirectory() as d:
            package, registry, manifest, source = _temporal_package(Path(d), {})
            with self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,
                       registry_supplier=lambda:registry,package_resolver=lambda *_:str(package),
                       world_as_of=AS_OF,
                       source_reverification={source:{"status":"CHANGED","checked_at":"2026-09-24T19:59:00Z","evidence_ref":"local:test"}})

    def test_source_unavailable_refuses(self):
        with TemporaryDirectory() as d:
            package, registry, manifest, source = _temporal_package(Path(d), {})
            with self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,
                       registry_supplier=lambda:registry,package_resolver=lambda *_:str(package),
                       world_as_of=AS_OF,
                       source_reverification={source:{"status":"UNAVAILABLE","checked_at":"2026-09-24T19:59:00Z","evidence_ref":"local:test"}})

    def test_unpromoted_registry_refuses(self):
        with TemporaryDirectory() as d, self.assertRaises(PortableVerifyError):
            self._run(d, registry=create_skill_registry(created_at="2026-09-24T19:00:00Z"))

    def test_bad_question_refuses(self):
        with TemporaryDirectory() as d, self.assertRaises(PortableVerifyError):
            self._run(d, question="unrelated zebra platypus")

    def test_missing_path_refuses(self):
        with TemporaryDirectory() as d:
            _, registry, manifest, _ = _temporal_package(Path(d), {})
            with self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,
                       registry_supplier=lambda:registry,package_resolver=lambda *_:"/not-present-package-nope",world_as_of=AS_OF)

    def test_tool_ceiling_is_not_widened(self):
        with TemporaryDirectory() as d:
            report, *_ = self._run(d, tools=("quote_only",))
            self.assertEqual(report["tool_grants"], [])
            self.assertNotIn("quote_only", json.dumps(report))

    def test_invalid_runtime_tools_refused(self):
        for value in ("shell", [""], [3], ["a"] * 33):
            with self.subTest(value=value), self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,
                       world_as_of=AS_OF,registry_supplier=lambda:None,
                       package_resolver=lambda *_:"/tmp",runtime_tools=value)

    def test_invalid_max_refused(self):
        for v in (0, 4, True, 1.5):
            with self.subTest(v=v), self.assertRaises(PortableVerifyError):
                verify(mode="VERIFY_ONLY",host="codex-cli",question=QUESTION,
                       world_as_of=AS_OF,registry_supplier=lambda:None,
                       package_resolver=lambda *_:"/tmp",max_skills=v)

    def test_bad_supplier_refuses_without_leaking(self):
        with self.assertRaises(PortableVerifyError) as ctx:
            verify(mode="VERIFY_ONLY",host="claude-code",question=QUESTION,world_as_of=AS_OF,
                   registry_supplier=lambda: (_ for _ in ()).throw(ValueError("SENSITIVE-SOURCE-BODY")),
                   package_resolver=lambda *_:"/tmp")
        self.assertNotIn("SENSITIVE",str(ctx.exception))

    def test_cli_off_does_not_touch_paths(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_verify_only","--mode","OFF",
            "--host","codex-cli","--registry","/absent","--packages-map","/absent"],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout)["verified_skill_count"],0)

    def test_cli_on_refuses(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_verify_only","--mode","ON",
            "--host","claude-code"],capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,2)
        self.assertIn("BLOCKED",run.stderr)

    def test_cli_verify_without_map_refuses(self):
        run=subprocess.run([sys.executable,"-m","knowledge_forge.portable_verify_only","--mode","VERIFY_ONLY",
            "--host","codex-cli","--question",QUESTION,"--world-as-of",AS_OF],
            capture_output=True,text=True,timeout=10)
        self.assertEqual(run.returncode,2)
        self.assertIn("verification refused",run.stderr)


if __name__=="__main__":
    unittest.main()
