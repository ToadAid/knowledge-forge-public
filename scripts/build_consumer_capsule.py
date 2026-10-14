#!/usr/bin/env python3
"""Build and verify the deterministic consumer-only Knowledge Forge capsule.

This packaging tool is deliberately outside ``src/knowledge_forge``. It reads
the sealed sources, computes the exact symbol dependency closure of
``skill_index.build_skill_index``, and emits only that closure. It never imports
the Forge package and never modifies the source tree.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from hashlib import sha256
import json
import platform
from pathlib import Path
import re
import sys
from typing import Any, Iterable, Mapping
import zipfile


CAPSULE_SCHEMA_VERSION = "knowledge-forge.consumer-capsule.v1"
CAPSULE_MANIFEST_SCHEMA_VERSION = "knowledge-forge.consumer-capsule-manifest.v1"
CAPSULE_FORMAT = "PYTHON_ZIPAPP_STORED_V1"
CAPSULE_FILENAME = "knowledge-forge-consumer.pyz"
MANIFEST_FILENAME = "knowledge-forge-consumer.manifest.json"
PYTHON_IMPLEMENTATION = "CPython"
PYTHON_MAJOR_MINOR = "3.12"
PYTHON_ABI = "cp312"
ENTRYPOINT_SOURCE = Path("packaging/consumer_capsule/entrypoint.py")
ROOT_MODULE = "knowledge_forge.skill_index"
ROOT_SYMBOL = "build_skill_index"
FIXED_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")

FORBIDDEN_MODULE_PARTS = frozenset({
    "operator_cli",
    "operator_drop",
    "operator_promotion",
    "authoring_cli",
    "agent_authoring",
    "repository",
})
FORBIDDEN_TOP_LEVEL_SYMBOLS = frozenset({
    "transition_lifecycle",
    "register_promoted_skill",
    "save_skill_registry",
    "fold_in_current_incident_lifecycle",
    "fold_in_promoted_version",
    "resolve_registry_conflict",
    "promote_validated_skill",
    "persist_operator_promotion",
    "main_operator",
})


class CapsuleBuildError(ValueError):
    """Raised when the consumer boundary cannot remain exact and deterministic."""


@dataclass(frozen=True)
class Definition:
    module: str
    name: str
    node: ast.AST


@dataclass(frozen=True)
class ModuleSource:
    name: str
    path: Path
    text: str
    tree: ast.Module
    definitions: Mapping[str, Definition]


def _sha256_ref(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _pretty_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, ast.Assign):
        return tuple(
            target.id for target in node.targets if isinstance(target, ast.Name)
        )
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return (node.target.id,)
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return tuple(alias.asname or alias.name.split(".")[0] for alias in node.names)
    return ()


def _module_path(source_root: Path, module: str) -> Path:
    parts = module.split(".")
    return source_root.joinpath("src", *parts).with_suffix(".py")


def _load_module(source_root: Path, module: str) -> ModuleSource:
    if not module.startswith("knowledge_forge."):
        raise CapsuleBuildError(f"external module cannot enter capsule: {module}")
    if any(part in FORBIDDEN_MODULE_PARTS for part in module.split(".")):
        raise CapsuleBuildError(f"forbidden module requested: {module}")
    path = _module_path(source_root, module)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapsuleBuildError(f"required source unavailable: {path}") from exc
    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError as exc:
        raise CapsuleBuildError(f"required source is not valid Python: {path}") from exc
    definitions: dict[str, Definition] = {}
    for node in tree.body:
        for name in _bound_names(node):
            if name in definitions:
                raise CapsuleBuildError(f"ambiguous top-level symbol: {module}.{name}")
            definitions[name] = Definition(module, name, node)
    return ModuleSource(module, path, text, tree, definitions)


def _resolve_relative_module(current: str, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return None
    package = current.rsplit(".", 1)[0].split(".")
    keep = len(package) - (node.level - 1)
    if keep < 1:
        raise CapsuleBuildError("relative import escapes knowledge_forge")
    prefix = ".".join(package[:keep])
    return prefix + (f".{node.module}" if node.module else "")


def _loaded_names(node: ast.AST) -> set[str]:
    return {
        child.id
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def _selected_import(node: ast.Import | ast.ImportFrom, names: set[str]) -> str:
    aliases = [
        alias
        for alias in node.names
        if (alias.asname or alias.name.split(".")[0]) in names
    ]
    if not aliases:
        raise CapsuleBuildError("selected import has no aliases")
    if isinstance(node, ast.Import):
        selected: ast.stmt = ast.Import(names=aliases)
    else:
        selected = ast.ImportFrom(module=node.module, names=aliases, level=node.level)
    return ast.unparse(ast.fix_missing_locations(selected))


def _source_segment(module: ModuleSource, node: ast.AST) -> str:
    segment = ast.get_source_segment(module.text, node)
    if segment is None:
        raise CapsuleBuildError(f"cannot preserve source segment: {module.name}")
    return segment.rstrip() + "\n"


def compute_symbol_closure(source_root: Path) -> dict[str, bytes]:
    modules: dict[str, ModuleSource] = {}
    selected_nodes: dict[str, set[int]] = {}
    selected_import_names: dict[tuple[str, int], set[str]] = {}
    queue: list[tuple[str, str]] = [(ROOT_MODULE, ROOT_SYMBOL)]
    visited: set[tuple[str, str]] = set()

    def module_source(name: str) -> ModuleSource:
        if name not in modules:
            modules[name] = _load_module(source_root, name)
        return modules[name]

    while queue:
        module_name, symbol = queue.pop()
        key = (module_name, symbol)
        if key in visited:
            continue
        visited.add(key)
        module = module_source(module_name)
        definition = module.definitions.get(symbol)
        if definition is None:
            raise CapsuleBuildError(f"unresolved local symbol: {module_name}.{symbol}")
        node = definition.node
        selected_nodes.setdefault(module_name, set()).add(id(node))

        if isinstance(node, ast.ImportFrom):
            selected_import_names.setdefault((module_name, id(node)), set()).add(symbol)
            target = _resolve_relative_module(module_name, node)
            if target is not None:
                alias = next(
                    item
                    for item in node.names
                    if (item.asname or item.name) == symbol
                )
                queue.append((target, alias.name))
            continue
        if isinstance(node, ast.Import):
            selected_import_names.setdefault((module_name, id(node)), set()).add(symbol)
            continue

        for name in _loaded_names(node):
            if name in module.definitions:
                queue.append((module_name, name))

    rendered: dict[str, bytes] = {}
    for module_name in sorted(selected_nodes):
        module = modules[module_name]
        lines = ["from __future__ import annotations\n"]
        for node in module.tree.body:
            if id(node) not in selected_nodes[module_name]:
                continue
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                    continue
                names = selected_import_names.get((module_name, id(node)), set())
                lines.append(_selected_import(node, names) + "\n")
            else:
                lines.append("\n" + _source_segment(module, node))
        payload = "".join(lines).rstrip() + "\n"
        tree = ast.parse(payload, filename=module_name)
        top_level = {
            name
            for node in tree.body
            for name in _bound_names(node)
        }
        denied = sorted(top_level & FORBIDDEN_TOP_LEVEL_SYMBOLS)
        if denied:
            raise CapsuleBuildError(
                f"privileged top-level symbols entered {module_name}: {denied}"
            )
        archive_name = module_name.replace(".", "/") + ".py"
        rendered[archive_name] = payload.encode("utf-8")

    if "knowledge_forge/skill_index.py" not in rendered:
        raise CapsuleBuildError("skill index root missing from closure")
    return rendered


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ARCHIVE_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.flag_bits = 0
    return info


def _identity(source_revision: str, files: Mapping[str, bytes]) -> dict[str, Any]:
    ordered = [
        {"path": name, "sha256": _sha256_ref(files[name]), "mode": "0644"}
        for name in sorted(files)
    ]
    return {
        "schema_version": CAPSULE_SCHEMA_VERSION,
        "capsule_format": CAPSULE_FORMAT,
        "source_repository": "ToadAid/knowledge-forge",
        "source_revision": source_revision,
        "operation": "index-build",
        "entrypoint": "__main__.py",
        "registry_descriptor_path": "/proc/self/fd/4",
        "python_implementation": PYTHON_IMPLEMENTATION,
        "python_major_minor": PYTHON_MAJOR_MINOR,
        "python_abi": PYTHON_ABI,
        "archive_timestamp": "1980-01-01T00:00:00Z",
        "archive_compression": "STORED",
        "authority": "KNOWLEDGE_ONLY",
        "instruction_authority": "NONE",
        "execution_authority": "NONE",
        "tool_grants": [],
        "included_files": ordered,
    }


def build_capsule(
    source_root: Path,
    output_dir: Path,
    source_revision: str,
) -> tuple[Path, Path]:
    source_root = source_root.resolve()
    if not REVISION.fullmatch(source_revision):
        raise CapsuleBuildError("source revision must be exactly 40 lowercase hex")
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise CapsuleBuildError("capsule builder requires CPython 3.12")
    if sys.platform != "linux" or platform.machine().lower() not in {"x86_64", "amd64"}:
        raise CapsuleBuildError("capsule builder requires Linux x86_64")

    entrypoint = (source_root / ENTRYPOINT_SOURCE).read_bytes()
    ast.parse(entrypoint.decode("utf-8"), filename=str(ENTRYPOINT_SOURCE))
    package_init = (source_root / "src/knowledge_forge/__init__.py").read_bytes()
    files: dict[str, bytes] = {
        "__main__.py": entrypoint,
        "knowledge_forge/__init__.py": package_init,
        **compute_symbol_closure(source_root),
    }
    identity = _identity(source_revision, files)
    files["CAPSULE-IDENTITY.json"] = _pretty_json_bytes(identity)

    output_dir.mkdir(parents=True, exist_ok=True)
    capsule_path = output_dir / CAPSULE_FILENAME
    with zipfile.ZipFile(capsule_path, "w", allowZip64=False) as archive:
        archive.comment = b""
        for name in sorted(files):
            archive.writestr(_zip_info(name), files[name])

    capsule_bytes = capsule_path.read_bytes()
    archive_files = [
        {"path": name, "sha256": _sha256_ref(files[name]), "mode": "0644"}
        for name in sorted(files)
    ]
    builder_source = (source_root / "scripts/build_consumer_capsule.py").read_bytes()
    python_executable = Path(sys.executable).resolve()
    manifest_without_digest = {
        "schema_version": CAPSULE_MANIFEST_SCHEMA_VERSION,
        "capsule_format": CAPSULE_FORMAT,
        "capsule_filename": CAPSULE_FILENAME,
        "capsule_sha256": _sha256_ref(capsule_bytes),
        "source_repository": "ToadAid/knowledge-forge",
        "source_revision": source_revision,
        "entrypoint": "__main__.py",
        "entrypoint_sha256": _sha256_ref(entrypoint),
        "python_implementation": PYTHON_IMPLEMENTATION,
        "python_major_minor": PYTHON_MAJOR_MINOR,
        "python_abi": PYTHON_ABI,
        "build_environment": {
            "builder": CAPSULE_MANIFEST_SCHEMA_VERSION,
            "builder_sha256": _sha256_ref(builder_source),
            "platform": "linux",
            "architecture": "x86_64",
            "python_implementation": PYTHON_IMPLEMENTATION,
            "python_major_minor": PYTHON_MAJOR_MINOR,
            "python_version": platform.python_version(),
            "python_abi": PYTHON_ABI,
            "python_cache_tag": sys.implementation.cache_tag,
            "python_executable_sha256": _sha256_ref(python_executable.read_bytes()),
            "external_dependencies": [],
        },
        "archive_files": archive_files,
    }
    manifest = {
        **manifest_without_digest,
        "file_manifest_sha256": _sha256_ref(
            _canonical_json_bytes(archive_files)
        ),
    }
    manifest_path = output_dir / MANIFEST_FILENAME
    manifest_path.write_bytes(_pretty_json_bytes(manifest))
    verify_capsule(
        capsule_path,
        manifest_path,
        expected_source_revision=source_revision,
        expected_python_abi=PYTHON_ABI,
    )
    return capsule_path, manifest_path


def verify_capsule(
    capsule_path: Path,
    manifest_path: Path,
    *,
    expected_source_revision: str,
    expected_python_abi: str,
) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CapsuleBuildError("capsule manifest is unreadable") from exc
    expected_fields = {
        "schema_version", "capsule_format", "capsule_filename", "capsule_sha256",
        "source_repository", "source_revision", "entrypoint", "entrypoint_sha256",
        "python_implementation", "python_major_minor", "python_abi",
        "build_environment", "archive_files", "file_manifest_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != expected_fields:
        raise CapsuleBuildError("capsule manifest fields mismatch")
    if manifest["schema_version"] != CAPSULE_MANIFEST_SCHEMA_VERSION:
        raise CapsuleBuildError("capsule manifest schema mismatch")
    if manifest["capsule_format"] != CAPSULE_FORMAT:
        raise CapsuleBuildError("capsule format mismatch")
    if manifest["source_revision"] != expected_source_revision:
        raise CapsuleBuildError("capsule source revision mismatch")
    if manifest["python_abi"] != expected_python_abi:
        raise CapsuleBuildError("capsule Python ABI mismatch")
    build_environment = manifest.get("build_environment")
    expected_build_fields = {
        "builder", "builder_sha256", "platform", "architecture",
        "python_implementation", "python_major_minor", "python_version",
        "python_abi", "python_cache_tag", "python_executable_sha256",
        "external_dependencies",
    }
    if not isinstance(build_environment, dict) or set(build_environment) != expected_build_fields:
        raise CapsuleBuildError("capsule build environment identity mismatch")
    if (
        build_environment["builder"] != CAPSULE_MANIFEST_SCHEMA_VERSION
        or build_environment["platform"] != "linux"
        or build_environment["architecture"] != "x86_64"
        or build_environment["python_implementation"] != PYTHON_IMPLEMENTATION
        or build_environment["python_major_minor"] != PYTHON_MAJOR_MINOR
        or build_environment["python_abi"] != expected_python_abi
        or build_environment["external_dependencies"] != []
        or not SHA256_REF.fullmatch(str(build_environment["builder_sha256"]))
        or not SHA256_REF.fullmatch(str(build_environment["python_executable_sha256"]))
    ):
        raise CapsuleBuildError("capsule build environment identity invalid")
    capsule_bytes = capsule_path.read_bytes()
    if manifest["capsule_sha256"] != _sha256_ref(capsule_bytes):
        raise CapsuleBuildError("capsule byte digest mismatch")
    archive_files = manifest.get("archive_files")
    if not isinstance(archive_files, list):
        raise CapsuleBuildError("capsule archive file manifest invalid")
    if manifest["file_manifest_sha256"] != _sha256_ref(
        _canonical_json_bytes(archive_files)
    ):
        raise CapsuleBuildError("capsule file manifest digest mismatch")
    expected_names = [item.get("path") for item in archive_files if isinstance(item, dict)]
    if expected_names != sorted(expected_names) or len(set(expected_names)) != len(expected_names):
        raise CapsuleBuildError("capsule archive manifest ordering invalid")

    try:
        with zipfile.ZipFile(capsule_path, "r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != expected_names:
                raise CapsuleBuildError("capsule archive entries mismatch")
            for info, record in zip(infos, archive_files, strict=True):
                if info.date_time != FIXED_ARCHIVE_TIMESTAMP:
                    raise CapsuleBuildError("capsule timestamp is not normalized")
                if info.compress_type != zipfile.ZIP_STORED:
                    raise CapsuleBuildError("capsule compression mismatch")
                if (info.external_attr >> 16) & 0o7777 != 0o644:
                    raise CapsuleBuildError("capsule permission metadata mismatch")
                payload = archive.read(info.filename)
                if record.get("sha256") != _sha256_ref(payload):
                    raise CapsuleBuildError("capsule entry digest mismatch")
                if any(part in FORBIDDEN_MODULE_PARTS for part in Path(info.filename).parts):
                    raise CapsuleBuildError("forbidden module present in capsule")
            identity = json.loads(archive.read("CAPSULE-IDENTITY.json"))
    except (OSError, KeyError, zipfile.BadZipFile, json.JSONDecodeError) as exc:
        raise CapsuleBuildError("capsule archive is invalid") from exc
    if identity.get("source_revision") != expected_source_revision:
        raise CapsuleBuildError("embedded capsule identity mismatch")
    if identity.get("operation") != "index-build":
        raise CapsuleBuildError("capsule operation widened")
    if identity.get("tool_grants") != []:
        raise CapsuleBuildError("capsule tool grants widened")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        capsule, manifest = build_capsule(
            args.source_root, args.output_dir, args.source_revision
        )
    except (CapsuleBuildError, OSError) as exc:
        parser.error(str(exc))
    print(capsule)
    print(manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
