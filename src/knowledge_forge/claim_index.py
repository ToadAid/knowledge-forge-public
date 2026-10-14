# Receipt-bound structured claim index for governed skill packages.

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .claims import ClaimError, normalize_claim, unit_id_for
from .conflicts import claim_sha256
from .faithfulness import candidate_sha256
from .provenance import ProvenanceError, normalize_knowledge_id, skill_id_for

CLAIM_INDEX_SCHEMA_VERSION = "0.1"


class ClaimIndexError(ValueError):
    pass


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                value,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ClaimIndexError("claim index is not canonical JSON data") from exc


def claim_index_artifact_sha256(index: Mapping[str, Any]) -> str:
    if not isinstance(index, Mapping):
        raise ClaimIndexError("claim index must be a mapping")
    return f"sha256:{sha256(_json_bytes(dict(index))).hexdigest()}"


def _sha256_ref(value: Any, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("sha256:")
        or len(value) != 71
        or any(ch not in "0123456789abcdef" for ch in value[7:])
    ):
        raise ClaimIndexError(f"{name} must be a sha256 reference")
    return value


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ClaimIndexError(f"{name} must be a positive integer")
    return value


def _source_bindings(value: Any, source_ids: set[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ClaimIndexError("claim index member requires source_bindings")
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for binding in value:
        if not isinstance(binding, Mapping):
            raise ClaimIndexError("source binding must be a mapping")
        if set(binding) != {"source_id", "section_ids"}:
            raise ClaimIndexError("source binding fields must match exact contract")
        source_id = _sha256_ref(binding.get("source_id"), "source_id")
        if source_id not in source_ids:
            raise ClaimIndexError("source binding references source outside package")
        section_ids = binding.get("section_ids")
        if (
            not isinstance(section_ids, list)
            or not section_ids
            or any(not isinstance(item, str) or not item for item in section_ids)
        ):
            raise ClaimIndexError("source binding requires section_ids")
        normalized_sections = tuple(dict.fromkeys(section_ids))
        key = (source_id, normalized_sections)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "source_id": source_id,
            "section_ids": list(normalized_sections),
        })
    result.sort(key=lambda item: (item["source_id"], item["section_ids"]))
    return result


def build_claim_index(
    candidate: Mapping[str, Any],
    *,
    skill_id: str,
    skill_version: int,
) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise ClaimIndexError("candidate must be a mapping")
    try:
        knowledge_id = normalize_knowledge_id(candidate.get("knowledge_id"))
    except ProvenanceError as exc:
        raise ClaimIndexError(str(exc)) from exc

    version = _positive_int(skill_version, "skill_version")
    compiler = candidate.get("compiler")
    if not isinstance(compiler, Mapping):
        raise ClaimIndexError("candidate requires compiler")
    profile_id = _sha256_ref(
        compiler.get("compiler_profile_id"),
        "compiler_profile_id",
    )
    try:
        expected_skill_id = skill_id_for(knowledge_id, profile_id)
    except ProvenanceError as exc:
        raise ClaimIndexError(str(exc)) from exc
    if skill_id != expected_skill_id:
        raise ClaimIndexError("skill_id does not match knowledge/compiler identity")

    source_ids = candidate.get("source_ids")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or any(not isinstance(item, str) for item in source_ids)
    ):
        raise ClaimIndexError("candidate requires source_ids")
    normalized_sources = [
        _sha256_ref(item, "source_id") for item in source_ids
    ]
    if len(set(normalized_sources)) != len(normalized_sources):
        raise ClaimIndexError("candidate source_ids must be unique")
    source_set = set(normalized_sources)

    units = candidate.get("units")
    if not isinstance(units, list) or not units:
        raise ClaimIndexError("candidate requires units")

    claims: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for unit in units:
        if not isinstance(unit, Mapping):
            raise ClaimIndexError("candidate unit must be a mapping")
        try:
            claim = normalize_claim(unit.get("claim"))
        except ClaimError as exc:
            raise ClaimIndexError(str(exc)) from exc
        unit_id = unit_id_for(knowledge_id, claim["claim_key"])
        if unit.get("unit_id") != unit_id:
            raise ClaimIndexError("unit_id does not match knowledge_id + claim_key")
        kind = unit.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ClaimIndexError("unit requires kind")
        claim_ref = f"{skill_id}#{unit_id}"
        if claim_ref in seen_refs:
            raise ClaimIndexError("duplicate claim_ref")
        seen_refs.add(claim_ref)
        claims.append({
            "claim_ref": claim_ref,
            "unit_id": unit_id,
            "claim_key": claim["claim_key"],
            "kind": kind,
            "claim_sha256": claim_sha256(claim),
            "claim": claim,
            "source_bindings": _source_bindings(
                unit.get("source_bindings"),
                source_set,
            ),
        })

    claims.sort(key=lambda item: item["claim_ref"])
    return {
        "schema_version": CLAIM_INDEX_SCHEMA_VERSION,
        "skill_id": skill_id,
        "skill_version": version,
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_sha256(candidate),
        "compiler_profile_id": profile_id,
        "source_ids": normalized_sources,
        "claims": claims,
    }


def validate_claim_index(
    index: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(index, Mapping):
        raise ClaimIndexError("claim index must be a mapping")
    expected_top = {
        "schema_version",
        "skill_id",
        "skill_version",
        "knowledge_id",
        "candidate_sha256",
        "compiler_profile_id",
        "source_ids",
        "claims",
    }
    if set(index) != expected_top:
        raise ClaimIndexError("claim index fields must match exact contract")
    if index.get("schema_version") != CLAIM_INDEX_SCHEMA_VERSION:
        raise ClaimIndexError("claim index schema_version mismatch")

    skill_id = index.get("skill_id")
    if not isinstance(skill_id, str) or not skill_id:
        raise ClaimIndexError("claim index requires skill_id")
    version = _positive_int(index.get("skill_version"), "skill_version")
    try:
        knowledge_id = normalize_knowledge_id(index.get("knowledge_id"))
    except ProvenanceError as exc:
        raise ClaimIndexError(str(exc)) from exc
    candidate_digest = _sha256_ref(
        index.get("candidate_sha256"),
        "candidate_sha256",
    )
    profile_id = _sha256_ref(
        index.get("compiler_profile_id"),
        "compiler_profile_id",
    )
    try:
        expected_skill_id = skill_id_for(knowledge_id, profile_id)
    except ProvenanceError as exc:
        raise ClaimIndexError(str(exc)) from exc
    if skill_id != expected_skill_id:
        raise ClaimIndexError("claim index skill_id mismatch")

    source_ids = index.get("source_ids")
    if (
        not isinstance(source_ids, list)
        or not source_ids
        or len(set(source_ids)) != len(source_ids)
    ):
        raise ClaimIndexError("claim index requires unique source_ids")
    normalized_sources = [
        _sha256_ref(item, "source_id") for item in source_ids
    ]
    source_set = set(normalized_sources)

    claims = index.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ClaimIndexError("claim index requires claims")
    refs: list[str] = []
    for item in claims:
        if not isinstance(item, Mapping):
            raise ClaimIndexError("claim index entry must be a mapping")
        expected_fields = {
            "claim_ref",
            "unit_id",
            "claim_key",
            "kind",
            "claim_sha256",
            "claim",
            "source_bindings",
        }
        if set(item) != expected_fields:
            raise ClaimIndexError("claim index entry fields must match exact contract")
        try:
            claim = normalize_claim(item.get("claim"))
        except ClaimError as exc:
            raise ClaimIndexError(str(exc)) from exc
        if item.get("claim_key") != claim["claim_key"]:
            raise ClaimIndexError("claim_key does not match structured claim")
        unit_id = unit_id_for(knowledge_id, claim["claim_key"])
        if item.get("unit_id") != unit_id:
            raise ClaimIndexError("claim index unit_id mismatch")
        claim_ref = f"{skill_id}#{unit_id}"
        if item.get("claim_ref") != claim_ref:
            raise ClaimIndexError("claim_ref mismatch")
        if item.get("claim_sha256") != claim_sha256(claim):
            raise ClaimIndexError("claim_sha256 mismatch")
        kind = item.get("kind")
        if not isinstance(kind, str) or not kind:
            raise ClaimIndexError("claim index entry requires kind")
        _source_bindings(item.get("source_bindings"), source_set)
        refs.append(claim_ref)

    if len(set(refs)) != len(refs):
        raise ClaimIndexError("claim index contains duplicate claim_ref")
    if refs != sorted(refs):
        raise ClaimIndexError("claim index claims must be sorted by claim_ref")

    if manifest is not None:
        if not isinstance(manifest, Mapping):
            raise ClaimIndexError("manifest must be a mapping")
        if manifest.get("skill_id") != skill_id:
            raise ClaimIndexError("claim index skill_id does not match manifest")
        if manifest.get("skill_version") != version:
            raise ClaimIndexError("claim index skill_version does not match manifest")
        if manifest.get("knowledge_id") != knowledge_id:
            raise ClaimIndexError("claim index knowledge_id does not match manifest")
        if manifest.get("candidate_sha256") != candidate_digest:
            raise ClaimIndexError("claim index candidate_sha256 does not match manifest")
        compiler = manifest.get("compiler")
        if (
            not isinstance(compiler, Mapping)
            or compiler.get("compiler_profile_id") != profile_id
        ):
            raise ClaimIndexError("claim index compiler profile does not match manifest")
        if manifest.get("source_ids") != normalized_sources:
            raise ClaimIndexError("claim index source_ids do not match manifest")

        manifest_units = manifest.get("units")
        if not isinstance(manifest_units, list) or not manifest_units:
            raise ClaimIndexError("manifest requires units")
        manifest_pairs: list[tuple[str, str, str]] = []
        for unit in manifest_units:
            if not isinstance(unit, Mapping):
                raise ClaimIndexError("manifest unit must be a mapping")
            unit_id = unit.get("unit_id")
            claim_key = unit.get("claim_key")
            kind = unit.get("kind")
            if (
                not isinstance(unit_id, str)
                or not isinstance(claim_key, str)
                or not isinstance(kind, str)
            ):
                raise ClaimIndexError("manifest unit identity is invalid")
            manifest_pairs.append((unit_id, claim_key, kind))
        index_pairs = [
            (item["unit_id"], item["claim_key"], item["kind"])
            for item in claims
        ]
        if sorted(manifest_pairs) != sorted(index_pairs):
            raise ClaimIndexError("claim index unit coverage does not match manifest")

    return {"valid": True, "errors": []}
