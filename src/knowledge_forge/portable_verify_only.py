"""P3D-P2-P0: disposable OFF/VERIFY_ONLY governed package preflight.

No agent hooks, ON, operator, approval, registry persistence, or context injection.
Registry, package paths, and clock come from the *caller*, not an attested host.
Verification means consistency with these inputs, NOT independently trusted truth.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any, Callable, Mapping, Sequence

from .portable_host_preflight import _local_directory, _read_json
from .consumer_discovery import (
    ConsumerBinding, build_consumer_discovery_catalog, route_consumer_knowledge,
)
from .temporal_integration import load_current_skill_context

_HOSTS = {
    "claude-code": ("ClaudeCode/cli", "terminal-session"),
    "codex-cli": ("OpenAI/CodexCLI", "terminal-session"),
}


class PortableVerifyError(ValueError):
    """Fail-closed non-authorizing verification refusal."""


def _report(host: str, mode: str, state: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "surface": "PORTABLE_GOVERNED_VERIFY_ONLY_NOT_ACTIVATION",
        "host": host, "mode": mode, "state": state,
        "verified": items, "verified_skill_count": len(items),
        "reasoning_context": [], "model_context_injected": False,
        "automatic_consumption_active": False,
        "human_authenticated": False, "host_isolation_verified": False,
        "registry_trust_attested": False, "trusted_clock_verified": False,
        "operator_invoked": False, "promotion_authorized": False,
        "registry_mutated": False, "execution_authority": "NONE",
        "instruction_authority": "NONE", "authority": "KNOWLEDGE_ONLY",
        "tool_grants": [], "verification_scope": "CALLER_SUPPLIED_INPUTS_NOT_HOST_ATTESTED",
    }


def _utc_time(value: Any) -> None:
    if not isinstance(value, str) or "T" not in value:
        raise PortableVerifyError("VERIFY_ONLY requires explicit timezone-aware world_as_of")
    try:
        dt = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except ValueError as exc:
        raise PortableVerifyError("invalid world_as_of timestamp") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise PortableVerifyError("world_as_of must be timezone-aware")


def verify(
    *, mode: str, host: str, question: str | None = None,
    registry_supplier: Callable[[], Mapping[str, Any]] | None = None,
    package_resolver: Callable[[str, int], str] | None = None,
    world_as_of: str | None = None,
    runtime_tools: Sequence[str] = (),
    source_reverification: Mapping[str, Any] | None = None,
    max_skills: int = 1, max_units: int = 1,
) -> dict[str, Any]:
    """OFF: zero callbacks. VERIFY_ONLY: validate with the existing trust wall.

    Context is read *inside this process* but NEVER returned or injected.
    Arbitrary caller callbacks are NOT sandboxed, nor independent host proof.
    """
    if host not in _HOSTS or mode not in {"OFF", "VERIFY_ONLY"}:
        raise PortableVerifyError("host/mode refused: ON is not implemented")
    if mode == "OFF":
        return _report(host, mode, "NO_FORGE_CALLS", [])
    if not isinstance(question, str) or not question.strip():
        raise PortableVerifyError("VERIFY_ONLY requires a question")
    _utc_time(world_as_of)
    if type(max_skills) is not int or not 1 <= max_skills <= 3:
        raise PortableVerifyError("max_skills must be 1..3")
    if type(max_units) is not int or not 1 <= max_units <= 3:
        raise PortableVerifyError("max_units must be 1..3")
    if not isinstance(runtime_tools, (list, tuple)) or len(runtime_tools) > 32 or any(
        not isinstance(t, str) or not t or len(t) > 128 for t in runtime_tools
    ):
        raise PortableVerifyError("runtime_tools must be a bounded sequence of names")
    if not callable(registry_supplier) or not callable(package_resolver):
        raise PortableVerifyError("registry and package suppliers required")
    try:
        registry = registry_supplier()
        repo, entry = _HOSTS[host]
        binding = ConsumerBinding(
            consumer_id=host, runtime_repo=repo, runtime_entrypoint=entry,
            integration_boundary="CALLER_SUPPLIED_VERIFY_ONLY_NO_AUTHORITY",
        )
        selected = route_consumer_knowledge(
            build_consumer_discovery_catalog(registry, binding),
            binding, question, max_skills=max_skills,
        )["selected_skills"]
        reports: list[dict[str, Any]] = []
        for item in selected:
            skill_id, version = item["skill_id"], item["skill_version"]
            path = package_resolver(skill_id, version)
            _local_directory(path)  # Diagnostic only; no race-safe filesystem ownership claim.
            # This call DOES read bodies into process memory for verification.
            loaded = load_current_skill_context(
                path, registry, skill_id=skill_id, question=question,
                runtime_tools=list(runtime_tools), world_as_of=world_as_of,
                source_reverification=source_reverification,
                max_units=max_units,
            )
            if any(loaded.get(k) != v for k, v in (
                ("trust_class", "UNTRUSTED_KNOWLEDGE_DATA"),
                ("authority", "KNOWLEDGE_ONLY"),
                ("instruction_authority", "NONE"),
                ("execution_authority", "NONE"),
            )):
                raise PortableVerifyError("loaded knowledge labels invalid")
            contexts = loaded.get("reasoning_context")
            evidence = loaded.get("load_evidence")
            temporal = loaded.get("temporal_evidence")
            if (not isinstance(contexts, list) or not 1 <= len(contexts) <= max_units
                    or not isinstance(evidence, dict) or not isinstance(temporal, dict)
                    or temporal.get("current_use_allowed") is not True
                    or evidence.get("skill_id") != skill_id
                    or evidence.get("skill_version") != version
                    or evidence.get("loaded_unit_ids") != [c.get("unit_id") for c in contexts]):
                raise PortableVerifyError("load evidence or temporal state invalid")
            if any(not isinstance(c, dict) or any(c.get(k) != v for k, v in (
                ("trust_class", "UNTRUSTED_KNOWLEDGE_DATA"),
                ("authority", "KNOWLEDGE_ONLY"),
                ("instruction_authority", "NONE"),
                ("execution_authority", "NONE"),
                ("executable", False),
            )) for c in contexts):
                raise PortableVerifyError("unit authority boundary invalid")
            if not set(loaded.get("effective_tools", [])) <= set(runtime_tools):
                raise PortableVerifyError("tool ceiling widened")
            # Closed projection. Never copy arbitrary strings, unit bodies or sources.
            reports.append({
                "skill_id": skill_id, "skill_version": version,
                "verified_unit_count": len(contexts),
                "artifact_binding_checked": True,
                "temporal_check_passed_relative_to_caller_clock": True,
                "registry_trust_attested": False,
                "model_context_injected": False,
            })
    except Exception as exc:
        # Never include source, filesystem, unit bodies or exception detail in CLI output.
        raise PortableVerifyError("VERIFY_ONLY refused: registry, package, route or temporal check failed") from exc
    return _report(host, mode, "VERIFIED_RELATIVE_TO_CALLER_INPUTS_NO_INJECTION", reports)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m knowledge_forge.portable_verify_only",
        description="OFF/VERIFY_ONLY metadata-only result; no retrieval activation",
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--question")
    parser.add_argument("--world-as-of")
    parser.add_argument("--registry")
    parser.add_argument("--packages-map")
    parser.add_argument("--max-skills", type=int, default=1)
    parser.add_argument("--max-units", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        if args.mode == "OFF":
            result = verify(mode="OFF", host=args.host)
        else:
            if not args.registry or not args.packages_map:
                raise PortableVerifyError("VERIFY_ONLY requires registry and packages map")
            def resolve(skill_id: str, version: int) -> str:
                mapping = _read_json(args.packages_map)
                if set(mapping) != {"schema_version", "packages"} or mapping["schema_version"] != "0.1":
                    raise PortableVerifyError("packages map contract invalid")
                packages = mapping.get("packages")
                v = packages.get(skill_id) if isinstance(packages, dict) else None
                if not isinstance(v, dict) or set(v) != {"version", "path"} or type(v["version"]) is not int or v["version"] != version:
                    raise PortableVerifyError("exact package mapping missing")
                return v["path"]
            result = verify(
                mode=args.mode, host=args.host, question=args.question,
                world_as_of=args.world_as_of,
                registry_supplier=lambda: _read_json(args.registry),
                package_resolver=resolve,
                max_skills=args.max_skills, max_units=args.max_units,
            )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    except PortableVerifyError:
        print(json.dumps({"ok": False, "activation": "BLOCKED", "error": "verification refused",
            "promotion_authorized": False, "execution_authority": "NONE"}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
