from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.agent0_discovery import (
    AGENT0_RUNTIME_REPO,
    Agent0DiscoveryError,
    agent0_catalog_bytes,
    build_agent0_discovery_catalog,
    route_agent0_knowledge,
    validate_agent0_discovery_catalog,
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
    predicate="equals",
    at="t1",
):
    fixture = promoted_fixture(
        knowledge_id=knowledge_id,
        value=value,
        subject=subject,
        predicate=predicate,
    )
    updated = register_promoted_skill(
        registry,
        *fixture,
        registered_at=at,
    )
    return updated, fixture


class Agent0DiscoveryTests(unittest.TestCase):
    def test_catalog_is_bound_to_real_agent0_runtime_and_grants_no_authority(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:market-risk",
            value=5,
            subject="risk.limit",
        )

        catalog = build_agent0_discovery_catalog(registry)

        self.assertTrue(validate_agent0_discovery_catalog(catalog)["valid"])
        self.assertEqual(catalog["consumer_id"], "agent0")
        self.assertEqual(catalog["consumer_runtime_repo"], AGENT0_RUNTIME_REPO)
        self.assertEqual(AGENT0_RUNTIME_REPO, "ToadAid/trading-desk")
        self.assertEqual(
            catalog["procedural_playbook_boundary"],
            "SEPARATE",
        )
        self.assertEqual(catalog["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(catalog["execution_authority"], "NONE")
        self.assertEqual(catalog["tool_grants"], [])

    def test_catalog_contains_discovery_metadata_not_claim_values_or_skill_bodies(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:market-risk",
            value="SECRET-VALUE-NOT-FOR-DISCOVERY",
            subject="risk.limit",
        )

        descriptor = build_agent0_discovery_catalog(registry)["skills"][0]

        self.assertEqual(
            set(descriptor),
            {
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
            },
        )
        self.assertEqual(
            set(descriptor["unit_routes"][0]),
            {
                "unit_id",
                "claim_key",
                "kind",
                "subject",
                "predicate",
                "executable",
            },
        )
        serialized = agent0_catalog_bytes(
            build_agent0_discovery_catalog(registry)
        ).decode("utf-8")
        self.assertNotIn("SECRET-VALUE-NOT-FOR-DISCOVERY", serialized)
        self.assertNotIn("source_receipt", serialized)
        self.assertNotIn("lifecycle_record", serialized)

    def test_suspended_current_skill_disappears_from_default_agent0_discovery(self):
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
                "incident_ref": "incident:agent0-discovery",
                "reason": "test suspension",
            },
        )
        stopped = fold_in_current_incident_lifecycle(
            registry,
            lifecycle,
            updated_at="t2",
        )

        catalog = build_agent0_discovery_catalog(stopped)

        self.assertEqual(catalog["skills"], [])
        with self.assertRaisesRegex(
            Agent0DiscoveryError,
            "no governed knowledge skill matched",
        ):
            route_agent0_knowledge(catalog, "risk limit")

    def test_route_uses_identity_metadata_and_returns_skill_id_only_boundary(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:fees",
            value=5,
            subject="protocol.fee",
            at="t1",
        )
        registry, risk_fixture = register(
            registry,
            knowledge_id="knowledge:risk",
            value=12,
            subject="risk.limit",
            at="t2",
        )

        catalog = build_agent0_discovery_catalog(registry)
        routed = route_agent0_knowledge(
            catalog,
            "What is the risk limit?",
            max_skills=1,
        )

        self.assertEqual(
            routed["selected_skill_ids"],
            [risk_fixture[0]["skill_id"]],
        )
        self.assertEqual(routed["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(routed["execution_authority"], "NONE")
        self.assertEqual(routed["tool_grants"], [])
        self.assertNotIn("content", routed["selected_skills"][0])

    def test_claim_value_does_not_participate_in_agent0_routing(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:risk",
            value=999999,
            subject="risk.limit",
        )
        catalog = build_agent0_discovery_catalog(registry)

        with self.assertRaisesRegex(
            Agent0DiscoveryError,
            "no governed knowledge skill matched",
        ):
            route_agent0_knowledge(catalog, "999999")

    def test_hard_max_skills_cannot_be_widened(self):
        registry = create_skill_registry(created_at="t0")
        registry, _ = register(
            registry,
            knowledge_id="knowledge:risk",
            value=5,
            subject="risk.limit",
        )
        catalog = build_agent0_discovery_catalog(registry)

        with self.assertRaisesRegex(
            Agent0DiscoveryError,
            "max_skills must be between 1 and 3",
        ):
            route_agent0_knowledge(
                catalog,
                "risk limit",
                max_skills=4,
            )

    def test_invalid_registry_is_refused_before_catalog_projection(self):
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
            Agent0DiscoveryError,
            "invalid registry",
        ):
            build_agent0_discovery_catalog(bad)

    def test_catalog_is_deterministic_under_registration_order(self):
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
        first = register_promoted_skill(
            first, *alpha, registered_at="t1"
        )
        first = register_promoted_skill(
            first, *beta, registered_at="t2"
        )

        second = create_skill_registry(created_at="t0")
        second = register_promoted_skill(
            second, *beta, registered_at="t1"
        )
        second = register_promoted_skill(
            second, *alpha, registered_at="t2"
        )

        self.assertEqual(
            agent0_catalog_bytes(build_agent0_discovery_catalog(first)),
            agent0_catalog_bytes(build_agent0_discovery_catalog(second)),
        )


if __name__ == "__main__":
    unittest.main()
