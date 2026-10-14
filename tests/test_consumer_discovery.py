from __future__ import annotations

import unittest

from knowledge_forge.consumer_discovery import (
    ConsumerBinding,
    ConsumerDiscoveryError,
    build_consumer_discovery_catalog,
    consumer_catalog_bytes,
    route_consumer_knowledge,
    validate_consumer_discovery_catalog,
)
from knowledge_forge.registry import create_skill_registry, register_promoted_skill
from tests.test_registry import promoted_fixture


class GeneralConsumerDiscoveryTests(unittest.TestCase):
    def _registry(self):
        fixture = promoted_fixture(
            knowledge_id="knowledge:portable",
            value="NEVER-LEAK-THIS-VALUE",
            subject="portable.topic",
        )
        registry = register_promoted_skill(
            create_skill_registry(created_at="t0"),
            *fixture,
            registered_at="t1",
        )
        return registry, fixture

    def test_third_party_consumer_uses_same_core_without_authority_grant(self):
        registry, _ = self._registry()
        binding = ConsumerBinding(
            consumer_id="example-agent",
            runtime_repo="ExampleOrg/example-agent",
            runtime_entrypoint="runtime/main.py",
            integration_boundary="READ_ONLY_DISCOVERY",
            hard_max_skills_per_query=2,
        )

        catalog = build_consumer_discovery_catalog(registry, binding)

        self.assertTrue(
            validate_consumer_discovery_catalog(catalog, binding)["valid"]
        )
        self.assertEqual(catalog["consumer_id"], "example-agent")
        self.assertEqual(catalog["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(catalog["execution_authority"], "NONE")
        self.assertEqual(catalog["tool_grants"], [])

    def test_generic_catalog_never_contains_claim_value(self):
        registry, _ = self._registry()
        binding = ConsumerBinding(
            consumer_id="example-agent",
            runtime_repo="ExampleOrg/example-agent",
            runtime_entrypoint="runtime/main.py",
            integration_boundary="READ_ONLY_DISCOVERY",
        )
        payload = consumer_catalog_bytes(
            build_consumer_discovery_catalog(registry, binding),
            binding,
        ).decode("utf-8")

        self.assertNotIn("NEVER-LEAK-THIS-VALUE", payload)
        self.assertNotIn("source_receipt", payload)
        self.assertNotIn("lifecycle_record", payload)

    def test_global_hard_cap_cannot_be_widened_by_consumer_binding(self):
        binding = ConsumerBinding(
            consumer_id="bad-agent",
            runtime_repo="ExampleOrg/bad-agent",
            runtime_entrypoint="main.py",
            integration_boundary="READ_ONLY",
            hard_max_skills_per_query=4,
        )
        with self.assertRaisesRegex(
            ConsumerDiscoveryError,
            "consumer hard max must be between 1 and 3",
        ):
            build_consumer_discovery_catalog(
                create_skill_registry(created_at="t0"),
                binding,
            )

    def test_general_router_returns_skill_identity_without_body(self):
        registry, fixture = self._registry()
        binding = ConsumerBinding(
            consumer_id="example-agent",
            runtime_repo="ExampleOrg/example-agent",
            runtime_entrypoint="runtime/main.py",
            integration_boundary="READ_ONLY_DISCOVERY",
        )
        catalog = build_consumer_discovery_catalog(registry, binding)
        routed = route_consumer_knowledge(
            catalog,
            binding,
            "portable topic",
        )

        self.assertEqual(
            routed["selected_skill_ids"],
            [fixture[0]["skill_id"]],
        )
        self.assertNotIn("content", routed["selected_skills"][0])
        self.assertEqual(routed["tool_grants"], [])


if __name__ == "__main__":
    unittest.main()
