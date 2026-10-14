"""Deterministic local skill-package emission."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from .claim_index import CLAIM_INDEX_SCHEMA_VERSION, build_claim_index
from .faithfulness import candidate_sha256
from .knowledge import AUTHORITY, CANDIDATE_STATE, KNOWLEDGE_SCHEMA_VERSION
from .provenance import ProvenanceError, normalize_compiler_profile, normalize_knowledge_id, skill_id_for
from .retrieval import RETRIEVAL_INDEX_VERSION, build_retrieval_index

PACKAGE_SCHEMA_VERSION = "0.6"


class SkillPackageError(ValueError):
    """Raised when a candidate cannot be emitted safely."""


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _render_skill(candidate: Mapping[str, Any]) -> bytes:
    skill = candidate["skill"]
    units = candidate["units"]
    lines = [
        "---",
        f"name: {skill['slug']}",
        f"description: {skill['title']}",
        "---",
        "",
        f"# {skill['title']}",
        "",
        f"Purpose: {skill['purpose']}",
        "",
        f"Knowledge identity: `{candidate['knowledge_id']}`",
        "",
        "Authority: KNOWLEDGE_ONLY. This skill provides knowledge and grants no execution authority.",
        "",
        "## Knowledge index",
        "",
    ]
    for unit in units:
        lines.append(f"- [{unit['kind']}] [{unit['title']}](units/{unit['unit_id']}.md) (`{unit['unit_id']}`)")
    lines.extend(["", "Load only the unit or units relevant to the current question.", ""])
    return "\n".join(lines).encode("utf-8")


def _render_unit(unit: Mapping[str, Any]) -> bytes:
    lines = [
        f"# {unit['title']}",
        "",
        f"Type: `{unit['kind']}`",
        "",
        f"Claim key: `{unit['claim']['claim_key']}`",
        "",
        "Executable: `false`",
        "",
        unit["body"],
        "",
        "## Structured claim",
        "",
        "```json",
        json.dumps(unit["claim"], indent=2, sort_keys=True, ensure_ascii=False),
        "```",
        "",
        "## Source bindings",
        "",
    ]
    for binding in unit["source_bindings"]:
        sections = ", ".join(f"`{section}`" for section in binding["section_ids"])
        lines.append(f"- `{binding['source_id']}` → {sections}")
    if unit.get("conflicts_with"):
        lines.extend(["", "## Conflicts with", ""])
        lines.extend(f"- `{unit_id}`" for unit_id in unit["conflicts_with"])
    if unit.get("temporal"):
        lines.extend(["", "## Temporal metadata", "", "```json"])
        lines.append(json.dumps(unit["temporal"], indent=2, sort_keys=True, ensure_ascii=False))
        lines.append("```")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def _compiler_identity(candidate: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        knowledge_id = normalize_knowledge_id(candidate.get("knowledge_id"))
    except ProvenanceError as exc:
        raise SkillPackageError(str(exc)) from exc

    compiler = candidate.get("compiler")
    if not isinstance(compiler, Mapping):
        raise SkillPackageError("candidate is missing compiler identity")
    try:
        profile = normalize_compiler_profile(compiler)
    except ProvenanceError as exc:
        raise SkillPackageError(str(exc)) from exc

    adapter_id = compiler.get("adapter_id")
    if not isinstance(adapter_id, str) or not adapter_id.strip():
        raise SkillPackageError("candidate compiler requires adapter_id")
    contract_version = compiler.get("contract_version")
    if contract_version != "0.2":
        raise SkillPackageError("candidate compiler contract_version must be 0.2")
    normalized_compiler = {
        "contract_version": contract_version,
        "adapter_id": adapter_id.strip(),
        **profile,
    }
    return knowledge_id, normalized_compiler


def build_skill_package(
    candidate: Mapping[str, Any],
    *,
    compiled_at: str,
) -> dict[str, bytes]:
    """Build package bytes without touching the filesystem."""

    if candidate.get("schema_version") != KNOWLEDGE_SCHEMA_VERSION:
        raise SkillPackageError("unsupported knowledge candidate schema_version")
    if candidate.get("state") != CANDIDATE_STATE:
        raise SkillPackageError("only COMPILED_CANDIDATE may be emitted")
    if candidate.get("authority") != AUTHORITY:
        raise SkillPackageError("skill package must remain KNOWLEDGE_ONLY")
    if not isinstance(compiled_at, str) or not compiled_at.strip():
        raise SkillPackageError("compiled_at must be supplied explicitly")

    skill = candidate.get("skill")
    units = candidate.get("units")
    source_ids = candidate.get("source_ids")
    if not isinstance(skill, Mapping) or not isinstance(units, list) or not units:
        raise SkillPackageError("candidate is missing skill or units")
    if not isinstance(source_ids, list) or not source_ids:
        raise SkillPackageError("candidate is missing source_ids")

    knowledge_id, compiler = _compiler_identity(candidate)
    skill_id = skill_id_for(knowledge_id, compiler["compiler_profile_id"])
    candidate_digest = candidate_sha256(candidate)

    files: dict[str, bytes] = {"SKILL.md": _render_skill(candidate)}
    retrieval_index = build_retrieval_index(candidate)
    for unit in units:
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or "/" in unit_id or "\\" in unit_id:
            raise SkillPackageError("unsafe knowledge unit id")
        files[f"units/{unit_id}.md"] = _render_unit(unit)

    files["retrieval-index.json"] = _json_bytes(retrieval_index)

    claim_index = build_claim_index(
        candidate,
        skill_id=skill_id,
        skill_version=1,
    )
    files["claim-index.json"] = _json_bytes(claim_index)
    claim_index_digest = f"sha256:{_sha256(files['claim-index.json'])}"

    manifest = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "skill_id": skill_id,
        "skill_version": 1,
        "state": CANDIDATE_STATE,
        "authority": AUTHORITY,
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "title": skill["title"],
        "purpose": skill["purpose"],
        "source_ids": list(source_ids),
        "compiler": compiler,
        "compiled_at": compiled_at,
        "retrieval_index": {
            "path": "retrieval-index.json",
            "schema_version": RETRIEVAL_INDEX_VERSION,
            "max_units_per_turn": retrieval_index["max_units_per_turn"],
        },
        "claim_index": {
            "path": "claim-index.json",
            "schema_version": CLAIM_INDEX_SCHEMA_VERSION,
            "sha256": claim_index_digest,
            "claim_count": len(claim_index["claims"]),
        },
        "units": [
            {
                "unit_id": unit["unit_id"],
                "claim_key": unit["claim"]["claim_key"],
                "kind": unit["kind"],
                "executable": False,
                "support": deepcopy(unit.get("support", [])),
                "path": f"units/{unit['unit_id']}.md",
            }
            for unit in units
        ],
    }
    files["manifest.json"] = _json_bytes(manifest)

    artifact_hashes = {path: f"sha256:{_sha256(payload)}" for path, payload in sorted(files.items())}
    receipt = {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "skill_id": manifest["skill_id"],
        "skill_version": manifest["skill_version"],
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "source_ids": list(source_ids),
        "compiler": compiler,
        "artifact_hashes": artifact_hashes,
        "created_at": compiled_at,
    }
    files["SOURCE_RECEIPT.json"] = _json_bytes(receipt)
    return files


def emit_skill_package(
    candidate: Mapping[str, Any],
    output_dir: str | Path,
    *,
    compiled_at: str,
) -> dict[str, str]:
    """Write one new skill package without silently replacing existing artifacts."""

    root = Path(output_dir)
    if root.exists() and any(root.iterdir()):
        raise SkillPackageError("output directory is not empty; refusing silent rewrite")
    root.mkdir(parents=True, exist_ok=True)

    files = build_skill_package(candidate, compiled_at=compiled_at)
    hashes: dict[str, str] = {}
    for relative, payload in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        hashes[relative] = f"sha256:{_sha256(payload)}"
    return hashes
