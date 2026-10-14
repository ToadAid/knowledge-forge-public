"""General load-time trust wall for governed knowledge consumers.

P4C joins three previously separate proofs at runtime:

1. exact package artifact validation + source receipt,
2. exact current registry/lifecycle truth,
3. selective retrieval into reasoning context.

Skill content is returned only as UNTRUSTED_KNOWLEDGE_DATA. It has no
instruction authority and cannot grant tools. If a consumer chooses to honor a
skill-origin tool constraint, the trust wall applies it by intersection with the
runtime's pre-existing tool set; widening requests are denied and recorded.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from .claim_index import claim_index_artifact_sha256
from .lifecycle import PROMOTED, validate_lifecycle_record
from .registry import validate_skill_registry
from .retrieval import (
    RetrievalError,
    route_question,
    validate_retrieval_index,
)
from .validate import PackageValidationError, validate_or_raise

TRUST_WALL_SCHEMA_VERSION = "0.2"
AUTHORITY = "KNOWLEDGE_ONLY"
TRUST_CLASS = "UNTRUSTED_KNOWLEDGE_DATA"
INSTRUCTION_AUTHORITY = "NONE"
EXECUTION_AUTHORITY = "NONE"
TOOL_POLICY_RULE = "INTERSECTION_ONLY_NEVER_WIDEN"
MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 64 * 1024 * 1024


class TrustWallError(ValueError):
    """Raised when governed knowledge cannot cross the runtime trust wall."""


def _json_object(payload: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrustWallError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TrustWallError(f"{name} must be a JSON object")
    return value


def _sha256_ref(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def _canonical_receipt_sha256(receipt: Mapping[str, Any]) -> str:
    try:
        payload = (
            json.dumps(
                dict(receipt),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise TrustWallError("source receipt is not canonical JSON data") from exc
    return _sha256_ref(payload)


def _snapshot_package(skill_root: str | Path) -> dict[str, bytes]:
    root = Path(skill_root)
    if root.is_symlink() or not root.is_dir():
        raise TrustWallError("skill_root must be a non-symlink directory")

    files: dict[str, bytes] = {}
    total = 0
    paths = sorted(root.rglob("*"), key=lambda item: item.as_posix())
    for path in paths:
        if path.is_symlink():
            raise TrustWallError("skill package must not contain symlinks")
        if path.is_dir():
            continue
        if not path.is_file():
            raise TrustWallError("skill package contains a non-regular artifact")
        relative = path.relative_to(root).as_posix()
        payload = path.read_bytes()
        files[relative] = payload
        total += len(payload)
        if len(files) > MAX_PACKAGE_FILES:
            raise TrustWallError(
                f"skill package exceeds {MAX_PACKAGE_FILES} file limit"
            )
        if total > MAX_PACKAGE_BYTES:
            raise TrustWallError(
                f"skill package exceeds {MAX_PACKAGE_BYTES} byte limit"
            )

    if not files:
        raise TrustWallError("skill package is empty")
    return files


def _registry_entry(
    registry: Mapping[str, Any],
    *,
    skill_id: str,
) -> tuple[int, Mapping[str, Any]]:
    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise TrustWallError(
            "registry is invalid: " + "; ".join(report["errors"])
        )

    skills = registry.get("skills")
    record = skills.get(skill_id) if isinstance(skills, Mapping) else None
    if not isinstance(record, Mapping):
        raise TrustWallError(f"skill is not registered: {skill_id}")
    version = record.get("current_version")
    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
    ):
        raise TrustWallError("registry current_version is invalid")
    versions = record.get("versions")
    entry = versions.get(str(version)) if isinstance(versions, Mapping) else None
    if not isinstance(entry, Mapping):
        raise TrustWallError(
            f"current skill version is not registered: {skill_id}@{version}"
        )
    if entry.get("state") != PROMOTED:
        raise TrustWallError(
            f"current skill is not loadable: {skill_id}@{version} "
            f"state={entry.get('state')}"
        )
    if entry.get("authority") != AUTHORITY:
        raise TrustWallError("registry skill authority must remain KNOWLEDGE_ONLY")
    return version, entry


def _verify_package_against_registry(
    files: Mapping[str, bytes],
    registry: Mapping[str, Any],
    *,
    skill_id: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    Mapping[str, Any],
]:
    try:
        validate_or_raise(files)
    except PackageValidationError as exc:
        raise TrustWallError(f"package validation failed: {exc}") from exc

    manifest = _json_object(files["manifest.json"], name="manifest.json")
    receipt = _json_object(
        files["SOURCE_RECEIPT.json"],
        name="SOURCE_RECEIPT.json",
    )
    claim_index = _json_object(
        files["claim-index.json"],
        name="claim-index.json",
    )
    retrieval_index = _json_object(
        files["retrieval-index.json"],
        name="retrieval-index.json",
    )

    current_version, entry = _registry_entry(
        registry,
        skill_id=skill_id,
    )

    if manifest.get("skill_id") != skill_id:
        raise TrustWallError("manifest skill_id does not match requested skill")
    if manifest.get("skill_version") != current_version:
        raise TrustWallError(
            "package version is not the registry current version"
        )

    for field in (
        "skill_id",
        "skill_version",
        "knowledge_id",
        "candidate_sha256",
        "source_ids",
    ):
        if receipt.get(field) != manifest.get(field):
            raise TrustWallError(
                f"source receipt {field} does not match manifest"
            )
        if entry.get(field) != manifest.get(field):
            raise TrustWallError(
                f"registry {field} does not match package manifest"
            )

    compiler = manifest.get("compiler")
    if not isinstance(compiler, Mapping):
        raise TrustWallError("manifest requires compiler provenance")
    if (
        entry.get("compiler_profile_id")
        != compiler.get("compiler_profile_id")
    ):
        raise TrustWallError(
            "registry compiler profile does not match package"
        )

    package_receipt_sha256 = _canonical_receipt_sha256(receipt)
    if entry.get("package_receipt_sha256") != package_receipt_sha256:
        raise TrustWallError(
            "registry package receipt does not match exact source receipt"
        )
    if entry.get("source_receipt") != receipt:
        raise TrustWallError(
            "registry source receipt does not match package source receipt"
        )

    claim_index_sha256 = claim_index_artifact_sha256(claim_index)
    if entry.get("claim_index_sha256") != claim_index_sha256:
        raise TrustWallError(
            "registry claim index digest does not match package"
        )
    if entry.get("claim_index") != claim_index:
        raise TrustWallError(
            "registry claim index does not match package claim index"
        )

    lifecycle = entry.get("lifecycle_record")
    if not isinstance(lifecycle, Mapping):
        raise TrustWallError("registry entry requires lifecycle_record")
    lifecycle_report = validate_lifecycle_record(lifecycle)
    if not lifecycle_report["valid"]:
        raise TrustWallError(
            "registry lifecycle is invalid: "
            + "; ".join(lifecycle_report["errors"])
        )
    if lifecycle.get("state") != PROMOTED:
        raise TrustWallError("load-time lifecycle must be PROMOTED")
    if (
        lifecycle.get("package_receipt_sha256")
        != package_receipt_sha256
    ):
        raise TrustWallError(
            "lifecycle package receipt does not match exact package"
        )

    events = lifecycle.get("events")
    if not isinstance(events, list) or not events:
        raise TrustWallError("promoted lifecycle requires event history")
    head = events[-1].get("event_sha256")
    if entry.get("lifecycle_head_sha256") != head:
        raise TrustWallError(
            "registry lifecycle head does not match lifecycle record"
        )

    try:
        validate_retrieval_index(retrieval_index, manifest)
    except RetrievalError as exc:
        raise TrustWallError(f"retrieval index invalid: {exc}") from exc

    return manifest, receipt, claim_index, retrieval_index, entry


def _tool_names(
    values: Iterable[str],
    *,
    name: str,
) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise TrustWallError(f"{name} must be an iterable of tool names")
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise TrustWallError(
                f"{name} contains an invalid tool name"
            )
        tool = value.strip()
        if tool not in result:
            result.append(tool)
    result.sort()
    return result


def apply_skill_tool_constraints(
    runtime_tools: Iterable[str],
    skill_requested_tools: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Apply untrusted skill-origin tool requests by monotonic intersection."""

    baseline = _tool_names(runtime_tools, name="runtime_tools")
    if skill_requested_tools is None:
        requested = None
        effective = list(baseline)
        denied: list[str] = []
    else:
        requested = _tool_names(
            skill_requested_tools,
            name="skill_requested_tools",
        )
        baseline_set = set(baseline)
        requested_set = set(requested)
        effective = sorted(baseline_set & requested_set)
        denied = sorted(requested_set - baseline_set)

    return {
        "rule": TOOL_POLICY_RULE,
        "runtime_tools": baseline,
        "skill_requested_tools": requested,
        "effective_tools": effective,
        "denied_widening": denied,
        "widening_possible": False,
    }


def load_promoted_skill_context(
    skill_root: str | Path,
    registry: Mapping[str, Any],
    *,
    skill_id: str,
    question: str,
    runtime_tools: Iterable[str],
    skill_requested_tools: Iterable[str] | None = None,
    max_units: int = 1,
) -> dict[str, Any]:
    """Verify exact current promoted truth, then return selected untrusted data."""

    if not isinstance(skill_id, str) or not skill_id.strip():
        raise TrustWallError("skill_id must be non-empty")
    if not isinstance(question, str) or not question.strip():
        raise TrustWallError("question must be non-empty")

    files = _snapshot_package(skill_root)
    (
        manifest,
        _receipt,
        claim_index,
        retrieval_index,
        entry,
    ) = _verify_package_against_registry(
        files,
        registry,
        skill_id=skill_id.strip(),
    )

    try:
        selected = route_question(
            retrieval_index,
            question,
            max_units=max_units,
        )
    except RetrievalError as exc:
        raise TrustWallError(str(exc)) from exc

    manifest_units = manifest.get("units")
    if not isinstance(manifest_units, list):
        raise TrustWallError("manifest units must be a list")
    units_by_id = {
        item.get("unit_id"): item
        for item in manifest_units
        if isinstance(item, Mapping)
    }
    claim_items = claim_index.get("claims")
    if not isinstance(claim_items, list):
        raise TrustWallError("claim index claims must be a list")
    claims_by_id = {
        item.get("unit_id"): item
        for item in claim_items
        if isinstance(item, Mapping)
    }

    context: list[dict[str, Any]] = []
    loaded_units: list[dict[str, Any]] = []
    for routed in selected:
        unit_id = routed["unit_id"]
        manifest_unit = units_by_id.get(unit_id)
        claim_item = claims_by_id.get(unit_id)
        if not isinstance(manifest_unit, Mapping):
            raise TrustWallError(
                f"selected unit missing from manifest: {unit_id}"
            )
        if not isinstance(claim_item, Mapping):
            raise TrustWallError(
                f"selected unit missing from claim index: {unit_id}"
            )
        if manifest_unit.get("executable") is not False:
            raise TrustWallError(
                f"selected unit became executable: {unit_id}"
            )
        claim = claim_item.get("claim")
        if (
            not isinstance(claim, Mapping)
            or claim.get("executable") is not False
        ):
            raise TrustWallError(
                f"selected claim became executable: {unit_id}"
            )
        path = manifest_unit.get("path")
        if not isinstance(path, str) or path not in files:
            raise TrustWallError(
                f"selected unit artifact is missing: {unit_id}"
            )
        try:
            content = files[path].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TrustWallError(
                f"selected unit artifact is not UTF-8: {unit_id}"
            ) from exc

        source_bindings = deepcopy(claim_item.get("source_bindings"))
        context.append({
            "trust_class": TRUST_CLASS,
            "instruction_authority": INSTRUCTION_AUTHORITY,
            "execution_authority": EXECUTION_AUTHORITY,
            "authority": AUTHORITY,
            "executable": False,
            "skill_id": manifest["skill_id"],
            "skill_version": manifest["skill_version"],
            "unit_id": unit_id,
            "claim_key": claim_item.get("claim_key"),
            "kind": claim_item.get("kind"),
            "source_bindings": source_bindings,
            "content": content,
        })
        loaded_units.append({
            "unit_id": unit_id,
            "claim_key": claim_item.get("claim_key"),
            "claim_sha256": claim_item.get("claim_sha256"),
            "source_bindings": source_bindings,
        })

    tool_policy = apply_skill_tool_constraints(
        runtime_tools,
        skill_requested_tools,
    )

    return {
        "schema_version": TRUST_WALL_SCHEMA_VERSION,
        "trust_class": TRUST_CLASS,
        "instruction_authority": INSTRUCTION_AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "authority": AUTHORITY,
        "reasoning_context": context,
        "effective_tools": list(tool_policy["effective_tools"]),
        "tool_policy": tool_policy,
        "load_evidence": {
            "skill_id": manifest["skill_id"],
            "skill_version": manifest["skill_version"],
            "knowledge_id": manifest["knowledge_id"],
            "package_receipt_sha256": entry["package_receipt_sha256"],
            "claim_index_sha256": entry["claim_index_sha256"],
            "lifecycle_head_sha256": entry["lifecycle_head_sha256"],
            "question_sha256": _sha256_ref(question.encode("utf-8")),
            "loaded_unit_ids": [
                item["unit_id"] for item in loaded_units
            ],
            "loaded_units": loaded_units,
            "verified_artifact_count": len(files),
            "reasoning_unit_count": len(context),
        },
    }
