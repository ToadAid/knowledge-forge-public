from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.claim_index import (
    ClaimIndexError,
    build_claim_index,
    claim_index_artifact_sha256,
    validate_claim_index,
)
from knowledge_forge.claims import unit_id_for
from knowledge_forge.faithfulness import candidate_sha256
from knowledge_forge.knowledge import canonicalize_compiler_result
from knowledge_forge.provenance import build_compiler_profile, skill_id_for


SOURCE = "sha256:" + ("a" * 64)


def profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


def candidate():
    return canonicalize_compiler_result(
        {
            "contract_version": "0.2",
            "adapter_id": "test.compiler.v1",
            "knowledge_id": "topic:claim-index",
            "compiler_profile": profile(),
            "request_source_ids": [SOURCE],
            "authority": "KNOWLEDGE_ONLY",
            "candidate": {
                "title": "Claim Index",
                "units": [
                    {
                        "kind": "DEFINITION",
                        "title": "Fee",
                        "body": "The fee is five.",
                        "claim": {
                            "claim_key": "fee.value",
                            "subject": "protocol.fee",
                            "predicate": "equals",
                            "value": 5,
                            "qualifiers": {},
                            "scope": {"chain": "base"},
                            "validity": {},
                            "executable": False,
                        },
                        "source_bindings": [{
                            "source_id": SOURCE,
                            "section_ids": ["s001"],
                        }],
                    }
                ],
            },
        },
        skill_slug="claim-index",
    )


def manifest_for(value):
    c = value
    skill_id = skill_id_for(
        c["knowledge_id"],
        c["compiler"]["compiler_profile_id"],
    )
    return {
        "skill_id": skill_id,
        "skill_version": 1,
        "knowledge_id": c["knowledge_id"],
        "candidate_sha256": candidate_sha256(c),
        "source_ids": c["source_ids"],
        "compiler": c["compiler"],
        "units": [{
            "unit_id": c["units"][0]["unit_id"],
            "claim_key": c["units"][0]["claim"]["claim_key"],
            "kind": c["units"][0]["kind"],
        }],
    }


class ClaimIndexTests(unittest.TestCase):
    def test_build_and_validate_claim_index(self):
        c = candidate()
        skill_id = skill_id_for(
            c["knowledge_id"],
            c["compiler"]["compiler_profile_id"],
        )
        index = build_claim_index(c, skill_id=skill_id, skill_version=1)

        self.assertEqual(
            validate_claim_index(index, manifest_for(c)),
            {"valid": True, "errors": []},
        )
        self.assertEqual(index["claims"][0]["claim"]["value"], 5)
        self.assertEqual(index["claims"][0]["claim"]["scope"], {"chain": "base"})
        self.assertTrue(claim_index_artifact_sha256(index).startswith("sha256:"))

    def test_index_is_deterministic(self):
        c = candidate()
        skill_id = skill_id_for(
            c["knowledge_id"],
            c["compiler"]["compiler_profile_id"],
        )
        one = build_claim_index(c, skill_id=skill_id, skill_version=1)
        two = build_claim_index(deepcopy(c), skill_id=skill_id, skill_version=1)
        self.assertEqual(one, two)

    def test_tampered_claim_value_refuses_even_if_claim_text_is_present(self):
        c = candidate()
        skill_id = skill_id_for(
            c["knowledge_id"],
            c["compiler"]["compiler_profile_id"],
        )
        index = build_claim_index(c, skill_id=skill_id, skill_version=1)
        index["claims"][0]["claim"]["value"] = 7

        with self.assertRaisesRegex(ClaimIndexError, "claim_sha256 mismatch"):
            validate_claim_index(index, manifest_for(c))

    def test_manifest_candidate_binding_cannot_lie(self):
        c = candidate()
        skill_id = skill_id_for(
            c["knowledge_id"],
            c["compiler"]["compiler_profile_id"],
        )
        index = build_claim_index(c, skill_id=skill_id, skill_version=1)
        manifest = manifest_for(c)
        manifest["candidate_sha256"] = "sha256:" + ("0" * 64)

        with self.assertRaisesRegex(
            ClaimIndexError,
            "candidate_sha256 does not match manifest",
        ):
            validate_claim_index(index, manifest)

    def test_manifest_unit_coverage_must_match_exactly(self):
        c = candidate()
        skill_id = skill_id_for(
            c["knowledge_id"],
            c["compiler"]["compiler_profile_id"],
        )
        index = build_claim_index(c, skill_id=skill_id, skill_version=1)
        manifest = manifest_for(c)
        manifest["units"].append({
            "unit_id": unit_id_for(c["knowledge_id"], "other.claim"),
            "claim_key": "other.claim",
            "kind": "DEFINITION",
        })

        with self.assertRaisesRegex(ClaimIndexError, "unit coverage"):
            validate_claim_index(index, manifest)


if __name__ == "__main__":
    unittest.main()
