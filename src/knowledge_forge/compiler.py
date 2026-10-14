"""Provider-neutral semantic compiler boundary.

This module defines the handoff between deterministic normalized source data and
any semantic compiler implementation. Provider/model provenance is explicit data
and never used as execution authority.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .canonical_boundary import CanonicalBoundaryError, require_ordinary_skill_role
from .provenance import (
    ProvenanceError,
    normalize_compiler_profile,
    normalize_knowledge_id,
)

COMPILER_CONTRACT_VERSION = "0.2"


class CompilerContractError(ValueError):
    """Raised when an adapter or compilation envelope violates the contract."""


@runtime_checkable
class CompilerAdapter(Protocol):
    """Minimum interface implemented by any semantic compiler provider."""

    adapter_id: str

    def compile(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """Compile a read-only semantic request into a provider-neutral candidate."""


def _knowledge_id(value: Any) -> str:
    try:
        return normalize_knowledge_id(value)
    except ProvenanceError as exc:
        raise CompilerContractError(str(exc)) from exc


def _profile(value: Any) -> dict[str, Any]:
    try:
        return normalize_compiler_profile(value)
    except ProvenanceError as exc:
        raise CompilerContractError(str(exc)) from exc


def build_compile_request_from_sources(
    normalized_sources: Sequence[Mapping[str, Any]],
    *,
    knowledge_id: str,
    skill_slug: str,
    compiler_profile: Mapping[str, Any],
    purpose: str = "reference",
) -> dict[str, Any]:
    "Create a deterministic multi-source compile request."

    if not isinstance(normalized_sources, Sequence) or isinstance(
        normalized_sources, (str, bytes, bytearray)
    ):
        raise CompilerContractError("normalized_sources must be a sequence")
    if not normalized_sources:
        raise CompilerContractError(
            "compile request requires at least one normalized source"
        )
    if not skill_slug or "/" in skill_slug or "\\" in skill_slug:
        raise CompilerContractError(
            "skill_slug must be a non-empty single path component"
        )
    if purpose not in {"reference", "study"}:
        raise CompilerContractError("purpose must be reference or study")

    bound_sources: list[dict[str, Any]] = []
    seen_sections: dict[str, list[Any]] = {}
    for normalized_source in normalized_sources:
        if not isinstance(normalized_source, Mapping):
            raise CompilerContractError("normalized sources must be mappings")

        source_id = normalized_source.get("source_id")
        try:
            source_role = require_ordinary_skill_role(normalized_source.get("source_role"))
        except CanonicalBoundaryError as exc:
            raise CompilerContractError(str(exc)) from exc
        sections = normalized_source.get("sections")
        if not isinstance(source_id, str) or not source_id.startswith("sha256:"):
            raise CompilerContractError("normalized source has no valid source_id")
        if not isinstance(sections, list):
            raise CompilerContractError("normalized source sections must be a list")

        copied_sections = deepcopy(sections)
        previous = seen_sections.get(source_id)
        if previous is not None:
            if previous != copied_sections:
                raise CompilerContractError(
                    "duplicate source_id has conflicting normalized sections"
                )
            continue
        seen_sections[source_id] = copied_sections
        bound_sources.append({"source_id": source_id, "source_role": source_role, "sections": copied_sections})

    return {
        "contract_version": COMPILER_CONTRACT_VERSION,
        "knowledge_id": _knowledge_id(knowledge_id),
        "skill": {"slug": skill_slug, "purpose": purpose},
        "compiler_profile": _profile(compiler_profile),
        "sources": bound_sources,
        "authority": "KNOWLEDGE_ONLY",
    }


def build_compile_request(
    normalized_source: Mapping[str, Any],
    *,
    knowledge_id: str,
    skill_slug: str,
    compiler_profile: Mapping[str, Any],
    purpose: str = "reference",
) -> dict[str, Any]:
    "Backward-compatible single-source wrapper."

    return build_compile_request_from_sources(
        [normalized_source],
        knowledge_id=knowledge_id,
        skill_slug=skill_slug,
        compiler_profile=compiler_profile,
        purpose=purpose,
    )


def invoke_compiler(
    adapter: CompilerAdapter,
    request: Mapping[str, Any],
) -> dict[str, Any]:
    """Invoke an adapter while binding result provenance to the caller request."""

    adapter_id = getattr(adapter, "adapter_id", None)
    if not isinstance(adapter_id, str) or not adapter_id.strip():
        raise CompilerContractError("compiler adapter requires a stable adapter_id")

    knowledge_id = _knowledge_id(request.get("knowledge_id"))
    compiler_profile = _profile(request.get("compiler_profile"))
    source_ids = [
        source.get("source_id")
        for source in request.get("sources", [])
        if isinstance(source, Mapping)
    ]
    if not source_ids or any(not isinstance(value, str) for value in source_ids):
        raise CompilerContractError("compile request requires source identities")

    candidate = adapter.compile(deepcopy(dict(request)))
    if not isinstance(candidate, Mapping):
        raise CompilerContractError("compiler adapter must return a mapping")

    return {
        "contract_version": COMPILER_CONTRACT_VERSION,
        "adapter_id": adapter_id.strip(),
        "knowledge_id": knowledge_id,
        "compiler_profile": compiler_profile,
        "request_source_ids": list(source_ids),
        "authority": "KNOWLEDGE_ONLY",
        "candidate": deepcopy(dict(candidate)),
    }
