from __future__ import annotations

import unittest

from knowledge_forge.provenance import (
    ProvenanceError,
    build_compiler_profile,
    skill_id_for,
)


class ProvenanceTests(unittest.TestCase):
    def test_profile_id_is_deterministic_and_parameter_order_independent(self):
        one = build_compiler_profile(
            compiler_digest="sha256:" + ("a" * 64),
            compiler_version="1.2.3",
            model_identity="example/model-v1",
            prompt_template_hash="sha256:" + ("b" * 64),
            parameters={"temperature": 0, "top_p": 1},
        )
        two = build_compiler_profile(
            compiler_digest="sha256:" + ("a" * 64),
            compiler_version="1.2.3",
            model_identity="example/model-v1",
            prompt_template_hash="sha256:" + ("b" * 64),
            parameters={"top_p": 1, "temperature": 0},
        )
        self.assertEqual(one, two)

    def test_profile_change_changes_profile_id(self):
        base = dict(
            compiler_digest="sha256:" + ("a" * 64),
            compiler_version="1.2.3",
            model_identity="example/model-v1",
            prompt_template_hash="sha256:" + ("b" * 64),
        )
        one = build_compiler_profile(parameters={"temperature": 0}, **base)
        two = build_compiler_profile(parameters={"temperature": 0.2}, **base)
        self.assertNotEqual(one["compiler_profile_id"], two["compiler_profile_id"])

    def test_invalid_digest_is_refused(self):
        with self.assertRaisesRegex(ProvenanceError, "compiler_digest"):
            build_compiler_profile(
                compiler_digest="not-a-digest",
                compiler_version="1.2.3",
                model_identity="example/model-v1",
                prompt_template_hash="sha256:" + ("b" * 64),
                parameters={},
            )

    def test_skill_lineage_binds_knowledge_and_compiler_profile(self):
        profile = build_compiler_profile(
            compiler_digest="sha256:" + ("a" * 64),
            compiler_version="1.2.3",
            model_identity="example/model-v1",
            prompt_template_hash="sha256:" + ("b" * 64),
            parameters={},
        )
        skill_id = skill_id_for("topic:bounded-context", profile["compiler_profile_id"])
        self.assertIn("topic:bounded-context", skill_id)
        self.assertIn(profile["compiler_profile_id"], skill_id)


if __name__ == "__main__":
    unittest.main()
