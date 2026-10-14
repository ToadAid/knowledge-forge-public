"""P3D-P1: Claude/Codex portable OFF/DRY_RUN diagnostics, NEVER ON.

Caller-supplied config, paths and registry are not protected host truth.
No promotion, model-context injection, operator, or activation in this module.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping, Sequence

from .consumer_discovery import (
    ConsumerBinding,
    ConsumerDiscoveryError,
    build_consumer_discovery_catalog,
    route_consumer_knowledge,
)

_MODES = frozenset({"OFF", "DRY_RUN"})
_HOSTS = {
    "claude-code": ("ClaudeCode/cli", "terminal-session"),
    "codex-cli": ("OpenAI/CodexCLI", "terminal-session"),
}
_MAX_JSON = 4 * 1024 * 1024


class PortableHostPreflightError(ValueError):
    """No host access or knowledge injection is available for this request."""


def _result(*, mode: str, host: str, state: str, selected: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "surface": "PORTABLE_CLI_HOST_PREFLIGHT_NONAUTHORIZING",
        "host": host,
        "mode": mode,
        "state": state,
        "selected": selected,
        "reasoning_context": [],
        "knowledge_bodies_loaded": False,
        "model_context_injected": False,
        "automatic_consumption_active": False,
        "host_authentication_verified": False,
        "operator_invoked": False,
        "promotion_authorized": False,
        "registry_mutated": False,
        "execution_authority": "NONE",
        "instruction_authority": "NONE",
        "authority": "KNOWLEDGE_ONLY",
        "tool_grants": [],
    }


def _local_directory(path_value: str) -> None:
    """Diagnostic check only. NOT safe filesystem access or trusted mapping."""
    if not isinstance(path_value, str) or not path_value or "\x00" in path_value:
        raise PortableHostPreflightError("package location must be an absolute path")
    path = Path(path_value)
    if not path.is_absolute() or ".." in path.parts:
        raise PortableHostPreflightError("package location must be an absolute clean path")
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current = current / part
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise PortableHostPreflightError("package path traverses a symlink")
        if not stat.S_ISDIR(os.stat(path).st_mode):
            raise PortableHostPreflightError("package location is not a real directory")
    except OSError as exc:
        raise PortableHostPreflightError("package location is absent or inaccessible") from exc


def probe(
    *,
    mode: str,
    host: str,
    question: str | None = None,
    registry_supplier: Callable[[], Mapping[str, Any]] | None = None,
    package_resolver: Callable[[str, int], str] | None = None,
    max_skills: int = 1,
) -> dict[str, Any]:
    """OFF: zero external callbacks. DRY_RUN: metadata-only routing and path check.

    No ON mode, load-current call, knowledge bodies, subprocess, or host hook.
    A same-UID caller can falsify its source: results NEVER attest to truth.
    """
    if mode not in _MODES or host not in _HOSTS:
        raise PortableHostPreflightError("unsupported host or activation mode; ON is unavailable")
    if mode == "OFF":
        return _result(mode=mode, host=host, state="NO_FORGE_CALLS", selected=[])
    if not isinstance(question, str) or not question.strip():
        raise PortableHostPreflightError("DRY_RUN requires a nonempty question")
    if type(max_skills) is not int or not 1 <= max_skills <= 3:
        raise PortableHostPreflightError("max_skills must be an integer 1..3")
    if not callable(registry_supplier) or not callable(package_resolver):
        raise PortableHostPreflightError("DRY_RUN requires explicit source and package resolver")
    repo, entrypoint = _HOSTS[host]
    binding = ConsumerBinding(
        consumer_id=host,
        runtime_repo=repo,
        runtime_entrypoint=entrypoint,
        integration_boundary="UNTRUSTED_DRY_RUN_DIAGNOSTICS_ONLY",
        hard_max_skills_per_query=3,
    )
    try:
        registry = registry_supplier()
        catalog = build_consumer_discovery_catalog(registry, binding)
        routed = route_consumer_knowledge(catalog, binding, question, max_skills=max_skills)
        items: list[dict[str, Any]] = []
        for selected in routed["selected_skills"]:
            skill_id, version = selected["skill_id"], selected["skill_version"]
            path = package_resolver(skill_id, version)
            _local_directory(path)
            items.append({
                "skill_id": skill_id,
                "skill_version": version,
                "package_location_check": "DIRECTORY_EXISTS_ONLY_NOT_VALIDATED",
                "trusted_registry_verified": False,
                "promoted_package_revalidated": False,
            })
    except (ConsumerDiscoveryError, OSError, KeyError, TypeError, ValueError) as exc:
        raise PortableHostPreflightError("DRY_RUN refused: registry, route or package unavailable") from exc
    return _result(mode=mode, host=host, state="METADATA_ONLY_NO_INJECTION", selected=items)


def _read_json(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    if not path.is_absolute() or ".." in path.parts or path.is_symlink():
        raise PortableHostPreflightError("config file must be an absolute non-symlink path")
    try:
        if not path.is_file() or path.stat().st_size > _MAX_JSON:
            raise PortableHostPreflightError("config file absent or too large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as exc:
        raise PortableHostPreflightError("config file unreadable or malformed") from exc
    if not isinstance(value, dict):
        raise PortableHostPreflightError("config must be JSON object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m knowledge_forge.portable_host_preflight",
        description="OFF/DRY_RUN metadata-only Claude/Codex diagnostics; never ON",
    )
    parser.add_argument("--mode", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--question")
    parser.add_argument("--registry")
    parser.add_argument("--packages-map")
    parser.add_argument("--max-skills", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.mode == "OFF":
            result = probe(mode=args.mode, host=args.host)
        else:
            if not args.registry or not args.packages_map:
                raise PortableHostPreflightError("DRY_RUN requires --registry and --packages-map")
            def resolve(skill_id: str, version: int) -> str:
                mapping = _read_json(args.packages_map)
                if set(mapping) != {"schema_version", "packages"} or mapping["schema_version"] != "0.1":
                    raise PortableHostPreflightError("invalid mapping contract")
                packages = mapping.get("packages")
                if not isinstance(packages, dict):
                    raise PortableHostPreflightError("package mapping missing")
                entry = packages.get(skill_id)
                if not isinstance(entry, dict) or set(entry) != {"version", "path"}:
                    raise PortableHostPreflightError("package mapping missing exact identity")
                if type(entry["version"]) is not int or entry["version"] != version:
                    raise PortableHostPreflightError("package version mapping mismatch")
                return entry["path"]
            result = probe(
                mode=args.mode, host=args.host, question=args.question,
                registry_supplier=lambda: _read_json(args.registry),
                package_resolver=resolve, max_skills=args.max_skills,
            )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except PortableHostPreflightError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "activation": "BLOCKED",
            "authority": "KNOWLEDGE_ONLY", "execution_authority": "NONE",
            "promotion_authorized": False}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
