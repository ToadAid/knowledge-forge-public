"""Backward-compatible Agent0 binding over the general discovery core."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any, Mapping

from .consumer_discovery import (
    AUTHORITY,
    DISCOVERY_SURFACE,
    EXECUTION_AUTHORITY,
    GLOBAL_HARD_MAX_SKILLS_PER_QUERY,
    ConsumerDiscoveryError,
    build_discovery_descriptors,
    route_discovery_descriptors,
    validate_discovery_descriptors,
)

AGENT0_DISCOVERY_SCHEMA_VERSION = "0.1"
AGENT0_CONSUMER_ID = "agent0"
AGENT0_RUNTIME_REPO = "ToadAid/trading-desk"
AGENT0_DISCOVERY_SURFACE = DISCOVERY_SURFACE
PROCEDURAL_PLAYBOOK_BOUNDARY = "SEPARATE"
HARD_MAX_SKILLS_PER_QUERY = GLOBAL_HARD_MAX_SKILLS_PER_QUERY


class Agent0DiscoveryError(ConsumerDiscoveryError):
    """Agent0-specific compatibility error."""


def build_agent0_discovery_catalog(
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        skills = build_discovery_descriptors(registry)
    except ConsumerDiscoveryError as exc:
        raise Agent0DiscoveryError(str(exc)) from exc
    return {
        "schema_version": AGENT0_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": AGENT0_CONSUMER_ID,
        "consumer_runtime_repo": AGENT0_RUNTIME_REPO,
        "surface": AGENT0_DISCOVERY_SURFACE,
        "procedural_playbook_boundary": PROCEDURAL_PLAYBOOK_BOUNDARY,
        "hard_max_skills_per_query": HARD_MAX_SKILLS_PER_QUERY,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
        "skills": skills,
    }


def validate_agent0_discovery_catalog(
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    expected_top = {
        "schema_version",
        "consumer_id",
        "consumer_runtime_repo",
        "surface",
        "procedural_playbook_boundary",
        "hard_max_skills_per_query",
        "authority",
        "execution_authority",
        "tool_grants",
        "skills",
    }
    if not isinstance(catalog, Mapping):
        return {"valid": False, "errors": ["catalog must be a mapping"]}
    if set(catalog) != expected_top:
        errors.append("catalog fields must match exact Agent0 contract")
    expected_values = {
        "schema_version": AGENT0_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": AGENT0_CONSUMER_ID,
        "consumer_runtime_repo": AGENT0_RUNTIME_REPO,
        "surface": AGENT0_DISCOVERY_SURFACE,
        "procedural_playbook_boundary": PROCEDURAL_PLAYBOOK_BOUNDARY,
        "hard_max_skills_per_query": HARD_MAX_SKILLS_PER_QUERY,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }
    for key, expected in expected_values.items():
        if catalog.get(key) != expected:
            errors.append(f"catalog {key} mismatch")
    descriptor_report = validate_discovery_descriptors(catalog.get("skills"))
    errors.extend(descriptor_report["errors"])
    return {"valid": not errors, "errors": errors}


def route_agent0_knowledge(
    catalog: Mapping[str, Any],
    question: str,
    *,
    max_skills: int = 1,
) -> dict[str, Any]:
    report = validate_agent0_discovery_catalog(catalog)
    if not report["valid"]:
        raise Agent0DiscoveryError(
            "cannot route from invalid Agent0 catalog: "
            + "; ".join(report["errors"])
        )
    try:
        selected = route_discovery_descriptors(
            catalog["skills"],
            question,
            consumer_id=AGENT0_CONSUMER_ID,
            max_skills=max_skills,
            hard_max_skills_per_query=HARD_MAX_SKILLS_PER_QUERY,
        )
    except ConsumerDiscoveryError as exc:
        raise Agent0DiscoveryError(str(exc)) from exc
    return {
        "schema_version": AGENT0_DISCOVERY_SCHEMA_VERSION,
        "consumer_id": AGENT0_CONSUMER_ID,
        "surface": AGENT0_DISCOVERY_SURFACE,
        "question_sha256": (
            "sha256:" + sha256(question.encode("utf-8")).hexdigest()
        ),
        "selected_skill_ids": [item["skill_id"] for item in selected],
        "selected_skills": selected,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
    }


def agent0_catalog_bytes(catalog: Mapping[str, Any]) -> bytes:
    report = validate_agent0_discovery_catalog(catalog)
    if not report["valid"]:
        raise Agent0DiscoveryError(
            "cannot serialize invalid Agent0 catalog: "
            + "; ".join(report["errors"])
        )
    return (
        json.dumps(catalog, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode("utf-8")
