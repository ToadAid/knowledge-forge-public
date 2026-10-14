from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.lifecycle import SUSPENDED, transition_lifecycle
from knowledge_forge.registry import (
    create_skill_registry,
    fold_in_current_incident_lifecycle,
    register_promoted_skill,
)
from knowledge_forge.skill_index import (
    SkillIndexError,
    build_skill_index,
    list_skill_index,
    load_skill_index,
    route_skill_index,
    save_skill_index,
    skill_index_bytes,
    validate_skill_index,
    verify_skill_index_current,
)
from tests.test_registry import promoted_fixture


class SkillIndexTests(unittest.TestCase):
    def _register(
        self,
        registry,
        *,
        knowledge_id,
        subject,
        value,
        at,
    ):
        fixture = promoted_fixture(
            knowledge_id=knowledge_id,
            subject=subject,
            value=value,
        )
        updated = register_promoted_skill(
            registry,
            *fixture,
            registered_at=at,
        )
        return updated, fixture

    def _registry(self):
        registry = create_skill_registry(created_at="t0")
        registry, fees = self._register(
            registry,
            knowledge_id="knowledge:fees",
            subject="protocol.fee",
            value="SECRET-FEE-VALUE",
            at="t1",
        )
        registry, risk = self._register(
            registry,
            knowledge_id="knowledge:risk",
            subject="risk.limit",
            value=12,
            at="t2",
        )
        return registry, fees, risk

    def test_build_is_deterministic_for_exact_registry(self):
        registry, _fees, _risk = self._registry()

        one = build_skill_index(registry)
        two = build_skill_index(deepcopy(registry))

        self.assertEqual(one, two)
        self.assertEqual(skill_index_bytes(one), skill_index_bytes(two))
        self.assertEqual(
            [item["skill_id"] for item in one["skills"]],
            sorted(item["skill_id"] for item in one["skills"]),
        )

    def test_index_contains_routing_metadata_not_values_or_skill_bodies(self):
        registry, _fees, _risk = self._registry()

        index = build_skill_index(registry)
        encoded = json.dumps(index, sort_keys=True)

        self.assertNotIn("SECRET-FEE-VALUE", encoded)
        self.assertNotIn("source_receipt", encoded)
        self.assertNotIn("lifecycle_record", encoded)
        self.assertEqual(index["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(index["execution_authority"], "NONE")
        self.assertEqual(index["tool_grants"], [])
        self.assertTrue(
            index["selection_requires_current_registry_revalidation"]
        )

    def test_fast_route_selects_candidate_without_registry_input(self):
        registry, _fees, risk = self._registry()
        index = build_skill_index(registry)

        routed = route_skill_index(
            index,
            "What is the risk limit?",
            consumer_id="test-agent",
        )

        self.assertEqual(
            routed["selected_skill_ids"],
            [risk[0]["skill_id"]],
        )
        self.assertTrue(
            routed["selection_requires_current_registry_revalidation"]
        )
        self.assertEqual(routed["execution_authority"], "NONE")
        self.assertEqual(routed["tool_grants"], [])

    def test_fast_route_hard_cap_cannot_be_widened(self):
        registry, _fees, _risk = self._registry()
        index = build_skill_index(registry)

        with self.assertRaisesRegex(SkillIndexError, "between 1 and 3"):
            route_skill_index(
                index,
                "knowledge",
                consumer_id="test-agent",
                max_skills=4,
            )

    def test_stale_index_is_detected_and_rebuild_removes_suspended_skill(self):
        registry, _fees, risk = self._registry()
        old_index = build_skill_index(registry)

        suspended = transition_lifecycle(
            risk[1],
            SUSPENDED,
            occurred_at="t3",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:p7c-index",
                "reason": "test suspension",
            },
        )
        stopped = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="t3",
        )

        report = verify_skill_index_current(old_index, stopped)
        rebuilt = build_skill_index(stopped)

        self.assertFalse(report["current"])
        self.assertTrue(report["requires_rebuild"])
        self.assertNotIn(
            risk[0]["skill_id"],
            [item["skill_id"] for item in rebuilt["skills"]],
        )
        self.assertTrue(
            verify_skill_index_current(rebuilt, stopped)["current"]
        )

    def test_skills_digest_detects_accidental_index_tamper(self):
        registry, _fees, _risk = self._registry()
        bad = deepcopy(build_skill_index(registry))
        bad["skills"][0]["route_terms"].append("tampered")
        bad["skills"][0]["route_terms"].sort()

        report = validate_skill_index(bad)

        self.assertFalse(report["valid"])
        self.assertIn("skill index skills_sha256 mismatch", report["errors"])

    def test_forged_tool_grant_is_refused(self):
        registry, _fees, _risk = self._registry()
        bad = deepcopy(build_skill_index(registry))
        bad["skills"][0]["tool_grants"] = ["shell"]

        report = validate_skill_index(bad)

        self.assertFalse(report["valid"])
        self.assertTrue(
            any("must not grant tools" in error for error in report["errors"])
        )

    def test_librarian_list_is_lightweight_sorted_and_has_unit_counts(self):
        registry, _fees, _risk = self._registry()

        listing = list_skill_index(build_skill_index(registry))

        self.assertEqual(
            [item["skill_id"] for item in listing],
            sorted(item["skill_id"] for item in listing),
        )
        self.assertTrue(all(item["unit_count"] >= 1 for item in listing))
        encoded = json.dumps(listing, sort_keys=True)
        self.assertNotIn("route_terms", encoded)
        self.assertNotIn("unit_routes", encoded)
        self.assertNotIn("SECRET-FEE-VALUE", encoded)

    def test_save_and_load_round_trip_is_atomic_replaceable_cache(self):
        registry, _fees, _risk = self._registry()
        index = build_skill_index(registry)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cache" / "skill-index.v1.json"
            saved = save_skill_index(index, path)
            loaded = load_skill_index(path)

            self.assertEqual(saved, path)
            self.assertEqual(loaded, index)

            save_skill_index(index, path)
            self.assertEqual(load_skill_index(path), index)

    def test_save_refuses_symlink_ancestor(self):
        registry, _fees, _risk = self._registry()
        index = build_skill_index(registry)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            os.symlink(real, linked)

            with self.assertRaisesRegex(SkillIndexError, "symlink"):
                save_skill_index(
                    index,
                    linked / "skill-index.v1.json",
                )

            self.assertFalse((real / "skill-index.v1.json").exists())


if __name__ == "__main__":
    unittest.main()
