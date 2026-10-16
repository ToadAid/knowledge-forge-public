"""Read-only product CLI for ToadAid Knowledge Forge.

P5A intentionally exposes governed inspection, validation, discovery, and
current-use loading. It does not expose promotion, incident mutation, contract
writes, signing, spending, deployment, or any other authority-bearing action.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .consumer_discovery import (
    ConsumerDiscoveryError,
    build_discovery_descriptors,
    route_discovery_descriptors,
)
from .personal_library import (
    PERSONAL_KNOWLEDGE_MODE,
    PersonalLibraryError,
    add_personal_entry,
    default_library_path,
    list_personal_entries,
    lookup_personal,
    remove_personal_entry,
)
from .registry import SkillRegistryError, validate_skill_registry
from .skill_index import (
    SkillIndexError,
    build_skill_index,
    list_skill_index,
    load_skill_index,
    route_skill_index,
    verify_skill_index_current,
)
from .temporal_integration import (
    TemporalIntegrationError,
    load_current_skill_context,
)
from .validate import validate_skill_package

CLI_SCHEMA_VERSION = "0.1"
CLI_CONSUMER_ID = "knowledge-forge-cli"
MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class KnowledgeForgeCliError(ValueError):
    """Raised when CLI input cannot be safely projected into core APIs."""


def _emit(payload: Mapping[str, Any], *, stream: Any = None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(
        json.dumps(
            dict(payload),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


def _load_json_mapping(path_value: str, label: str) -> dict[str, Any]:
    path = Path(path_value)
    if path.is_symlink():
        raise KnowledgeForgeCliError(f"{label} may not be a symlink")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise KnowledgeForgeCliError(f"{label} is unreadable: {path}") from exc
    except json.JSONDecodeError as exc:
        raise KnowledgeForgeCliError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise KnowledgeForgeCliError(f"{label} must contain a JSON object")
    return data


def _snapshot_package(root_value: str) -> dict[str, bytes]:
    root = Path(root_value)
    if root.is_symlink():
        raise KnowledgeForgeCliError("package root may not be a symlink")
    if not root.is_dir():
        raise KnowledgeForgeCliError("package root must be a directory")

    files: dict[str, bytes] = {}
    total_bytes = 0
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise KnowledgeForgeCliError(
                f"package artifact may not be a symlink: {path.relative_to(root)}"
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise KnowledgeForgeCliError(
                f"package artifact is unreadable: {relative}"
            ) from exc
        files[relative] = payload
        total_bytes += len(payload)
        if len(files) > MAX_PACKAGE_FILES:
            raise KnowledgeForgeCliError(
                f"package exceeds {MAX_PACKAGE_FILES} file limit"
            )
        if total_bytes > MAX_PACKAGE_BYTES:
            raise KnowledgeForgeCliError(
                f"package exceeds {MAX_PACKAGE_BYTES} byte limit"
            )

    if not files:
        raise KnowledgeForgeCliError("package contains no regular files")
    return files


def _validated_registry(path_value: str) -> dict[str, Any]:
    registry = _load_json_mapping(path_value, "registry")
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise KnowledgeForgeCliError(
            "registry validation failed: " + "; ".join(report["errors"])
        )
    return registry


def _current_registry_summary(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    skills = registry.get("skills")
    if not isinstance(skills, Mapping):
        raise KnowledgeForgeCliError("registry skills must be a mapping")

    result: list[dict[str, Any]] = []
    for skill_id in sorted(skills):
        record = skills[skill_id]
        if not isinstance(record, Mapping):
            raise KnowledgeForgeCliError(
                f"registry skill record must be a mapping: {skill_id}"
            )
        current_version = record.get("current_version")
        versions = record.get("versions")
        entry = (
            versions.get(str(current_version))
            if isinstance(versions, Mapping)
            else None
        )
        if not isinstance(entry, Mapping):
            raise KnowledgeForgeCliError(
                f"registry current version missing: {skill_id}"
            )
        result.append({
            "skill_id": skill_id,
            "skill_version": current_version,
            "state": entry.get("state"),
            "title": entry.get("title"),
            "knowledge_id": entry.get("knowledge_id"),
            "authority": entry.get("authority"),
            "package_receipt_sha256": entry.get("package_receipt_sha256"),
            "lifecycle_head_sha256": entry.get("lifecycle_head_sha256"),
        })
    return result


def _command_validate_package(args: argparse.Namespace) -> int:
    files = _snapshot_package(args.package_root)
    report = validate_skill_package(files)
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "validate-package",
        "package_root": str(Path(args.package_root)),
        "artifact_count": len(files),
        "valid": report["valid"],
        "errors": report["errors"],
        "findings": report["security_findings"],
    }
    _emit(payload)
    return 0 if report["valid"] else 2


def _command_registry_inspect(args: argparse.Namespace) -> int:
    registry = _validated_registry(args.registry)
    current = _current_registry_summary(registry)
    conflicts = registry.get("conflict_sets")
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "registry-inspect",
        "registry": str(Path(args.registry)),
        "valid": True,
        "skill_count": len(current),
        "conflict_count": len(conflicts) if isinstance(conflicts, list) else 0,
        "current_skills": current,
    }
    _emit(payload)
    return 0


def _command_discover(args: argparse.Namespace) -> int:
    registry = _validated_registry(args.registry)
    descriptors = build_discovery_descriptors(registry)
    matches = route_discovery_descriptors(
        descriptors,
        args.question,
        consumer_id=CLI_CONSUMER_ID,
        max_skills=args.max_skills,
    )
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "discover",
        "consumer_id": CLI_CONSUMER_ID,
        "question": args.question,
        "max_skills": args.max_skills,
        "match_count": len(matches),
        "matches": matches,
        "authority": "KNOWLEDGE_ONLY",
        "execution_authority": "NONE",
        "tool_grants": [],
    }
    _emit(payload)
    return 0


def _command_index_build(args: argparse.Namespace) -> int:
    # Project registry truth to stdout; the read-only CLI writes no cache file.
    registry = _validated_registry(args.registry)
    index = build_skill_index(registry)
    _emit(index)
    return 0


def _command_list(args: argparse.Namespace) -> int:
    index = load_skill_index(args.index)
    skills = list_skill_index(index)
    _emit({
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "list",
        "index": str(Path(args.index)),
        "registry_sha256": index["registry_sha256"],
        "skills_sha256": index["skills_sha256"],
        "skill_count": len(skills),
        "skills": skills,
        "selection_requires_current_registry_revalidation": True,
        "authority": "KNOWLEDGE_ONLY",
        "execution_authority": "NONE",
        "tool_grants": [],
    })
    return 0


def _command_discover_index(args: argparse.Namespace) -> int:
    index = load_skill_index(args.index)
    routed = route_skill_index(
        index,
        args.question,
        consumer_id=CLI_CONSUMER_ID,
        max_skills=args.max_skills,
    )
    _emit({
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "discover-index",
        "consumer_id": CLI_CONSUMER_ID,
        "index": str(Path(args.index)),
        "question": args.question,
        "max_skills": args.max_skills,
        "match_count": len(routed["selected_skill_ids"]),
        **routed,
    })
    return 0


def _command_index_verify(args: argparse.Namespace) -> int:
    index = load_skill_index(args.index)
    registry = _validated_registry(args.registry)
    report = verify_skill_index_current(index, registry)
    _emit({
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "index-verify",
        "index": str(Path(args.index)),
        "registry": str(Path(args.registry)),
        **report,
    })
    return 0 if report["current"] else 3


def _command_load_current(args: argparse.Namespace) -> int:
    registry = _validated_registry(args.registry)
    loaded = load_current_skill_context(
        args.skill_root,
        registry,
        skill_id=args.skill_id,
        question=args.question,
        runtime_tools=args.runtime_tool,
        world_as_of=args.world_as_of,
        agent_as_of=args.agent_as_of,
        max_units=args.max_units,
    )
    payload = {
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "load-current",
        "skill_root": str(Path(args.skill_root)),
        "registry": str(Path(args.registry)),
        "skill_id": args.skill_id,
        "question": args.question,
        "world_as_of": args.world_as_of,
        "result": loaded,
    }
    _emit(payload)
    return 0


def _command_personal_add(args: argparse.Namespace) -> int:
    payload = add_personal_entry(
        args.package_root,
        args.validated_lifecycle,
        library_path=args.library,
        accept=args.accept,
        added_at=args.added_at,
    )
    _emit(payload)
    return 0


def _command_personal_list(args: argparse.Namespace) -> int:
    entries = list_personal_entries(library_path=args.library)
    _emit({
        "schema_version": CLI_SCHEMA_VERSION,
        "command": "personal-list",
        "library": (
            str(Path(args.library).absolute())
            if args.library is not None
            else str(default_library_path())
        ),
        "entry_count": len(entries),
        "entries": entries,
        "authority": "KNOWLEDGE_ONLY",
        "instruction_authority": "NONE",
        "execution_authority": "NONE",
        "executable": False,
        "tool_grants": [],
        "knowledge_mode": PERSONAL_KNOWLEDGE_MODE,
        "promotion_performed": False,
    })
    return 0


def _command_personal_lookup(args: argparse.Namespace) -> int:
    payload = lookup_personal(
        args.question,
        library_path=args.library,
        max_skills=args.max_skills,
        max_units=args.max_units,
    )
    _emit(payload)
    return 0


def _command_personal_remove(args: argparse.Namespace) -> int:
    payload = remove_personal_entry(
        args.skill_id,
        library_path=args.library,
    )
    _emit(payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-forge",
        description=(
            "Read-only governed knowledge validation, inspection, discovery, "
            "and current-use loading."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s CLI {CLI_SCHEMA_VERSION}",
    )

    commands = parser.add_subparsers(dest="command", required=True)

    validate_parser = commands.add_parser(
        "validate-package",
        help="Validate an emitted skill package fail-closed.",
    )
    validate_parser.add_argument("package_root")
    validate_parser.set_defaults(handler=_command_validate_package)

    inspect_parser = commands.add_parser(
        "registry-inspect",
        help="Validate and summarize durable current registry truth.",
    )
    inspect_parser.add_argument("registry")
    inspect_parser.set_defaults(handler=_command_registry_inspect)

    index_build_parser = commands.add_parser(
        "index-build",
        help=(
            "Project current PROMOTED registry truth to a deterministic fast "
            "skill index on stdout; this command does not write a cache file."
        ),
    )
    index_build_parser.add_argument("registry")
    index_build_parser.set_defaults(handler=_command_index_build)

    list_parser = commands.add_parser(
        "list",
        help="List governed skills from a prebuilt fast skill index.",
    )
    list_parser.add_argument("index")
    list_parser.set_defaults(handler=_command_list)

    discover_index_parser = commands.add_parser(
        "discover-index",
        help=(
            "Fast-route a question from a prebuilt index. Selected skills "
            "still require current registry/trust/temporal revalidation."
        ),
    )
    discover_index_parser.add_argument("index")
    discover_index_parser.add_argument("question")
    discover_index_parser.add_argument(
        "--max-skills",
        type=int,
        default=1,
        choices=(1, 2, 3),
    )
    discover_index_parser.set_defaults(handler=_command_discover_index)

    index_verify_parser = commands.add_parser(
        "index-verify",
        help="Verify whether a fast index exactly matches current registry truth.",
    )
    index_verify_parser.add_argument("index")
    index_verify_parser.add_argument("registry")
    index_verify_parser.set_defaults(handler=_command_index_verify)

    discover_parser = commands.add_parser(
        "discover",
        help="Route a question to bounded governed skill identities.",
    )
    discover_parser.add_argument("registry")
    discover_parser.add_argument("question")
    discover_parser.add_argument(
        "--max-skills",
        type=int,
        default=1,
        choices=(1, 2, 3),
    )
    discover_parser.set_defaults(handler=_command_discover)

    load_parser = commands.add_parser(
        "load-current",
        help="Load current selected knowledge through trust + temporal walls.",
    )
    load_parser.add_argument("skill_root")
    load_parser.add_argument("registry")
    load_parser.add_argument("skill_id")
    load_parser.add_argument("question")
    load_parser.add_argument("--world-as-of", required=True)
    load_parser.add_argument("--agent-as-of")
    load_parser.add_argument(
        "--runtime-tool",
        action="append",
        default=[],
        help="Existing runtime tool name; repeatable. Skills cannot widen it.",
    )
    load_parser.add_argument(
        "--max-units",
        type=int,
        default=1,
        choices=(1, 2, 3),
    )
    load_parser.set_defaults(handler=_command_load_current)

    personal_parser = commands.add_parser(
        "personal",
        help=(
            "Local personal library: add, list, lookup, and remove "
            "VALIDATED knowledge. No promotion and no operator surface."
        ),
    )
    personal_commands = personal_parser.add_subparsers(
        dest="personal_command", required=True
    )

    personal_add_parser = personal_commands.add_parser(
        "add",
        help="Accept a VALIDATED package into the personal library.",
    )
    personal_add_parser.add_argument("package_root")
    personal_add_parser.add_argument(
        "--validated-lifecycle",
        required=True,
        dest="validated_lifecycle",
        help="Path to the package's lifecycle.validated.json record.",
    )
    personal_add_parser.add_argument(
        "--library",
        default=None,
        help="Personal library file; defaults to the user state root.",
    )
    personal_add_parser.add_argument("--added-at", default=None)
    personal_add_parser.add_argument(
        "--accept",
        action="store_true",
        help=(
            "Explicit conversational acceptance; it is not proof of "
            "authenticated human identity and is recorded as such."
        ),
    )
    personal_add_parser.set_defaults(handler=_command_personal_add)

    personal_list_parser = personal_commands.add_parser(
        "list",
        help="List personal library entries (digests only, no content).",
    )
    personal_list_parser.add_argument("--library", default=None)
    personal_list_parser.set_defaults(handler=_command_personal_list)

    personal_lookup_parser = personal_commands.add_parser(
        "lookup",
        help="Bounded, revalidated personal retrieval; untrusted data only.",
    )
    personal_lookup_parser.add_argument("question")
    personal_lookup_parser.add_argument("--library", default=None)
    personal_lookup_parser.add_argument(
        "--max-skills", type=int, default=1, choices=(1, 2, 3)
    )
    personal_lookup_parser.add_argument(
        "--max-units", type=int, default=1, choices=(1, 2, 3)
    )
    personal_lookup_parser.set_defaults(handler=_command_personal_lookup)

    personal_remove_parser = personal_commands.add_parser(
        "remove",
        help="Remove a library reference; package files are not touched.",
    )
    personal_remove_parser.add_argument("skill_id")
    personal_remove_parser.add_argument("--library", default=None)
    personal_remove_parser.set_defaults(handler=_command_personal_remove)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (
        ConsumerDiscoveryError,
        KnowledgeForgeCliError,
        PersonalLibraryError,
        SkillIndexError,
        SkillRegistryError,
        TemporalIntegrationError,
    ) as exc:
        _emit(
            {
                "schema_version": CLI_SCHEMA_VERSION,
                "ok": False,
                "command": getattr(args, "command", None),
                "error": str(exc),
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
