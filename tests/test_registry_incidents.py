from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.lifecycle import (
    DISPUTED,
    PROMOTED,
    REVOKED,
    SUSPENDED,
    transition_lifecycle,
)
from knowledge_forge.registry import (
    SkillRegistryError,
    create_skill_registry,
    discover_conflict_sets,
    discover_skill,
    fold_in_current_incident_lifecycle,
    register_promoted_skill,
    validate_skill_registry,
)
from tests.test_registry import promoted_fixture


def registered_fixture(*, knowledge_id="knowledge:alpha", value=5):
    registry = create_skill_registry(created_at="t0")
    manifest, lifecycle, claim_index, source_receipt = promoted_fixture(
        knowledge_id=knowledge_id,
        value=value,
    )
    registry = register_promoted_skill(
        registry,
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
        registered_at="t1",
    )
    return registry, manifest, lifecycle


def incident(lifecycle, target, *, incident_ref="incident:test", at="t2"):
    if target in {SUSPENDED, DISPUTED}:
        evidence = {
            "incident_ref": incident_ref,
            "reason": f"{target.lower()} for test",
        }
    elif target == REVOKED:
        evidence = {
            "incident_ref": incident_ref,
            "reason": "revoked for test",
            "decision_ref": "decision:revoke-test",
        }
    else:
        raise AssertionError(target)
    return transition_lifecycle(
        lifecycle,
        target,
        occurred_at=at,
        actor="operator:test",
        evidence=evidence,
    )


class RegistryIncidentTests(unittest.TestCase):
    def test_current_skill_can_suspend_without_successor_and_default_discovery_refuses(self):
        registry, manifest, promoted = registered_fixture()
        suspended = incident(promoted, SUSPENDED)

        stopped = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="t2",
        )

        skill_id = manifest["skill_id"]
        self.assertEqual(
            stopped["skills"][skill_id]["versions"]["1"]["state"],
            SUSPENDED,
        )
        with self.assertRaisesRegex(SkillRegistryError, "current skill is not usable"):
            discover_skill(stopped, skill_id)
        audited = discover_skill(stopped, skill_id, version=1)
        self.assertEqual(audited["state"], SUSPENDED)
        self.assertTrue(validate_skill_registry(stopped)["valid"])

    def test_dispute_and_revoke_are_no_successor_stops(self):
        for target in (DISPUTED, REVOKED):
            with self.subTest(target=target):
                registry, manifest, promoted = registered_fixture()
                stopped = fold_in_current_incident_lifecycle(
                    registry,
                    incident(promoted, target),
                    updated_at="t2",
                )
                entry = stopped["skills"][manifest["skill_id"]]["versions"]["1"]
                self.assertEqual(entry["state"], target)
                with self.assertRaisesRegex(
                    SkillRegistryError,
                    "current skill is not usable",
                ):
                    discover_skill(stopped, manifest["skill_id"])

    def test_incident_fold_requires_exact_one_event_extension(self):
        registry, _, promoted = registered_fixture()
        suspended = incident(promoted, SUSPENDED)
        disputed = transition_lifecycle(
            suspended,
            DISPUTED,
            occurred_at="t3",
            actor="reviewer:test",
            evidence={
                "incident_ref": "incident:test",
                "reason": "escalated",
            },
        )

        with self.assertRaisesRegex(
            SkillRegistryError,
            "extend the exact current registry lifecycle by one event",
        ):
            fold_in_current_incident_lifecycle(
                registry,
                disputed,
                updated_at="t3",
            )

    def test_stopped_skill_is_removed_from_active_conflict_truth(self):
        registry = create_skill_registry(created_at="t0")
        alpha = promoted_fixture(knowledge_id="knowledge:alpha", value=5)
        beta = promoted_fixture(knowledge_id="knowledge:beta", value=7)

        registry = register_promoted_skill(
            registry, *alpha, registered_at="t1"
        )
        registry = register_promoted_skill(
            registry, *beta, registered_at="t2"
        )
        self.assertEqual(len(discover_conflict_sets(registry)), 1)

        alpha_stopped = incident(alpha[1], SUSPENDED, incident_ref="incident:alpha")
        registry = fold_in_current_incident_lifecycle(
            registry,
            alpha_stopped,
            updated_at="t3",
        )

        self.assertEqual(discover_conflict_sets(registry), [])

    def test_restoration_reactivates_current_claim_and_conflict_truth(self):
        registry = create_skill_registry(created_at="t0")
        alpha = promoted_fixture(knowledge_id="knowledge:alpha", value=5)
        beta = promoted_fixture(knowledge_id="knowledge:beta", value=7)

        registry = register_promoted_skill(
            registry, *alpha, registered_at="t1"
        )
        registry = register_promoted_skill(
            registry, *beta, registered_at="t2"
        )

        suspended = incident(
            alpha[1],
            SUSPENDED,
            incident_ref="incident:restore",
            at="t3",
        )
        registry = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="t3",
        )
        self.assertEqual(registry["conflict_sets"], [])

        restored = transition_lifecycle(
            suspended,
            PROMOTED,
            occurred_at="t4",
            actor="operator:test",
            evidence={
                "decision_ref": "decision:restore",
                "incident_ref": "incident:restore",
                "resolution_ref": "resolution:evidence",
            },
        )
        registry = fold_in_current_incident_lifecycle(
            registry,
            restored,
            updated_at="t4",
        )

        self.assertEqual(
            discover_skill(registry, alpha[0]["skill_id"])["state"],
            PROMOTED,
        )
        self.assertEqual(len(registry["conflict_sets"]), 1)

    def test_rehashed_or_parallel_incident_history_is_refused(self):
        registry, _, promoted = registered_fixture()
        suspended = incident(promoted, SUSPENDED)
        forged = deepcopy(suspended)
        forged["events"][-1]["previous_event_sha256"] = "sha256:" + ("0" * 64)

        with self.assertRaisesRegex(
            SkillRegistryError,
            "incident lifecycle is invalid",
        ):
            fold_in_current_incident_lifecycle(
                registry,
                forged,
                updated_at="t2",
            )


if __name__ == "__main__":
    unittest.main()
