from __future__ import annotations

import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.knowledge import KnowledgeCandidateError, canonicalize_compiler_result
from knowledge_forge.provenance import build_compiler_profile


SOURCE = "sha256:" + ("a" * 64)


def profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


def compiler_result(units):
    return {
        "contract_version": "0.2",
        "adapter_id": "test.compiler.v1",
        "knowledge_id": "topic:bounded-context",
        "compiler_profile": profile(),
        "request_source_ids": [SOURCE],
        "authority": "KNOWLEDGE_ONLY",
        "candidate": {"title": "Useful Knowledge", "units": units},
    }


def unit(**overrides):
    value = {
        "kind": "DECISION_RULE",
        "title": "Prefer bounded context",
        "body": "Load only the knowledge needed for the question.",
        "claim": {
            "claim_key": "retrieval.context.bounded",
            "subject": "retrieval.context",
            "predicate": "prefers",
            "value": "bounded",
            "qualifiers": {},
            "scope": {},
            "validity": {},
            "executable": False,
        },
        "source_bindings": [{"source_id": SOURCE, "section_ids": ["s001"]}],
    }
    value.update(overrides)
    return value


class KnowledgeCandidateTests(unittest.TestCase):
    def test_canonical_candidate_preserves_source_binding_authority_and_compiler_provenance(self):
        expected_profile = profile()
        result = canonicalize_compiler_result(
            compiler_result([unit()]),
            skill_slug="bounded-context",
        )
        self.assertEqual(result["schema_version"], "0.3")
        self.assertEqual(result["state"], "COMPILED_CANDIDATE")
        self.assertEqual(result["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(result["knowledge_id"], "topic:bounded-context")
        self.assertEqual(
            result["compiler"]["compiler_profile_id"],
            expected_profile["compiler_profile_id"],
        )
        self.assertEqual(result["compiler"]["model_identity"], "test/model-v1")
        self.assertEqual(result["units"][0]["unit_id"], unit_id_for("topic:bounded-context", "retrieval.context.bounded"))
        self.assertFalse(result["units"][0]["claim"]["executable"])
        self.assertEqual(result["units"][0]["source_bindings"][0]["section_ids"], ["s001"])

    def test_exact_support_metadata_survives_canonicalization(self):
        support = [{
            "source_id": SOURCE,
            "section_id": "s001",
            "section_sha256": "e" * 64,
            "coordinate_space": "normalized_section_utf8_bytes",
            "start_byte": 0,
            "end_byte": 7,
            "span_sha256": "f" * 64,
        }]
        result = canonicalize_compiler_result(
            compiler_result([unit(support=support)]),
            skill_slug="x",
        )
        self.assertEqual(result["units"][0]["support"], support)

    def test_support_outside_source_binding_is_refused(self):
        support = [{
            "source_id": SOURCE,
            "section_id": "s999",
            "section_sha256": "e" * 64,
            "coordinate_space": "normalized_section_utf8_bytes",
            "start_byte": 0,
            "end_byte": 7,
            "span_sha256": "f" * 64,
        }]
        with self.assertRaisesRegex(KnowledgeCandidateError, "outside source_bindings"):
            canonicalize_compiler_result(
                compiler_result([unit(support=support)]),
                skill_slug="x",
            )

    def test_unknown_unit_kind_is_refused(self):
        with self.assertRaisesRegex(KnowledgeCandidateError, "unsupported"):
            canonicalize_compiler_result(
                compiler_result([unit(kind="TEMPORAL_CLAIM")]),
                skill_slug="x",
            )

    def test_unbound_source_is_refused(self):
        bad = "sha256:" + ("b" * 64)
        with self.assertRaisesRegex(KnowledgeCandidateError, "unbound"):
            canonicalize_compiler_result(
                compiler_result([
                    unit(source_bindings=[{"source_id": bad, "section_ids": ["s001"]}])
                ]),
                skill_slug="x",
            )

    def test_conflict_must_reference_emitted_unit(self):
        with self.assertRaisesRegex(KnowledgeCandidateError, "another emitted claim"):
            canonicalize_compiler_result(
                compiler_result([unit(conflicts_with_claim_keys=["missing.claim"])]),
                skill_slug="x",
            )

    def test_two_units_can_preserve_explicit_conflict(self):
        first = unit(conflicts_with_claim_keys=["retrieval.context.unbounded"])
        second = unit(
            title="Alternative",
            claim={**unit()["claim"], "claim_key":"retrieval.context.unbounded", "value":"unbounded"},
            conflicts_with_claim_keys=["retrieval.context.bounded"],
        )
        result = canonicalize_compiler_result(
            compiler_result([first, second]),
            skill_slug="x",
        )
        self.assertEqual(result["units"][0]["conflicts_with"], [unit_id_for("topic:bounded-context", "retrieval.context.unbounded")])

    def test_prose_and_value_changes_keep_same_claim_identity(self):
        first = canonicalize_compiler_result(
            compiler_result([unit()]),
            skill_slug="x",
        )
        changed = unit(
            title="Use a bounded knowledge window",
            body="Retrieve only a small relevant knowledge window.",
            claim={**unit()["claim"], "value": "smallest-sufficient"},
        )
        second = canonicalize_compiler_result(
            compiler_result([changed]),
            skill_slug="x",
        )
        self.assertEqual(first["units"][0]["unit_id"], second["units"][0]["unit_id"])
        self.assertNotEqual(
            first["units"][0]["claim"]["value"],
            second["units"][0]["claim"]["value"],
        )

    def test_model_supplied_wrong_unit_id_is_refused(self):
        with self.assertRaisesRegex(KnowledgeCandidateError, "deterministic"):
            canonicalize_compiler_result(
                compiler_result([unit(unit_id="model-invented")]),
                skill_slug="x",
            )

    def test_claim_must_be_non_executable(self):
        bad = unit(claim={**unit()["claim"], "executable": True})
        with self.assertRaisesRegex(KnowledgeCandidateError, "executable"):
            canonicalize_compiler_result(compiler_result([bad]), skill_slug="x")

    def test_tampered_profile_is_refused_at_candidate_boundary(self):
        result = compiler_result([unit()])
        result["compiler_profile"]["parameters"]["temperature"] = 0.5
        with self.assertRaisesRegex(KnowledgeCandidateError, "profile_id"):
            canonicalize_compiler_result(result, skill_slug="x")


if __name__ == "__main__":
    unittest.main()
