from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.agent1_discovery import (
    AGENT1_INTEGRATION_BOUNDARY,
    AGENT1_RUNTIME_ENTRYPOINT,
    AGENT1_RUNTIME_REPO,
    Agent1DiscoveryError,
    agent1_catalog_bytes,
    build_agent1_discovery_catalog,
    route_agent1_knowledge,
    validate_agent1_discovery_catalog,
)
from knowledge_forge.lifecycle import SUSPENDED, transition_lifecycle
from knowledge_forge.registry import (
    create_skill_registry,
    fold_in_current_incident_lifecycle,
    register_promoted_skill,
)
from tests.test_registry import promoted_fixture


def register(
    registry,
    *,
    knowledge_id,
    value,
    subject,
    at="t1",
):
    fixture = promoted_fixture(
        knowledge_id=knowledge_id,
        value=value,
        subject=subject,
    )
    updated = register_promoted_skill(
        registry,
        *fixture,
        registered_at=at,
    )
    return updated, fixture


class Agent1DiscoveryTests(unittest.TestCase):
    def test_catalog_binds_real_agent1_runtime_without_joining_tool_roster(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:observation",
            value=5,
            subject="market.observation",
        )

        catalog = build_agent1_discovery_catalog(registry)

        self.assertTrue(validate_agent1_discovery_catalog(catalog)["valid"])
        self.assertEqual(AGENT1_RUNTIME_REPO, "ToadAid/frog-to-toad")
        self.assertEqual(AGENT1_RUNTIME_ENTRYPOINT, "agent1/runtime.mjs")
        self.assertEqual(
            catalog["integration_boundary"],
            AGENT1_INTEGRATION_BOUNDARY,
        )
        self.assertIn("STAGE0_TOOL_ROSTER_UNCHANGED", AGENT1_INTEGRATION_BOUNDARY)
        self.assertIn("NO_SKILL_MATERIALIZATION", AGENT1_INTEGRATION_BOUNDARY)
        self.assertEqual(catalog["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(catalog["execution_authority"], "NONE")
        self.assertEqual(catalog["tool_grants"], [])

    def test_catalog_does_not_smuggle_stage0_tools_or_memory_writes(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:risk",
            value="SECRET-VALUE",
            subject="risk.limit",
        )

        payload = agent1_catalog_bytes(
            build_agent1_discovery_catalog(registry)
        ).decode("utf-8")

        self.assertNotIn("SECRET-VALUE", payload)
        self.assertNotIn("market_price", payload)
        self.assertNotIn("journal_append", payload)
        self.assertNotIn("memory_save", payload)
        self.assertNotIn("agent1/skills", payload)

    def test_suspended_current_skill_is_absent_from_agent1_discovery(self):
        registry = create_skill_registry(created_at="t0")
        registry, fixture = register(
            registry,
            knowledge_id="knowledge:risk",
            value=5,
            subject="risk.limit",
        )
        lifecycle = transition_lifecycle(
            fixture[1],
            SUSPENDED,
            occurred_at="t2",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:agent1-discovery",
                "reason": "test suspension",
            },
        )
        registry = fold_in_current_incident_lifecycle(
            registry,
            lifecycle,
            updated_at="t2",
        )

        catalog = build_agent1_discovery_catalog(registry)

        self.assertEqual(catalog["skills"], [])
        with self.assertRaisesRegex(
            Agent1DiscoveryError,
            "no governed knowledge skill matched",
        ):
            route_agent1_knowledge(catalog, "risk limit")

    def test_agent1_routes_identity_only_and_cannot_widen_max(self):
        registry = create_skill_registry(created_at="t0")
        registry, fixture = register(
            registry,
            knowledge_id="knowledge:risk",
            value=12,
            subject="risk.limit",
        )
        catalog = build_agent1_discovery_catalog(registry)

        routed = route_agent1_knowledge(
            catalog,
            "What is the risk limit?",
        )

        self.assertEqual(
            routed["selected_skill_ids"],
            [fixture[0]["skill_id"]],
        )
        self.assertEqual(routed["tool_grants"], [])
        self.assertNotIn("content", routed["selected_skills"][0])

        with self.assertRaisesRegex(
            Agent1DiscoveryError,
            "max_skills must be between 1 and 3",
        ):
            route_agent1_knowledge(
                catalog,
                "risk limit",
                max_skills=4,
            )

    def test_invalid_registry_refuses_before_agent1_projection(self):
        registry = create_skill_registry(created_at="t0")
        registry, fixture = register(
            registry,
            knowledge_id="knowledge:risk",
            value=5,
            subject="risk.limit",
        )
        bad = deepcopy(registry)
        skill_id = fixture[0]["skill_id"]
        bad["skills"][skill_id]["versions"]["1"]["authority"] = "EXECUTE"

        with self.assertRaisesRegex(
            Agent1DiscoveryError,
            "invalid registry",
        ):
            build_agent1_discovery_catalog(bad)

    def test_agent1_catalog_is_deterministic(self):
        alpha = promoted_fixture(
            knowledge_id="knowledge:alpha",
            value=1,
            subject="alpha.topic",
        )
        beta = promoted_fixture(
            knowledge_id="knowledge:beta",
            value=2,
            subject="beta.topic",
        )

        first = create_skill_registry(created_at="t0")
        first = register_promoted_skill(first, *alpha, registered_at="t1")
        first = register_promoted_skill(first, *beta, registered_at="t2")

        second = create_skill_registry(created_at="t0")
        second = register_promoted_skill(second, *beta, registered_at="t1")
        second = register_promoted_skill(second, *alpha, registered_at="t2")

        self.assertEqual(
            agent1_catalog_bytes(build_agent1_discovery_catalog(first)),
            agent1_catalog_bytes(build_agent1_discovery_catalog(second)),
        )


if __name__ == "__main__":
    unittest.main()
