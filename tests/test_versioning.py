from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.lifecycle import (
    REVOKED,
    SUPERSEDED,
    transition_lifecycle,
)
from knowledge_forge.registry import (
    SkillRegistryError,
    create_skill_registry,
    discover_skill,
    fold_in_current_incident_lifecycle,
    fold_in_promoted_version,
    load_skill_registry,
    register_promoted_skill,
    save_skill_registry,
    validate_skill_registry,
)
from tests.test_registry import promoted_fixture


def registry_v1(*, value=5):
    registry = create_skill_registry(created_at="2026-09-23T02:00:00Z")
    manifest, lifecycle, claim_index, source_receipt = promoted_fixture(version=1, value=value)
    registry = register_promoted_skill(
        registry,
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
        registered_at="2026-09-23T02:30:00Z",
    )
    return registry, manifest, lifecycle, claim_index, source_receipt


def supersede(
    lifecycle,
    *,
    replacement_version: int,
    replacement_skill_id: str,
):
    return transition_lifecycle(
        lifecycle,
        SUPERSEDED,
        occurred_at="2026-09-23T02:40:00Z",
        actor="operator:test",
        evidence={
            "replacement_skill_id": replacement_skill_id,
            "replacement_skill_version": replacement_version,
        },
    )


class VersionFoldInTests(unittest.TestCase):
    def test_v2_becomes_current_and_v1_claim_index_remains_historical(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1(value=5)
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(
            version=2,
            value=6,
        )
        superseded_v1 = supersede(
            lifecycle_v1,
            replacement_version=2,
            replacement_skill_id=manifest_v1["skill_id"],
        )

        updated = fold_in_promoted_version(
            registry,
            manifest_v2,
            lifecycle_v2,
            claim_index_v2,
            source_receipt_v2,
            superseded_v1,
            updated_at="2026-09-23T02:41:00Z",
        )

        skill_id = manifest_v1["skill_id"]
        self.assertEqual(registry["skills"][skill_id]["current_version"], 1)
        skill = updated["skills"][skill_id]
        self.assertEqual(skill["current_version"], 2)
        self.assertEqual(skill["versions"]["1"]["state"], "SUPERSEDED")
        self.assertEqual(skill["versions"]["2"]["state"], "PROMOTED")
        self.assertEqual(
            skill["versions"]["1"]["claim_index"]["claims"][0]["claim"]["value"],
            5,
        )
        self.assertEqual(
            skill["versions"]["2"]["claim_index"]["claims"][0]["claim"]["value"],
            6,
        )
        self.assertEqual(discover_skill(updated, skill_id)["skill_version"], 2)
        self.assertEqual(
            discover_skill(updated, skill_id, version=1)["state"],
            "SUPERSEDED",
        )
        self.assertTrue(validate_skill_registry(updated)["valid"])

    def test_replacement_must_be_newer_than_current(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        manifest_same, promoted_same, index_same, source_receipt_same = promoted_fixture(version=1)
        superseded_v1 = supersede(
            lifecycle_v1,
            replacement_version=2,
            replacement_skill_id=manifest_v1["skill_id"],
        )

        with self.assertRaisesRegex(SkillRegistryError, "newer than current_version"):
            fold_in_promoted_version(
                registry,
                manifest_same,
                promoted_same,
                index_same,
                source_receipt_same,
                superseded_v1,
                updated_at="t2",
            )

    def test_supersession_must_name_exact_replacement_version(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        wrong = supersede(
            lifecycle_v1,
            replacement_version=3,
            replacement_skill_id=manifest_v1["skill_id"],
        )

        with self.assertRaisesRegex(SkillRegistryError, "replacement_skill_version"):
            fold_in_promoted_version(
                registry,
                manifest_v2,
                lifecycle_v2,
                claim_index_v2,
                source_receipt_v2,
                wrong,
                updated_at="t2",
            )

    def test_supersession_must_extend_exact_registered_lifecycle(self):
        registry, manifest_v1, _, _, _ = registry_v1()
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        _, parallel_v1, _, _ = promoted_fixture(
            version=1,
            decision_ref="decision:test:parallel-v1",
        )
        parallel_superseded = supersede(
            parallel_v1,
            replacement_version=2,
            replacement_skill_id=manifest_v1["skill_id"],
        )

        with self.assertRaisesRegex(SkillRegistryError, "extend the exact current"):
            fold_in_promoted_version(
                registry,
                manifest_v2,
                lifecycle_v2,
                claim_index_v2,
                source_receipt_v2,
                parallel_superseded,
                updated_at="t2",
            )

    def test_replacement_must_be_independently_promoted(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        validated_only = deepcopy(lifecycle_v2)
        validated_only["state"] = "VALIDATED"
        validated_only["events"] = validated_only["events"][:2]
        superseded_v1 = supersede(
            lifecycle_v1,
            replacement_version=2,
            replacement_skill_id=manifest_v1["skill_id"],
        )

        with self.assertRaisesRegex(SkillRegistryError, "PROMOTED"):
            fold_in_promoted_version(
                registry,
                manifest_v2,
                validated_only,
                claim_index_v2,
                source_receipt_v2,
                superseded_v1,
                updated_at="t2",
            )

    def test_registry_rejects_two_promoted_versions_as_current_truth(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        superseded_v1 = supersede(
            lifecycle_v1,
            replacement_version=2,
            replacement_skill_id=manifest_v1["skill_id"],
        )
        updated = fold_in_promoted_version(
            registry,
            manifest_v2,
            lifecycle_v2,
            claim_index_v2,
            source_receipt_v2,
            superseded_v1,
            updated_at="t2",
        )
        tampered = deepcopy(updated)
        old = tampered["skills"][manifest_v1["skill_id"]]["versions"]["1"]
        old["state"] = "PROMOTED"

        report = validate_skill_registry(tampered)
        self.assertFalse(report["valid"])
        self.assertIn(
            (
                "registry historical entry state must be one of REVOKED, SUPERSEDED: "
                f"{manifest_v1['skill_id']}@1"
            ),
            report["errors"],
        )

    def test_version_handoff_recomputes_current_cross_skill_conflicts(self):
        registry = create_skill_registry(created_at="t0")
        alpha_v1 = promoted_fixture(
            version=1,
            knowledge_id="knowledge:alpha",
            value=5,
        )
        beta = promoted_fixture(
            version=1,
            knowledge_id="knowledge:beta",
            value=7,
        )
        registry = register_promoted_skill(
            registry,
            *alpha_v1,
            registered_at="t1",
        )
        registry = register_promoted_skill(
            registry,
            *beta,
            registered_at="t2",
        )
        self.assertEqual(len(registry["conflict_sets"]), 1)

        alpha_manifest_v1, alpha_lifecycle_v1, _, _ = alpha_v1
        alpha_v2 = promoted_fixture(
            version=2,
            knowledge_id="knowledge:alpha",
            value=7,
        )
        superseded_alpha = supersede(
            alpha_lifecycle_v1,
            replacement_version=2,
            replacement_skill_id=alpha_manifest_v1["skill_id"],
        )
        registry = fold_in_promoted_version(
            registry,
            alpha_v2[0],
            alpha_v2[1],
            alpha_v2[2],
            alpha_v2[3],
            superseded_alpha,
            updated_at="t3",
        )

        self.assertEqual(registry["conflict_sets"], [])
        historical = registry["skills"][alpha_manifest_v1["skill_id"]]["versions"]["1"]
        self.assertEqual(
            historical["claim_index"]["claims"][0]["claim"]["value"],
            5,
        )

    def test_revoked_current_can_receive_independently_promoted_replacement(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        revoked_v1 = transition_lifecycle(
            lifecycle_v1,
            REVOKED,
            occurred_at="t2",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:revoke-v1",
                "reason": "v1 is invalid",
                "decision_ref": "decision:revoke-v1",
            },
        )
        registry = fold_in_current_incident_lifecycle(
            registry,
            revoked_v1,
            updated_at="t2",
        )

        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = (
            promoted_fixture(version=2)
        )
        updated = fold_in_promoted_version(
            registry,
            manifest_v2,
            lifecycle_v2,
            claim_index_v2,
            source_receipt_v2,
            None,
            updated_at="t3",
        )

        skill_id = manifest_v1["skill_id"]
        self.assertEqual(updated["skills"][skill_id]["current_version"], 2)
        self.assertEqual(
            updated["skills"][skill_id]["versions"]["1"]["state"],
            REVOKED,
        )
        self.assertEqual(
            updated["skills"][skill_id]["versions"]["2"]["state"],
            "PROMOTED",
        )
        self.assertEqual(discover_skill(updated, skill_id)["skill_version"], 2)

    def test_revoked_current_refuses_fake_supersession_event_during_replacement(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        revoked_v1 = transition_lifecycle(
            lifecycle_v1,
            REVOKED,
            occurred_at="t2",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:revoke-v1",
                "reason": "v1 is invalid",
                "decision_ref": "decision:revoke-v1",
            },
        )
        registry = fold_in_current_incident_lifecycle(
            registry,
            revoked_v1,
            updated_at="t2",
        )
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = (
            promoted_fixture(version=2)
        )

        with self.assertRaisesRegex(
            SkillRegistryError,
            "revoked current version is terminal",
        ):
            fold_in_promoted_version(
                registry,
                manifest_v2,
                lifecycle_v2,
                claim_index_v2,
                source_receipt_v2,
                revoked_v1,
                updated_at="t3",
            )

    def test_folded_registry_round_trips_durably(self):
        registry, manifest_v1, lifecycle_v1, _, _ = registry_v1()
        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        updated = fold_in_promoted_version(
            registry,
            manifest_v2,
            lifecycle_v2,
            claim_index_v2,
            source_receipt_v2,
            supersede(
                lifecycle_v1,
                replacement_version=2,
                replacement_skill_id=manifest_v1["skill_id"],
            ),
            updated_at="t2",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            save_skill_registry(updated, path)
            loaded = load_skill_registry(path)

        self.assertEqual(loaded, updated)
        self.assertEqual(
            discover_skill(loaded, manifest_v1["skill_id"])["skill_version"],
            2,
        )


if __name__ == "__main__":
    unittest.main()
