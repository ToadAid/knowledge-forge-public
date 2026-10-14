"""Thin Agent1 binding over provider-neutral governed discovery.

Agent1's current Stage-0 runtime remains authoritative for its own runnable tool
roster. Knowledge Forge discovery is an external read-only knowledge surface:
it does not join that roster, write Agent1 journal/memory, or materialize files
into ``agent1/skills``. P4C will own trusted unit-body loading later.
"""

from __future__ import annotations

from typing import Any, Mapping

from .consumer_discovery import (
    ConsumerBinding,
    ConsumerDiscoveryError,
    build_consumer_discovery_catalog,
    consumer_catalog_bytes,
    route_consumer_knowledge,
    validate_consumer_discovery_catalog,
)

AGENT1_CONSUMER_ID = "agent1"
AGENT1_RUNTIME_REPO = "ToadAid/frog-to-toad"
AGENT1_RUNTIME_ENTRYPOINT = "agent1/runtime.mjs"
AGENT1_INTEGRATION_BOUNDARY = (
    "EXTERNAL_DISCOVERY_ONLY_STAGE0_TOOL_ROSTER_UNCHANGED_NO_SKILL_MATERIALIZATION"
)
AGENT1_BINDING = ConsumerBinding(
    consumer_id=AGENT1_CONSUMER_ID,
    runtime_repo=AGENT1_RUNTIME_REPO,
    runtime_entrypoint=AGENT1_RUNTIME_ENTRYPOINT,
    integration_boundary=AGENT1_INTEGRATION_BOUNDARY,
    hard_max_skills_per_query=3,
)


class Agent1DiscoveryError(ConsumerDiscoveryError):
    """Agent1-specific discovery error."""


def build_agent1_discovery_catalog(
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        return build_consumer_discovery_catalog(registry, AGENT1_BINDING)
    except ConsumerDiscoveryError as exc:
        raise Agent1DiscoveryError(str(exc)) from exc


def validate_agent1_discovery_catalog(
    catalog: Mapping[str, Any],
) -> dict[str, Any]:
    return validate_consumer_discovery_catalog(catalog, AGENT1_BINDING)


def route_agent1_knowledge(
    catalog: Mapping[str, Any],
    question: str,
    *,
    max_skills: int = 1,
) -> dict[str, Any]:
    try:
        return route_consumer_knowledge(
            catalog,
            AGENT1_BINDING,
            question,
            max_skills=max_skills,
        )
    except ConsumerDiscoveryError as exc:
        raise Agent1DiscoveryError(str(exc)) from exc


def agent1_catalog_bytes(catalog: Mapping[str, Any]) -> bytes:
    try:
        return consumer_catalog_bytes(catalog, AGENT1_BINDING)
    except ConsumerDiscoveryError as exc:
        raise Agent1DiscoveryError(str(exc)) from exc
