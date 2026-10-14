from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.diffing import (
    KnowledgeDiffError,
    claim_semantic_sha256,
    diff_candidates,
    validate_knowledge_diff,
)


KNOWLEDGE_ID = "topic:diff-proof"
PROFILE_A = "sha256:" + ("a" * 64)
PROFILE_B = "sha256:" + ("b" * 64)
SOURCE_A = "sha256:" + ("1" * 64)
SOURCE_B = "sha256:" + ("2" * 64)


def unit(
    claim_key,
    *,
    value,
    title=None,
    body=None,
    qualifiers=None,
    kind="DEFINITION",
    source_id=SOURCE_A,
    section_id="s001",
):
    return {
        "unit_id": unit_id_for(KNOWLEDGE_ID, claim_key),
        "kind": kind,
        "title": title or claim_key,
        "body": body or f"body for {claim_key}",
        "claim": {
            "claim_key": claim_key,
            "subject": "topic.subject",
            "predicate": "states",
            "value": value,
            "qualifiers": qualifiers or {},
            "scope": {},
            "validity": {},
            "executable": False,
        },
        "source_bindings": [{
            "source_id": source_id,
            "section_ids": [section_id],
        }],
        "support": [{
            "source_id": source_id,
            "section_id": section_id,
            "section_sha256": "3" * 64,
            "coordinate_space": "normalized_section_utf8_bytes",
            "start_byte": 0,
            "end_byte": 1,
            "span_sha256": "4" * 64,
        }],
    }


def candidate(units, *, profile=PROFILE_A, source_ids=None):
    return {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": KNOWLEDGE_ID,
        "skill": {
            "slug": "diff-proof",
            "title": "Diff Proof",
            "purpose": "reference",
        },
        "compiler": {
            "contract_version": "0.2",
            "adapter_id": "test.compiler",
            "compiler_profile_id": profile,
            "compiler_digest": "sha256:" + ("c" * 64),
            "compiler_version": "1",
            "model_identity": "test/model",
            "prompt_template_hash": "sha256:" + ("d" * 64),
            "parameters": {"temperature": 0},
        },
        "source_ids": source_ids or [SOURCE_A],
        "units": units,
    }


class KnowledgeDiffTests(unittest.TestCase):
    def test_rewording_and_provenance_rebind_do_not_create_semantic_change(self):
        old = candidate([
            unit(
                "claim.alpha",
                value={"x": 1},
                title="Old title",
                body="Old wording.",
            )
        ])
        new_unit = unit(
            "claim.alpha",
            value={"x": 1},
            title="New title",
            body="Totally different prose.",
            source_id=SOURCE_B,
            section_id="s009",
        )
        new = candidate([new_unit], source_ids=[SOURCE_B])

        report = diff_candidates(old, new)

        self.assertEqual(report["summary"]["UNCHANGED"], 1)
        self.assertEqual(report["summary"]["CHANGED"], 0)
        self.assertTrue(report["source_ids_changed"])
        self.assertEqual(
            report["changes"][0]["semantic_sha256"],
            claim_semantic_sha256(old["units"][0]),
        )

    def test_structured_value_or_qualifier_change_is_changed(self):
        old = candidate([unit("claim.alpha", value=1)])
        new = candidate([
            unit(
                "claim.alpha",
                value=2,
                qualifiers={"environment": "production"},
            )
        ])

        report = diff_candidates(old, new)
        change = report["changes"][0]

        self.assertEqual(change["kind"], "CHANGED")
        self.assertIn("claim.value", change["changed_fields"])
        self.assertIn("claim.qualifiers", change["changed_fields"])

    def test_added_and_removed_are_preserved_without_guessing_lineage(self):
        old = candidate([unit("claim.old", value="old")])
        new = candidate([
            unit("claim.new.a", value="a"),
            unit("claim.new.b", value="b"),
        ])

        report = diff_candidates(old, new)

        self.assertEqual(report["summary"]["REMOVED"], 1)
        self.assertEqual(report["summary"]["ADDED"], 2)
        self.assertEqual(report["summary"]["SPLIT"], 0)

    def test_split_requires_explicit_lineage(self):
        old = candidate([unit("claim.old", value="old")])
        new = candidate([
            unit("claim.new.a", value="a"),
            unit("claim.new.b", value="b"),
        ])

        report = diff_candidates(
            old,
            new,
            lineage=[{
                "kind": "SPLIT",
                "from_claim_keys": ["claim.old"],
                "to_claim_keys": ["claim.new.a", "claim.new.b"],
                "reason": "source now separates two independent rules",
            }],
        )

        self.assertEqual(report["summary"]["SPLIT"], 1)
        self.assertEqual(report["summary"]["REMOVED"], 0)
        self.assertEqual(report["summary"]["ADDED"], 0)
        split = report["changes"][0]
        self.assertEqual(split["kind"], "SPLIT")
        self.assertEqual(split["from_claim_keys"], ["claim.old"])

    def test_merge_requires_explicit_lineage(self):
        old = candidate([
            unit("claim.old.a", value="a"),
            unit("claim.old.b", value="b"),
        ])
        new = candidate([unit("claim.new", value="combined")])

        report = diff_candidates(
            old,
            new,
            lineage=[{
                "kind": "MERGED",
                "from_claim_keys": ["claim.old.a", "claim.old.b"],
                "to_claim_keys": ["claim.new"],
                "reason": "new source states one combined rule",
            }],
        )

        self.assertEqual(report["summary"]["MERGED"], 1)
        self.assertEqual(report["summary"]["REMOVED"], 0)
        self.assertEqual(report["summary"]["ADDED"], 0)

    def test_invalid_lineage_cannot_relabel_unchanged_identity(self):
        old = candidate([
            unit("claim.same", value=1),
            unit("claim.old", value=2),
        ])
        new = candidate([
            unit("claim.same", value=1),
            unit("claim.new.a", value=2),
            unit("claim.new.b", value=3),
        ])

        with self.assertRaisesRegex(KnowledgeDiffError, "REMOVED identities"):
            diff_candidates(
                old,
                new,
                lineage=[{
                    "kind": "SPLIT",
                    "from_claim_keys": ["claim.same"],
                    "to_claim_keys": ["claim.new.a", "claim.new.b"],
                    "reason": "invalid",
                }],
            )

    def test_claim_cannot_participate_in_two_lineage_relations(self):
        old = candidate([
            unit("claim.old.a", value="a"),
            unit("claim.old.b", value="b"),
        ])
        new = candidate([
            unit("claim.new.a", value="a"),
            unit("claim.new.b", value="b"),
            unit("claim.new.c", value="c"),
        ])

        with self.assertRaisesRegex(KnowledgeDiffError, "only one lineage"):
            diff_candidates(
                old,
                new,
                lineage=[
                    {
                        "kind": "SPLIT",
                        "from_claim_keys": ["claim.old.a"],
                        "to_claim_keys": ["claim.new.a", "claim.new.b"],
                        "reason": "first",
                    },
                    {
                        "kind": "MERGED",
                        "from_claim_keys": ["claim.old.a", "claim.old.b"],
                        "to_claim_keys": ["claim.new.c"],
                        "reason": "second",
                    },
                ],
            )

    def test_compiler_change_is_metadata_not_automatic_knowledge_change(self):
        old = candidate([unit("claim.alpha", value=1)], profile=PROFILE_A)
        new = candidate([unit("claim.alpha", value=1)], profile=PROFILE_B)

        report = diff_candidates(old, new)

        self.assertTrue(report["compiler_profile_changed"])
        self.assertEqual(report["summary"]["UNCHANGED"], 1)
        self.assertEqual(report["summary"]["CHANGED"], 0)

    def test_cross_knowledge_identity_diff_refuses(self):
        old = candidate([unit("claim.alpha", value=1)])
        new = deepcopy(old)
        new["knowledge_id"] = "topic:other"

        with self.assertRaisesRegex(KnowledgeDiffError, "knowledge_id mismatch"):
            diff_candidates(old, new)

    def test_generated_diff_passes_independent_artifact_validation(self):
        old = candidate([unit("claim.alpha", value=1)])
        new = candidate([
            unit("claim.alpha", value=2),
            unit("claim.beta", value=3),
        ])
        report = diff_candidates(old, new)
        self.assertEqual(
            validate_knowledge_diff(report),
            {"valid": True, "errors": []},
        )

    def test_artifact_validator_refuses_extra_change_fields(self):
        report = diff_candidates(
            candidate([unit("claim.alpha", value=1)]),
            candidate([unit("claim.alpha", value=1)]),
        )
        report["changes"][0]["surprise"] = True
        with self.assertRaisesRegex(KnowledgeDiffError, "exact contract"):
            validate_knowledge_diff(report)

    def test_artifact_validator_refuses_fake_summary(self):
        report = diff_candidates(
            candidate([unit("claim.alpha", value=1)]),
            candidate([unit("claim.alpha", value=2)]),
        )
        report["summary"]["CHANGED"] = 0
        with self.assertRaisesRegex(KnowledgeDiffError, "summary counts"):
            validate_knowledge_diff(report)

    def test_artifact_validator_refuses_malformed_semantic_hash(self):
        report = diff_candidates(
            candidate([unit("claim.alpha", value=1)]),
            candidate([unit("claim.alpha", value=2)]),
        )
        report["changes"][0]["new_semantic_sha256"] = "not-a-hash"
        with self.assertRaisesRegex(KnowledgeDiffError, "sha256 reference"):
            validate_knowledge_diff(report)

    def test_artifact_validator_refuses_duplicate_identity_across_changes(self):
        report = diff_candidates(
            candidate([unit("claim.alpha", value=1)]),
            candidate([
                unit("claim.alpha", value=1),
                unit("claim.beta", value=2),
            ]),
        )
        duplicate = deepcopy(report["changes"][0])
        duplicate["kind"] = "REMOVED"
        duplicate.pop("semantic_sha256")
        duplicate["old_semantic_sha256"] = "sha256:" + ("f" * 64)
        report["changes"].append(duplicate)
        report["summary"]["REMOVED"] = 1
        with self.assertRaisesRegex(KnowledgeDiffError, "more than one diff change"):
            validate_knowledge_diff(report)

    def test_output_is_deterministic_under_unit_ordering(self):
        old = candidate([
            unit("claim.b", value=1),
            unit("claim.a", value=1),
        ])
        new_one = candidate([
            unit("claim.b", value=2),
            unit("claim.a", value=1),
            unit("claim.c", value=3),
        ])
        new_two = candidate(list(reversed(new_one["units"])))

        first = diff_candidates(old, new_one)
        second = diff_candidates(old, new_two)

        self.assertEqual(first["changes"], second["changes"])
        self.assertEqual(first["summary"], second["summary"])


if __name__ == "__main__":
    unittest.main()
