from __future__ import annotations

import ast
from contextlib import redirect_stderr
from copy import deepcopy
from io import StringIO
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

from scripts.build_consumer_capsule import (
    CAPSULE_FILENAME,
    MANIFEST_FILENAME,
    CapsuleBuildError,
    build_capsule,
    verify_capsule,
)


ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
FORBIDDEN_MODULES = {
    "operator_cli.py", "operator_drop.py", "operator_promotion.py",
    "authoring_cli.py", "agent_authoring.py", "repository.py", "cli.py",
}
FORBIDDEN_SYMBOLS = {
    "transition_lifecycle", "register_promoted_skill", "save_skill_registry",
    "fold_in_current_incident_lifecycle", "fold_in_promoted_version",
    "resolve_registry_conflict", "promote_validated_skill",
    "persist_operator_promotion",
}


@unittest.skipUnless(
    sys.implementation.name == "cpython" and sys.version_info[:2] == (3, 12),
    "consumer capsule builder is supported only on CPython 3.12",
)
class ConsumerCapsuleTests(unittest.TestCase):
    def _source_copy(self, parent: Path, name: str) -> Path:
        target = parent / name
        target.mkdir()
        shutil.copytree(ROOT / "src", target / "src")
        (target / "packaging/consumer_capsule").mkdir(parents=True)
        shutil.copy2(
            ROOT / "packaging/consumer_capsule/entrypoint.py",
            target / "packaging/consumer_capsule/entrypoint.py",
        )
        (target / "scripts").mkdir()
        shutil.copy2(
            ROOT / "scripts/build_consumer_capsule.py",
            target / "scripts/build_consumer_capsule.py",
        )
        return target

    def _build_pair(self, root: Path):
        first_source = self._source_copy(root, "source-a")
        second_source = self._source_copy(root, "source-b")
        first = root / "build-a"
        second = root / "build-b"
        build_capsule(first_source, first, REVISION)
        build_capsule(second_source, second, REVISION)
        return first, second

    def test_independent_builds_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = self._build_pair(Path(tmp))
            self.assertEqual(
                (first / CAPSULE_FILENAME).read_bytes(),
                (second / CAPSULE_FILENAME).read_bytes(),
            )
            self.assertEqual(
                (first / MANIFEST_FILENAME).read_bytes(),
                (second / MANIFEST_FILENAME).read_bytes(),
            )

    def test_manifest_digest_and_order_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = self._build_pair(Path(tmp))
            one = json.loads((first / MANIFEST_FILENAME).read_text())
            two = json.loads((second / MANIFEST_FILENAME).read_text())
            self.assertEqual(one, two)
            paths = [item["path"] for item in one["archive_files"]]
            self.assertEqual(paths, sorted(paths))
            self.assertEqual(one["build_environment"]["external_dependencies"], [])
            self.assertEqual(one["build_environment"]["platform"], "linux")
            self.assertEqual(one["build_environment"]["architecture"], "x86_64")
            self.assertRegex(one["build_environment"]["builder_sha256"], r"^sha256:[0-9a-f]{64}$")
            self.assertRegex(one["build_environment"]["python_executable_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_changed_capsule_bytes_refuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, manifest = build_capsule(source, output, REVISION)
            capsule.write_bytes(capsule.read_bytes() + b"tamper")
            with self.assertRaisesRegex(CapsuleBuildError, "byte digest"):
                verify_capsule(capsule, manifest, expected_source_revision=REVISION, expected_python_abi="cp312")

    def test_unexpected_module_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, manifest = build_capsule(source, output, REVISION)
            with zipfile.ZipFile(capsule, "a", compression=zipfile.ZIP_STORED) as archive:
                archive.writestr("knowledge_forge/operator_cli.py", "raise SystemExit(1)\n")
            data = json.loads(manifest.read_text())
            from hashlib import sha256
            data["capsule_sha256"] = "sha256:" + sha256(capsule.read_bytes()).hexdigest()
            manifest.write_text(json.dumps(data, sort_keys=True, indent=2) + "\n")
            with self.assertRaisesRegex(CapsuleBuildError, "entries mismatch"):
                verify_capsule(capsule, manifest, expected_source_revision=REVISION, expected_python_abi="cp312")

    def test_consumer_boundary_excludes_privileged_modules_and_symbols(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, _ = build_capsule(source, output, REVISION)
            with zipfile.ZipFile(capsule) as archive:
                names = archive.namelist()
                self.assertFalse(any(Path(name).name in FORBIDDEN_MODULES for name in names))
                discovered = set()
                for name in names:
                    if not name.endswith(".py"):
                        continue
                    tree = ast.parse(archive.read(name).decode("utf-8"), filename=name)
                    discovered.update(
                        node.name
                        for node in tree.body
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                    )
                self.assertEqual(discovered & FORBIDDEN_SYMBOLS, set())

    def test_capsule_imports_only_selected_forge_closure_and_standard_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, _ = build_capsule(source, output, REVISION)
            with zipfile.ZipFile(capsule) as archive:
                module_names = {
                    name.removesuffix(".py").replace("/", ".")
                    for name in archive.namelist()
                    if name.startswith("knowledge_forge/") and name.endswith(".py")
                }
                for name in archive.namelist():
                    if not name.endswith(".py"):
                        continue
                    tree = ast.parse(archive.read(name).decode("utf-8"), filename=name)
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Import):
                            self.assertFalse(any(alias.name.startswith("knowledge_forge.") and alias.name not in module_names for alias in node.names))
                            self.assertFalse(any(alias.name in {"subprocess", "socket", "ctypes"} for alias in node.names))
                        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                            self.assertFalse(node.module.startswith("knowledge_forge.") and node.module not in module_names)
                            self.assertNotIn(node.module, {"subprocess", "socket", "ctypes"})

    def test_entrypoint_refuses_unknown_subcommand_before_registry_read(self):
        spec = importlib.util.spec_from_file_location(
            "consumer_capsule_entrypoint",
            ROOT / "packaging/consumer_capsule/entrypoint.py",
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        stderr = StringIO()
        spec.loader.exec_module(module)
        with redirect_stderr(stderr):
            result = module.main(["promote", "/tmp/registry.json"])
        self.assertEqual(result, 2)
        self.assertIn("only fixed descriptor-bound index-build", stderr.getvalue())

    def test_wrong_python_abi_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, manifest = build_capsule(source, output, REVISION)
            with self.assertRaisesRegex(CapsuleBuildError, "ABI mismatch"):
                verify_capsule(capsule, manifest, expected_source_revision=REVISION, expected_python_abi="cp313")

    def test_wrong_source_revision_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self._source_copy(Path(tmp), "source")
            output = Path(tmp) / "build"
            capsule, manifest = build_capsule(source, output, REVISION)
            with self.assertRaisesRegex(CapsuleBuildError, "source revision mismatch"):
                verify_capsule(capsule, manifest, expected_source_revision="0" * 40, expected_python_abi="cp312")


if __name__ == "__main__":
    unittest.main()
