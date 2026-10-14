"""Procedure boundary for governed knowledge consumers.

P4F proves that a PROCEDURE knowledge unit is descriptive data, not executable
authority. Procedure content may describe sensitive operations, commands, or
tools, but the knowledge boundary emits zero execution/capability grants.

Actual execution authority must come from a separate external typed authority
path owned by the consuming runtime. This module never creates such authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .trust_wall import (
    AUTHORITY,
    EXECUTION_AUTHORITY,
    INSTRUCTION_AUTHORITY,
    TRUST_CLASS,
)

PROCEDURE_BOUNDARY_SCHEMA_VERSION = "0.1"
PROCEDURE_KIND = "PROCEDURE"
AUTHORITY_SOURCE = "EXTERNAL_TYPED_RUNTIME_AUTHORITY_REQUIRED"

SPEND = "SPEND"
SIGN = "SIGN"
DEPLOY = "DEPLOY"
PUSH = "PUSH"
WRITE = "WRITE"
SENSITIVE_CAPABILITIES = frozenset({
    SPEND,
    SIGN,
    DEPLOY,
    PUSH,
    WRITE,
})


class ProcedureBoundaryError(ValueError):
    """Raised when procedure knowledge attempts to cross execution authority."""


def _validate_load_result(load_result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if not isinstance(load_result, Mapping):
        raise ProcedureBoundaryError("load result must be a mapping")
    if load_result.get("trust_class") != TRUST_CLASS:
        raise ProcedureBoundaryError(
            "procedure boundary requires UNTRUSTED_KNOWLEDGE_DATA"
        )
    if load_result.get("instruction_authority") != INSTRUCTION_AUTHORITY:
        raise ProcedureBoundaryError(
            "procedure boundary requires zero instruction authority"
        )
    if load_result.get("execution_authority") != EXECUTION_AUTHORITY:
        raise ProcedureBoundaryError(
            "procedure boundary requires zero execution authority"
        )
    if load_result.get("authority") != AUTHORITY:
        raise ProcedureBoundaryError(
            "procedure boundary requires KNOWLEDGE_ONLY authority"
        )

    context = load_result.get("reasoning_context")
    if not isinstance(context, list) or not context:
        raise ProcedureBoundaryError(
            "procedure boundary requires reasoning_context"
        )

    validated: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for item in context:
        if not isinstance(item, Mapping):
            raise ProcedureBoundaryError(
                "reasoning context entry must be a mapping"
            )
        if item.get("trust_class") != TRUST_CLASS:
            raise ProcedureBoundaryError(
                "reasoning context trust class mismatch"
            )
        if item.get("instruction_authority") != INSTRUCTION_AUTHORITY:
            raise ProcedureBoundaryError(
                "reasoning context instruction authority mismatch"
            )
        if item.get("execution_authority") != EXECUTION_AUTHORITY:
            raise ProcedureBoundaryError(
                "reasoning context execution authority mismatch"
            )
        if item.get("authority") != AUTHORITY:
            raise ProcedureBoundaryError(
                "reasoning context authority mismatch"
            )
        if item.get("executable") is not False:
            raise ProcedureBoundaryError(
                "knowledge unit executable must remain exactly false"
            )
        unit_id = item.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise ProcedureBoundaryError(
                "reasoning context requires unit_id"
            )
        if unit_id in seen:
            raise ProcedureBoundaryError(
                "reasoning context unit_ids must be unique"
            )
        seen.add(unit_id)
        validated.append(item)
    return validated


def project_procedure_boundary(
    load_result: Mapping[str, Any],
) -> dict[str, Any]:
    """Project selected procedure units as descriptive, zero-authority data."""

    context = _validate_load_result(load_result)

    procedures: list[dict[str, Any]] = []
    for item in context:
        if item.get("kind") != PROCEDURE_KIND:
            continue

        procedures.append({
            "unit_id": item["unit_id"],
            "skill_id": item.get("skill_id"),
            "skill_version": item.get("skill_version"),
            "claim_key": item.get("claim_key"),
            "kind": PROCEDURE_KIND,
            "content": item.get("content"),
            "source_bindings": deepcopy(item.get("source_bindings")),
            "trust_class": TRUST_CLASS,
            "instruction_authority": INSTRUCTION_AUTHORITY,
            "authority": AUTHORITY,
            "executable": False,
            "execution_authority": EXECUTION_AUTHORITY,
            "capability_grants": [],
            "tool_grants": [],
            "requires_external_authority_for_execution": True,
        })

    effective_tools = load_result.get("effective_tools")
    if not isinstance(effective_tools, list):
        raise ProcedureBoundaryError(
            "load result effective_tools must be a list"
        )
    if any(not isinstance(tool, str) or not tool for tool in effective_tools):
        raise ProcedureBoundaryError(
            "load result effective_tools contains invalid tool name"
        )

    return {
        "schema_version": PROCEDURE_BOUNDARY_SCHEMA_VERSION,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "authority_source": AUTHORITY_SOURCE,
        "runtime_effective_tools": sorted(dict.fromkeys(effective_tools)),
        "procedure_tool_grants": [],
        "procedure_capability_grants": [],
        "procedure_count": len(procedures),
        "procedures": procedures,
    }


def evaluate_procedure_capability_request(
    load_result: Mapping[str, Any],
    *,
    capability: str,
) -> dict[str, Any]:
    """Return a deterministic denial of procedure-origin execution authority."""

    projection = project_procedure_boundary(load_result)

    if not isinstance(capability, str) or not capability.strip():
        raise ProcedureBoundaryError(
            "capability must be a non-empty string"
        )
    requested = capability.strip().upper()
    if requested not in SENSITIVE_CAPABILITIES:
        raise ProcedureBoundaryError(
            "unsupported sensitive capability: " + requested
        )
    if not projection["procedures"]:
        raise ProcedureBoundaryError(
            "no PROCEDURE unit is loaded"
        )

    return {
        "schema_version": PROCEDURE_BOUNDARY_SCHEMA_VERSION,
        "requested_capability": requested,
        "authorized_by_procedure": False,
        "execution_authority": EXECUTION_AUTHORITY,
        "capability_grants": [],
        "reason": "PROCEDURE_KNOWLEDGE_HAS_ZERO_EXECUTION_AUTHORITY",
        "authority_source_required": AUTHORITY_SOURCE,
        "procedure_unit_ids": [
            item["unit_id"]
            for item in projection["procedures"]
        ],
    }
