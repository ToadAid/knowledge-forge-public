from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.registry import (
    SkillRegistryError,
    create_skill_registry,
    discover_conflict_sets,
    register_promoted_skill,
    resolve_registry_conflict,
    validate_skill_registry,
)
from tests.test_registry import promoted_fixture


def register(registry, fixture, *, at):
    manifest, lifecycle, claim_index, source_receipt = fixture
    return register_promoted_skill(
        registry,
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
        registered_at=at,
    )


class RegistryConflictTests(unittest.TestCase):
    def test_second_promotion_surfaces_exact_contradiction_without_blocking(self):
        registry = create_skill_registry(created_at="t0")
        alpha = promoted_fixture(
            knowledge_id="knowledge:alpha",
            value=5,
        )
        beta = promoted_fixture(
            knowledge_id="knowledge:beta",
            value=7,
        )

        registry = register(registry, alpha, at="t1")
        registry = register(registry, beta, at="t2")

        self.assertEqual(len(registry["skills"]), 2)
        conflicts = discover_conflict_sets(registry)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(
            conflicts[0]["relations"][0]["classification"],
            "CONTRADICTION",
        )
        self.assertEqual(conflicts[0]["state"], "OPEN")

    def test_scope_difference_surfaces_variance(self):
        registry = create_skill_registry(created_at="t0")
        registry = register(
            registry,
            promoted_fixture(
                knowledge_id="knowledge:alpha",
                value=5,
                scope={"chain": "base"},
            ),
            at="t1",
        )
        registry = register(
            registry,
            promoted_fixture(
                knowledge_id="knowledge:beta",
                value=7,
                scope={"chain": "ethereum"},
            ),
            at="t2",
        )

        conflict = registry["conflict_sets"][0]
        self.assertEqual(
            conflict["relations"][0]["classification"],
            "VARIANCE",
        )
        self.assertEqual(
            conflict["relations"][0]["variance_dimensions"],
            ["scope"],
        )

    def test_equal_cross_skill_values_do_not_create_conflict(self):
        registry = create_skill_registry(created_at="t0")
        registry = register(
            registry,
            promoted_fixture(knowledge_id="knowledge:alpha", value=5),
            at="t1",
        )
        registry = register(
            registry,
            promoted_fixture(knowledge_id="knowledge:beta", value=5),
            at="t2",
        )
        self.assertEqual(registry["conflict_sets"], [])

    def test_discovery_can_filter_by_skill(self):
        registry = create_skill_registry(created_at="t0")
        alpha = promoted_fixture(knowledge_id="knowledge:alpha", value=5)
        beta = promoted_fixture(knowledge_id="knowledge:beta", value=7)
        gamma = promoted_fixture(
            knowledge_id="knowledge:gamma",
            value=10,
            subject="protocol.limit",
        )
        registry = register(registry, alpha, at="t1")
        registry = register(registry, beta, at="t2")
        registry = register(registry, gamma, at="t3")

        alpha_id = alpha[0]["skill_id"]
        gamma_id = gamma[0]["skill_id"]
        self.assertEqual(len(discover_conflict_sets(registry, skill_id=alpha_id)), 1)
        self.assertEqual(discover_conflict_sets(registry, skill_id=gamma_id), [])

    def test_resolution_survives_unrelated_promotion_when_identity_is_unchanged(self):
        registry = create_skill_registry(created_at="t0")
        alpha = promoted_fixture(knowledge_id="knowledge:alpha", value=5)
        beta = promoted_fixture(knowledge_id="knowledge:beta", value=7)
        registry = register(registry, alpha, at="t1")
        registry = register(registry, beta, at="t2")

        conflict = registry["conflict_sets"][0]
        selected = conflict["members"][0]["claim_ref"]
        registry = resolve_registry_conflict(
            registry,
            conflict["conflict_set_id"],
            applicable_claim_refs=[selected],
            reason="Operator context selects this supported claim.",
            resolved_at="t3",
            resolver_ref="resolver:test",
        )
        self.assertEqual(registry["conflict_sets"][0]["state"], "RESOLVED")

        unrelated = promoted_fixture(
            knowledge_id="knowledge:gamma",
            value=11,
            subject="protocol.limit",
        )
        registry = register(registry, unrelated, at="t4")

        self.assertEqual(registry["conflict_sets"][0]["state"], "RESOLVED")
        self.assertEqual(
            registry["conflict_sets"][0]["resolution"]["applicable_claim_refs"],
            [selected],
        )

    def test_tampered_durable_conflict_structure_is_refused(self):
        registry = create_skill_registry(created_at="t0")
        registry = register(
            registry,
            promoted_fixture(knowledge_id="knowledge:alpha", value=5),
            at="t1",
        )
        registry = register(
            registry,
            promoted_fixture(knowledge_id="knowledge:beta", value=7),
            at="t2",
        )
        bad = deepcopy(registry)
        bad["conflict_sets"][0]["relations"][0]["classification"] = "VARIANCE"
        bad["conflict_sets"][0]["relations"][0]["variance_dimensions"] = ["scope"]

        report = validate_skill_registry(bad)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "conflict_set" in error
                for error in report["errors"]
            )
        )

    def test_unknown_conflict_resolution_refuses(self):
        registry = create_skill_registry(created_at="t0")
        with self.assertRaisesRegex(SkillRegistryError, "not registered"):
            resolve_registry_conflict(
                registry,
                "conflict-" + ("0" * 64),
                applicable_claim_refs=["x"],
                reason="no",
                resolved_at="t1",
                resolver_ref="resolver:test",
            )


if __name__ == "__main__":
    unittest.main()
