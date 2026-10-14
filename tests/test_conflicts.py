from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.conflicts import (
    CONTRADICTION,
    OPEN,
    RESOLVED,
    VARIANCE,
    ConflictSetError,
    record_conflict_resolution,
    scan_conflict_sets,
    validate_conflict_set,
)


SOURCE_A = "sha256:" + ("1" * 64)
SOURCE_B = "sha256:" + ("2" * 64)
PROFILE_A = "sha256:" + ("a" * 64)
PROFILE_B = "sha256:" + ("b" * 64)


def unit(
    knowledge_id,
    claim_key,
    *,
    value,
    subject="protocol.fee",
    predicate="equals",
    qualifiers=None,
    scope=None,
    validity=None,
    source_id=SOURCE_A,
):
    return {
        "unit_id": unit_id_for(knowledge_id, claim_key),
        "kind": "DEFINITION",
        "title": claim_key,
        "body": f"{claim_key}: {value}",
        "claim": {
            "claim_key": claim_key,
            "subject": subject,
            "predicate": predicate,
            "value": value,
            "qualifiers": qualifiers or {},
            "scope": scope or {},
            "validity": validity or {},
            "executable": False,
        },
        "source_bindings": [{
            "source_id": source_id,
            "section_ids": ["s001"],
        }],
    }


def candidate(knowledge_id, profile_id, units, source_id=SOURCE_A):
    return {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": knowledge_id,
        "skill": {
            "slug": knowledge_id.replace(":", "-"),
            "title": knowledge_id,
            "purpose": "reference",
        },
        "compiler": {
            "contract_version": "0.2",
            "adapter_id": "test.compiler",
            "compiler_profile_id": profile_id,
            "compiler_digest": "sha256:" + ("c" * 64),
            "compiler_version": "1",
            "model_identity": "test/model",
            "prompt_template_hash": "sha256:" + ("d" * 64),
            "parameters": {},
        },
        "source_ids": [source_id],
        "units": units,
    }


class ConflictSetTests(unittest.TestCase):
    def test_same_applicability_different_values_is_contradiction(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )

        sets = scan_conflict_sets([one, two])

        self.assertEqual(len(sets), 1)
        conflict = sets[0]
        self.assertEqual(conflict["state"], OPEN)
        self.assertEqual(conflict["relations"][0]["classification"], CONTRADICTION)
        self.assertEqual(conflict["relations"][0]["variance_dimensions"], [])
        self.assertIsNone(conflict["resolution"])

    def test_scope_difference_is_variance_not_contradiction(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5, scope={"chain": "base"})],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "fee.value",
                value=7,
                scope={"chain": "ethereum"},
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        conflict = scan_conflict_sets([one, two])[0]
        relation = conflict["relations"][0]

        self.assertEqual(relation["classification"], VARIANCE)
        self.assertEqual(relation["variance_dimensions"], ["scope"])

    def test_qualifier_difference_is_variance(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit(
                "skill:one",
                "fee.value",
                value=5,
                qualifiers={"pool_type": "stable"},
            )],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "fee.value",
                value=7,
                qualifiers={"pool_type": "volatile"},
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        relation = scan_conflict_sets([one, two])[0]["relations"][0]
        self.assertEqual(relation["classification"], VARIANCE)
        self.assertEqual(relation["variance_dimensions"], ["qualifiers"])

    def test_validity_difference_is_variance_without_temporal_guessing(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit(
                "skill:one",
                "fee.value",
                value=5,
                validity={"valid_from": "2026-01-01"},
            )],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "fee.value",
                value=7,
                validity={"valid_from": "2026-06-01"},
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        relation = scan_conflict_sets([one, two])[0]["relations"][0]
        self.assertEqual(relation["classification"], VARIANCE)
        self.assertEqual(relation["variance_dimensions"], ["validity"])

    def test_multiple_applicability_differences_are_preserved(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit(
                "skill:one",
                "fee.value",
                value=5,
                scope={"chain": "base"},
                qualifiers={"pool": "a"},
                validity={"as_of_source": "epoch-1"},
            )],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "fee.value",
                value=7,
                scope={"chain": "ethereum"},
                qualifiers={"pool": "b"},
                validity={"as_of_source": "epoch-2"},
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        relation = scan_conflict_sets([one, two])[0]["relations"][0]
        self.assertEqual(
            relation["variance_dimensions"],
            ["scope", "qualifiers", "validity"],
        )

    def test_equal_values_do_not_create_conflictset(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=5, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )

        self.assertEqual(scan_conflict_sets([one, two]), [])

    def test_unrelated_subject_predicate_do_not_create_conflictset(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "limit.value",
                value=7,
                subject="protocol.limit",
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        self.assertEqual(scan_conflict_sets([one, two]), [])

    def test_same_skill_units_are_not_inferred_as_cross_skill_conflict(self):
        first = unit("skill:one", "fee.one", value=5)
        second = unit("skill:one", "fee.two", value=7)
        same_skill = candidate(
            "skill:one",
            PROFILE_A,
            [first, second],
        )
        unrelated = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "limit.value",
                value=10,
                subject="protocol.limit",
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )

        self.assertEqual(scan_conflict_sets([same_skill, unrelated]), [])

    def test_non_disagreeing_group_member_is_pruned_from_conflictset(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [
                unit("skill:one", "fee.same", value=5),
                unit("skill:one", "fee.diff", value=7),
            ],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=5, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )

        conflict = scan_conflict_sets([one, two])[0]
        refs = {member["claim_key"] for member in conflict["members"]}

        self.assertEqual(refs, {"fee.diff", "fee.value"})

    def test_scan_is_deterministic_under_candidate_order(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )

        self.assertEqual(
            scan_conflict_sets([one, two]),
            scan_conflict_sets([two, one]),
        )

    def test_resolution_preserves_every_member_and_relation(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]
        original_members = deepcopy(conflict["members"])
        original_relations = deepcopy(conflict["relations"])
        selected = conflict["members"][0]["claim_ref"]

        resolved = record_conflict_resolution(
            conflict,
            applicable_claim_refs=[selected],
            reason="Runtime context matches this claim's applicability.",
            resolved_at="2026-09-24T03:20:00Z",
            resolver_ref="resolver:test",
        )

        self.assertEqual(resolved["state"], RESOLVED)
        self.assertEqual(resolved["members"], original_members)
        self.assertEqual(resolved["relations"], original_relations)
        self.assertEqual(resolved["resolution"]["applicable_claim_refs"], [selected])
        self.assertEqual(validate_conflict_set(resolved), {"valid": True, "errors": []})

    def test_resolution_may_preserve_multiple_applicable_variance_claims(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit(
                "skill:one",
                "fee.value",
                value=5,
                scope={"chain": "base"},
            )],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit(
                "skill:two",
                "fee.value",
                value=7,
                scope={"chain": "ethereum"},
                source_id=SOURCE_B,
            )],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]
        refs = [member["claim_ref"] for member in conflict["members"]]

        resolved = record_conflict_resolution(
            conflict,
            applicable_claim_refs=refs,
            reason="Both scope-specific claims remain applicable.",
            resolved_at="2026-09-24T03:20:00Z",
            resolver_ref="resolver:test",
        )

        self.assertEqual(
            resolved["resolution"]["applicable_claim_refs"],
            sorted(refs),
        )

    def test_resolution_cannot_select_both_sides_of_exact_contradiction(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]
        refs = [member["claim_ref"] for member in conflict["members"]]

        with self.assertRaisesRegex(
            ConflictSetError,
            "both sides of an exact contradiction",
        ):
            record_conflict_resolution(
                conflict,
                applicable_claim_refs=refs,
                reason="This must not count as resolved.",
                resolved_at="2026-09-24T03:20:00Z",
                resolver_ref="resolver:test",
            )

    def test_unknown_resolution_member_refuses(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]

        with self.assertRaisesRegex(ConflictSetError, "unknown conflict member"):
            record_conflict_resolution(
                conflict,
                applicable_claim_refs=["unknown#claim"],
                reason="bad",
                resolved_at="2026-09-24T03:20:00Z",
                resolver_ref="resolver:test",
            )

    def test_tampered_conflict_id_refuses(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]
        conflict["conflict_set_id"] = "conflict-" + ("0" * 64)

        with self.assertRaisesRegex(ConflictSetError, "conflict_set_id mismatch"):
            validate_conflict_set(conflict)

    def test_tampered_relation_classification_refuses(self):
        one = candidate(
            "skill:one",
            PROFILE_A,
            [unit("skill:one", "fee.value", value=5)],
        )
        two = candidate(
            "skill:two",
            PROFILE_B,
            [unit("skill:two", "fee.value", value=7, source_id=SOURCE_B)],
            source_id=SOURCE_B,
        )
        conflict = scan_conflict_sets([one, two])[0]
        conflict["relations"][0]["classification"] = VARIANCE
        conflict["relations"][0]["variance_dimensions"] = ["scope"]

        with self.assertRaisesRegex(ConflictSetError, "classification mismatch"):
            validate_conflict_set(conflict)


if __name__ == "__main__":
    unittest.main()
