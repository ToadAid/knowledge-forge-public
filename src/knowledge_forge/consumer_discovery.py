"""Provider-neutral governed-knowledge discovery for agent consumers.

This module is the reusable P4 discovery core. Consumer adapters bind runtime
identity and boundary policy, while this core owns the invariant projection:
current PROMOTED knowledge only, identity/routing metadata only, no claim values
or skill bodies, no tool grants, and no execution authority.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .lifecycle import PROMOTED
from .registry import validate_skill_registry

CONSUMER_DISCOVERY_SCHEMA_VERSION = "0.1"
DISCOVERY_SURFACE = "GOVERNED_KNOWLEDGE_DISCOVERY"
AUTHORITY = "KNOWLEDGE_ONLY"
EXECUTION_AUTHORITY = "NONE"
GLOBAL_HARD_MAX_SKILLS_PER_QUERY = 3

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "the",
    "to", "what", "when", "where", "which", "who", "why", "with",
})


class ConsumerDiscoveryError(ValueError):
    """Raised when governed discovery cannot remain bounded and authority-safe."""


@dataclass(frozen=True)
class ConsumerBinding:
    consumer_id: str
    runtime_repo: str
    runtime_entrypoint: str
    integration_boundary: str
    hard_max_skills_per_query: int = GLOBAL_HARD_MAX_SKILLS_PER_QUERY


def validate_consumer_binding(binding: ConsumerBinding) -> None:
    for label, value in (
        ("consumer_id", binding.consumer_id),
        ("runtime_repo", binding.runtime_repo),
        ("runtime_entrypoint", binding.runtime_entrypoint),
        ("integration_boundary", binding.integration_boundary),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ConsumerDiscoveryError(f"{label} must be a non-empty string")
    maximum = binding.hard_max_skills_per_query
    if (
        not isinstance(maximum, int)
        or isinstance(maximum, bool)
        or not 1 <= maximum <= GLOBAL_HARD_MAX_SKILLS_PER_QUERY
    ):
        raise ConsumerDiscoveryError(
            "consumer hard max must be between 1 and "
            f"{GLOBAL_HARD_MAX_SKILLS_PER_QUERY}"
        )


def _normalized_phrase(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip().lower())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN_RE.findall(value.lower())
        if token not in _STOPWORDS
    )


def _route_aliases(entry: Mapping[str, Any]) -> list[str]:
    aliases: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        normalized = _normalized_phrase(
            value.replace("_", " ").replace(".", " ").replace(":", " ")
        )
        if normalized and normalized not in aliases:
            aliases.append(normalized)

    add(entry.get("title"))
    add(entry.get("purpose"))
    add(entry.get("knowledge_id"))

    claim_index = entry.get("claim_index")
    claims = claim_index.get("claims") if isinstance(claim_index, Mapping) else None
    if not isinstance(claims, list):
        raise ConsumerDiscoveryError("registry entry claim_index requires claims")

    for item in claims:
        if not isinstance(item, Mapping):
            raise ConsumerDiscoveryError("claim index entry must be a mapping")
        add(item.get("claim_key"))
        add(item.get("kind"))
        claim = item.get("claim")
        if not isinstance(claim, Mapping):
            raise ConsumerDiscoveryError(
                "claim index entry requires structured claim"
            )
        add(claim.get("subject"))
        add(claim.get("predicate"))

    return aliases


def _unit_routes(entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    claim_index = entry.get("claim_index")
    claims = claim_index.get("claims") if isinstance(claim_index, Mapping) else None
    if not isinstance(claims, list) or not claims:
        raise ConsumerDiscoveryError("registry entry claim_index requires claims")

    routes: list[dict[str, Any]] = []
    for item in claims:
        if not isinstance(item, Mapping):
            raise ConsumerDiscoveryError("claim index entry must be a mapping")
        claim = item.get("claim")
        if not isinstance(claim, Mapping):
            raise ConsumerDiscoveryError(
                "claim index entry requires structured claim"
            )
        if claim.get("executable") is not False:
            raise ConsumerDiscoveryError(
                "consumer discovery accepts non-executable claims only"
            )
        routes.append({
            "unit_id": item.get("unit_id"),
            "claim_key": item.get("claim_key"),
            "kind": item.get("kind"),
            "subject": claim.get("subject"),
            "predicate": claim.get("predicate"),
            "executable": False,
        })

    routes.sort(key=lambda item: str(item["unit_id"]))
    return routes


def _descriptor(entry: Mapping[str, Any]) -> dict[str, Any]:
    aliases = _route_aliases(entry)
    terms = sorted({
        token
        for alias in aliases
        for token in _tokens(alias)
    })
    if not terms:
        raise ConsumerDiscoveryError(
            f"skill has no discovery routing terms: {entry.get('skill_id')}"
        )

    return {
        "skill_id": entry.get("skill_id"),
        "skill_version": entry.get("skill_version"),
        "knowledge_id": entry.get("knowledge_id"),
        "title": entry.get("title"),
        "purpose": entry.get("purpose"),
        "compiler_profile_id": entry.get("compiler_profile_id"),
        "package_ref": entry.get("package_ref"),
        "claim_index_sha256": entry.get("claim_index_sha256"),
        "lifecycle_head_sha256": entry.get("lifecycle_head_sha256"),
        "source_ids": deepcopy(entry.get("source_ids")),
        "route_aliases": aliases,
        "route_terms": terms,
        "unit_routes": _unit_routes(entry),
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }


DESCRIPTOR_FIELDS = {
    "skill_id",
    "skill_version",
    "knowledge_id",
    "title",
    "purpose",
    "compiler_profile_id",
    "package_ref",
    "claim_index_sha256",
    "lifecycle_head_sha256",
    "source_ids",
    "route_aliases",
    "route_terms",
    "unit_routes",
    "authority",
    "execution_authority",
    "tool_grants",
}
ROUTE_FIELDS = {
    "unit_id",
    "claim_key",
    "kind",
    "subject",
    "predicate",
    "executable",
}


def build_discovery_descriptors(
    registry: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project current PROMOTED registry truth into consumer-neutral descriptors."""

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise ConsumerDiscoveryError(
            "cannot build discovery from invalid registry: "
            + "; ".join(report["errors"])
        )

    skills = registry.get("skills")
    if not isinstance(skills, Mapping):
        raise ConsumerDiscoveryError("registry skills must be a mapping")

    descriptors: list[dict[str, Any]] = []
    for skill_id in sorted(skills):
        record = skills[skill_id]
        if not isinstance(record, Mapping):
            raise ConsumerDiscoveryError("registry skill record must be a mapping")
        versions = record.get("versions")
        if not isinstance(versions, Mapping):
            raise ConsumerDiscoveryError("registry versions must be a mapping")
        entry = versions.get(str(record.get("current_version")))
        if not isinstance(entry, Mapping):
            raise ConsumerDiscoveryError("registry current version is missing")
        if entry.get("state") != PROMOTED:
            continue
        descriptors.append(_descriptor(entry))

    descriptors.sort(key=lambda item: item["skill_id"])
    return descriptors


def validate_discovery_descriptors(skills: Any) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(skills, list):
        return {"valid": False, "errors": ["discovery skills must be a list"]}

    ids: list[str] = []
    for item in skills:
        if not isinstance(item, Mapping):
            errors.append("discovery descriptor must be a mapping")
            continue
        if set(item) != DESCRIPTOR_FIELDS:
            errors.append("discovery descriptor fields mismatch")
            continue

        skill_id = item.get("skill_id")
        if not isinstance(skill_id, str) or not skill_id:
            errors.append("discovery skill_id is invalid")
        else:
            ids.append(skill_id)

        if item.get("authority") != AUTHORITY:
            errors.append(f"discovery authority mismatch: {skill_id}")
        if item.get("execution_authority") != EXECUTION_AUTHORITY:
            errors.append(f"discovery execution authority mismatch: {skill_id}")
        if item.get("tool_grants") != []:
            errors.append(f"discovery descriptor must not grant tools: {skill_id}")

        aliases = item.get("route_aliases")
        terms = item.get("route_terms")
        if (
            not isinstance(aliases, list)
            or not aliases
            or any(not isinstance(alias, str) or not alias for alias in aliases)
        ):
            errors.append(f"discovery route_aliases invalid: {skill_id}")
        if (
            not isinstance(terms, list)
            or not terms
            or terms != sorted(set(terms))
            or any(not isinstance(term, str) or not term for term in terms)
        ):
            errors.append(f"discovery route_terms invalid: {skill_id}")

        routes = item.get("unit_routes")
        if not isinstance(routes, list) or not routes:
            errors.append(f"discovery unit_routes invalid: {skill_id}")
            continue
        for route in routes:
            if not isinstance(route, Mapping) or set(route) != ROUTE_FIELDS:
                errors.append(f"discovery unit route fields mismatch: {skill_id}")
                continue
            if route.get("executable") is not False:
                errors.append(f"discovery unit route became executable: {skill_id}")

    if ids != sorted(ids) or len(ids) != len(set(ids)):
        errors.append("discovery skill ids must be unique and sorted")

    return {"valid": not errors, "errors": errors}


def route_discovery_descriptors(
    skills: Any,
    question: str,
    *,
    consumer_id: str,
    max_skills: int = 1,
    hard_max_skills_per_query: int = GLOBAL_HARD_MAX_SKILLS_PER_QUERY,
) -> list[dict[str, Any]]:
    """Select bounded skill identities from descriptors; never load unit bodies."""

    report = validate_discovery_descriptors(skills)
    if not report["valid"]:
        raise ConsumerDiscoveryError(
            "cannot route invalid discovery descriptors: "
            + "; ".join(report["errors"])
        )
    if not isinstance(consumer_id, str) or not consumer_id.strip():
        raise ConsumerDiscoveryError("consumer_id must be non-empty")
    if not isinstance(question, str) or not question.strip():
        raise ConsumerDiscoveryError("question must be non-empty")
    if (
        not isinstance(hard_max_skills_per_query, int)
        or isinstance(hard_max_skills_per_query, bool)
        or not 1 <= hard_max_skills_per_query <= GLOBAL_HARD_MAX_SKILLS_PER_QUERY
    ):
        raise ConsumerDiscoveryError(
            "consumer hard max must be between 1 and "
            f"{GLOBAL_HARD_MAX_SKILLS_PER_QUERY}"
        )
    if (
        not isinstance(max_skills, int)
        or isinstance(max_skills, bool)
        or not 1 <= max_skills <= hard_max_skills_per_query
    ):
        raise ConsumerDiscoveryError(
            f"max_skills must be between 1 and {hard_max_skills_per_query}"
        )

    query_phrase = _normalized_phrase(question)
    query_tokens = set(_tokens(question))
    if not query_tokens:
        raise ConsumerDiscoveryError("question contains no discovery terms")

    scored: list[tuple[int, int, str, Mapping[str, Any]]] = []
    for item in skills:
        exact_alias = max(
            (
                len(_tokens(alias))
                for alias in item["route_aliases"]
                if _normalized_phrase(alias) in query_phrase
            ),
            default=0,
        )
        overlap = len(query_tokens & set(item["route_terms"]))
        if exact_alias or overlap:
            scored.append((exact_alias, overlap, item["skill_id"], item))

    if not scored:
        raise ConsumerDiscoveryError(
            f"no governed knowledge skill matched {consumer_id} discovery query"
        )

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [deepcopy(dict(item[3])) for item in scored[:max_skills]]


def build_consumer_discovery_catalog(
    registry: Mapping[str, Any],
    binding: ConsumerBinding,
) -> dict[str, Any]:
    validate_consumer_binding(binding)
    return {
        "schema_version": CONSUMER_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": binding.consumer_id,
        "consumer_runtime_repo": binding.runtime_repo,
        "consumer_runtime_entrypoint": binding.runtime_entrypoint,
        "surface": DISCOVERY_SURFACE,
        "integration_boundary": binding.integration_boundary,
        "hard_max_skills_per_query": binding.hard_max_skills_per_query,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
        "skills": build_discovery_descriptors(registry),
    }


def validate_consumer_discovery_catalog(
    catalog: Mapping[str, Any],
    binding: ConsumerBinding,
) -> dict[str, Any]:
    try:
        validate_consumer_binding(binding)
    except ConsumerDiscoveryError as exc:
        return {"valid": False, "errors": [str(exc)]}

    errors: list[str] = []
    expected = {
        "schema_version",
        "consumer_id",
        "consumer_runtime_repo",
        "consumer_runtime_entrypoint",
        "surface",
        "integration_boundary",
        "hard_max_skills_per_query",
        "authority",
        "execution_authority",
        "tool_grants",
        "skills",
    }
    if not isinstance(catalog, Mapping):
        return {"valid": False, "errors": ["catalog must be a mapping"]}
    if set(catalog) != expected:
        errors.append("catalog fields must match exact consumer contract")
    expected_values = {
        "schema_version": CONSUMER_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": binding.consumer_id,
        "consumer_runtime_repo": binding.runtime_repo,
        "consumer_runtime_entrypoint": binding.runtime_entrypoint,
        "surface": DISCOVERY_SURFACE,
        "integration_boundary": binding.integration_boundary,
        "hard_max_skills_per_query": binding.hard_max_skills_per_query,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }
    for key, expected_value in expected_values.items():
        if catalog.get(key) != expected_value:
            errors.append(f"catalog {key} mismatch")

    descriptor_report = validate_discovery_descriptors(catalog.get("skills"))
    errors.extend(descriptor_report["errors"])
    return {"valid": not errors, "errors": errors}


def route_consumer_knowledge(
    catalog: Mapping[str, Any],
    binding: ConsumerBinding,
    question: str,
    *,
    max_skills: int = 1,
) -> dict[str, Any]:
    report = validate_consumer_discovery_catalog(catalog, binding)
    if not report["valid"]:
        raise ConsumerDiscoveryError(
            "cannot route invalid consumer catalog: "
            + "; ".join(report["errors"])
        )
    selected = route_discovery_descriptors(
        catalog["skills"],
        question,
        consumer_id=binding.consumer_id,
        max_skills=max_skills,
        hard_max_skills_per_query=binding.hard_max_skills_per_query,
    )
    return {
        "schema_version": CONSUMER_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": binding.consumer_id,
        "surface": DISCOVERY_SURFACE,
        "question_sha256": (
            "sha256:" + sha256(question.encode("utf-8")).hexdigest()
        ),
        "selected_skill_ids": [item["skill_id"] for item in selected],
        "selected_skills": selected,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }


def consumer_catalog_bytes(
    catalog: Mapping[str, Any],
    binding: ConsumerBinding,
) -> bytes:
    report = validate_consumer_discovery_catalog(catalog, binding)
    if not report["valid"]:
        raise ConsumerDiscoveryError(
            "cannot serialize invalid consumer catalog: "
            + "; ".join(report["errors"])
        )
    return (
        json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
