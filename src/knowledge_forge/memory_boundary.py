"""Runtime proof that governed skill knowledge cannot become memory.

P4D keeps Knowledge Forge knowledge and memory storage classes separate.
A verified P4C load may be represented outside reasoning only as a
provenance-only KNOWLEDGE_REFERENCE. Skill-origin material cannot be
reclassified as autobiography, user-stated memory, or lived evidence.

This module does not authorize positive memory writes from other origins.
Memory systems remain responsible for their own write policy and evidence.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .trust_wall import (
    AUTHORITY,
    EXECUTION_AUTHORITY,
    INSTRUCTION_AUTHORITY,
    TRUST_CLASS,
    TRUST_WALL_SCHEMA_VERSION,
)

MEMORY_BOUNDARY_SCHEMA_VERSION = "0.2"
KNOWLEDGE_ORIGIN = "GOVERNED_SKILL_KNOWLEDGE"
KNOWLEDGE_REFERENCE_CLASS = "KNOWLEDGE_REFERENCE"
MEMORY_MUTATION_AUTHORITY = "NONE"

AUTOBIOGRAPHICAL_MEMORY = "AUTOBIOGRAPHICAL_MEMORY"
USER_STATED_MEMORY = "USER_STATED_MEMORY"
LIVED_EVIDENCE = "LIVED_EVIDENCE"

PROHIBITED_MEMORY_CLASSES = frozenset({
    AUTOBIOGRAPHICAL_MEMORY,
    USER_STATED_MEMORY,
    LIVED_EVIDENCE,
})

_REFERENCE_FIELDS = {
    "schema_version",
    "storage_class",
    "origin",
    "memory_mutation_authority",
    "autobiographical_evidence",
    "user_stated_evidence",
    "lived_evidence",
    "trust_class",
    "instruction_authority",
    "execution_authority",
    "authority",
    "skill_id",
    "skill_version",
    "knowledge_id",
    "package_receipt_sha256",
    "claim_index_sha256",
    "lifecycle_head_sha256",
    "question_sha256",
    "loaded_units",
}


class MemoryBoundaryError(ValueError):
    """Raised when skill-origin knowledge is projected across memory law."""


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MemoryBoundaryError(f"{name} must be a non-empty string")
    return value.strip()


def _positive_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise MemoryBoundaryError(f"{name} must be a positive integer")
    return value


def _sha256_ref(value: Any, name: str) -> str:
    text = _nonempty(value, name)
    if (
        not text.startswith("sha256:")
        or len(text) != 71
        or any(ch not in "0123456789abcdef" for ch in text[7:])
    ):
        raise MemoryBoundaryError(f"{name} must be a sha256 reference")
    return text


def _validated_load_evidence(load_result: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(load_result, Mapping):
        raise MemoryBoundaryError("trust-wall load result must be a mapping")
    if load_result.get("schema_version") != TRUST_WALL_SCHEMA_VERSION:
        raise MemoryBoundaryError("trust-wall schema_version mismatch")
    if load_result.get("trust_class") != TRUST_CLASS:
        raise MemoryBoundaryError(
            "skill-origin memory boundary requires untrusted knowledge data"
        )
    if load_result.get("instruction_authority") != INSTRUCTION_AUTHORITY:
        raise MemoryBoundaryError(
            "skill-origin knowledge must have zero instruction authority"
        )
    if load_result.get("execution_authority") != EXECUTION_AUTHORITY:
        raise MemoryBoundaryError(
            "skill-origin knowledge must have zero execution authority"
        )
    if load_result.get("authority") != AUTHORITY:
        raise MemoryBoundaryError(
            "skill-origin knowledge authority must remain KNOWLEDGE_ONLY"
        )

    context = load_result.get("reasoning_context")
    if not isinstance(context, list) or not context:
        raise MemoryBoundaryError(
            "trust-wall load requires selected reasoning context"
        )
    context_ids: list[str] = []
    for item in context:
        if not isinstance(item, Mapping):
            raise MemoryBoundaryError("reasoning context entry must be a mapping")
        if item.get("trust_class") != TRUST_CLASS:
            raise MemoryBoundaryError("reasoning context trust class mismatch")
        if item.get("instruction_authority") != INSTRUCTION_AUTHORITY:
            raise MemoryBoundaryError(
                "reasoning context instruction authority mismatch"
            )
        if item.get("execution_authority") != EXECUTION_AUTHORITY:
            raise MemoryBoundaryError(
                "reasoning context execution authority mismatch"
            )
        if item.get("authority") != AUTHORITY:
            raise MemoryBoundaryError("reasoning context authority mismatch")
        if item.get("executable") is not False:
            raise MemoryBoundaryError(
                "reasoning context must remain non-executable"
            )
        context_ids.append(_nonempty(item.get("unit_id"), "context unit_id"))
    if len(context_ids) != len(set(context_ids)):
        raise MemoryBoundaryError("reasoning context unit_ids must be unique")

    evidence = load_result.get("load_evidence")
    if not isinstance(evidence, Mapping):
        raise MemoryBoundaryError("trust-wall load_evidence must be a mapping")

    skill_id = _nonempty(evidence.get("skill_id"), "skill_id")
    skill_version = _positive_int(
        evidence.get("skill_version"),
        "skill_version",
    )
    knowledge_id = _nonempty(evidence.get("knowledge_id"), "knowledge_id")
    package_receipt_sha256 = _sha256_ref(
        evidence.get("package_receipt_sha256"),
        "package_receipt_sha256",
    )
    claim_index_sha256 = _sha256_ref(
        evidence.get("claim_index_sha256"),
        "claim_index_sha256",
    )
    lifecycle_head_sha256 = _sha256_ref(
        evidence.get("lifecycle_head_sha256"),
        "lifecycle_head_sha256",
    )
    question_sha256 = _sha256_ref(
        evidence.get("question_sha256"),
        "question_sha256",
    )

    loaded_units = evidence.get("loaded_units")
    if not isinstance(loaded_units, list) or not loaded_units:
        raise MemoryBoundaryError("load_evidence requires loaded_units")

    projected_units: list[dict[str, Any]] = []
    loaded_ids: list[str] = []
    for item in loaded_units:
        if not isinstance(item, Mapping):
            raise MemoryBoundaryError("loaded unit evidence must be a mapping")
        unit_id = _nonempty(item.get("unit_id"), "loaded unit_id")
        claim_key = _nonempty(item.get("claim_key"), "loaded claim_key")
        claim_sha256 = _sha256_ref(
            item.get("claim_sha256"),
            "loaded claim_sha256",
        )
        source_bindings = item.get("source_bindings")
        if not isinstance(source_bindings, list) or not source_bindings:
            raise MemoryBoundaryError(
                "loaded unit evidence requires source_bindings"
            )
        projected_units.append({
            "unit_id": unit_id,
            "claim_key": claim_key,
            "claim_sha256": claim_sha256,
            "source_bindings": deepcopy(source_bindings),
        })
        loaded_ids.append(unit_id)

    if loaded_ids != context_ids:
        raise MemoryBoundaryError(
            "reasoning context and load evidence unit order must match"
        )

    return {
        "skill_id": skill_id,
        "skill_version": skill_version,
        "knowledge_id": knowledge_id,
        "package_receipt_sha256": package_receipt_sha256,
        "claim_index_sha256": claim_index_sha256,
        "lifecycle_head_sha256": lifecycle_head_sha256,
        "question_sha256": question_sha256,
        "loaded_units": projected_units,
    }


def build_knowledge_reference(
    load_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project a P4C load into provenance-only non-memory audit evidence."""

    evidence = _validated_load_evidence(load_result)
    return {
        "schema_version": MEMORY_BOUNDARY_SCHEMA_VERSION,
        "storage_class": KNOWLEDGE_REFERENCE_CLASS,
        "origin": KNOWLEDGE_ORIGIN,
        "memory_mutation_authority": MEMORY_MUTATION_AUTHORITY,
        "autobiographical_evidence": False,
        "user_stated_evidence": False,
        "lived_evidence": False,
        "trust_class": TRUST_CLASS,
        "instruction_authority": INSTRUCTION_AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "authority": AUTHORITY,
        **evidence,
    }


def validate_knowledge_reference(
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(reference, Mapping):
        return {
            "valid": False,
            "errors": ["knowledge reference must be a mapping"],
        }
    if set(reference) != _REFERENCE_FIELDS:
        errors.append("knowledge reference fields must match exact contract")
    expected = {
        "schema_version": MEMORY_BOUNDARY_SCHEMA_VERSION,
        "storage_class": KNOWLEDGE_REFERENCE_CLASS,
        "origin": KNOWLEDGE_ORIGIN,
        "memory_mutation_authority": MEMORY_MUTATION_AUTHORITY,
        "autobiographical_evidence": False,
        "user_stated_evidence": False,
        "lived_evidence": False,
        "trust_class": TRUST_CLASS,
        "instruction_authority": INSTRUCTION_AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "authority": AUTHORITY,
    }
    for key, value in expected.items():
        if reference.get(key) != value:
            errors.append(f"knowledge reference {key} mismatch")

    try:
        _nonempty(reference.get("skill_id"), "skill_id")
        _positive_int(reference.get("skill_version"), "skill_version")
        _nonempty(reference.get("knowledge_id"), "knowledge_id")
        for field in (
            "package_receipt_sha256",
            "claim_index_sha256",
            "lifecycle_head_sha256",
            "question_sha256",
        ):
            _sha256_ref(reference.get(field), field)
        loaded = reference.get("loaded_units")
        if not isinstance(loaded, list) or not loaded:
            raise MemoryBoundaryError("knowledge reference requires loaded_units")
        for item in loaded:
            if not isinstance(item, Mapping):
                raise MemoryBoundaryError(
                    "knowledge reference loaded unit must be a mapping"
                )
            if set(item) != {
                "unit_id",
                "claim_key",
                "claim_sha256",
                "source_bindings",
            }:
                raise MemoryBoundaryError(
                    "knowledge reference loaded unit fields mismatch"
                )
            _nonempty(item.get("unit_id"), "unit_id")
            _nonempty(item.get("claim_key"), "claim_key")
            _sha256_ref(item.get("claim_sha256"), "claim_sha256")
            bindings = item.get("source_bindings")
            if not isinstance(bindings, list) or not bindings:
                raise MemoryBoundaryError("loaded unit requires source_bindings")
    except MemoryBoundaryError as exc:
        errors.append(str(exc))

    return {"valid": not errors, "errors": errors}


def project_skill_origin(
    load_result: Mapping[str, Any],
    *,
    target_storage_class: str,
) -> dict[str, Any]:
    """Permit only provenance-only KNOWLEDGE_REFERENCE projection.

    This function intentionally does not authorize memory writes from any
    non-skill origin. It proves only the negative boundary for skill knowledge.
    """

    target = _nonempty(target_storage_class, "target_storage_class")
    _validated_load_evidence(load_result)

    if target in PROHIBITED_MEMORY_CLASSES:
        raise MemoryBoundaryError(
            f"skill-origin knowledge cannot become {target}"
        )
    if target != KNOWLEDGE_REFERENCE_CLASS:
        raise MemoryBoundaryError(
            "skill-origin projection may only target KNOWLEDGE_REFERENCE"
        )
    return build_knowledge_reference(load_result)
