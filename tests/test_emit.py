from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.emit import SkillPackageError, build_skill_package, emit_skill_package
from knowledge_forge.knowledge import canonicalize_compiler_result
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


def candidate():
    return canonicalize_compiler_result(
        {
            "contract_version": "0.2",
            "adapter_id": "test.compiler.v1",
            "knowledge_id": "topic:bounded-context",
            "compiler_profile": profile(),
            "request_source_ids": [SOURCE],
            "authority": "KNOWLEDGE_ONLY",
            "candidate": {
                "title": "Bounded Context",
                "units": [
                    {
                        "kind": "DECISION_RULE",
                        "title": "Load selectively",
                        "body": "Load the smallest useful knowledge unit.",
                        "claim": {"claim_key":"retrieval.context.bounded","subject":"retrieval.context","predicate":"prefers","value":"bounded","qualifiers":{},"scope":{},"validity":{},"executable":False},
                        "source_bindings": [{"source_id": SOURCE, "section_ids": ["s001"]}],
                    }
                ],
            },
        },
        skill_slug="bounded-context",
    )


class SkillPackageTests(unittest.TestCase):
    def test_build_emits_core_artifacts_and_on_demand_unit(self):
        files = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        self.assertEqual(
            set(files),
            {"SKILL.md", "manifest.json", "SOURCE_RECEIPT.json", "retrieval-index.json", "claim-index.json", f"units/{unit_id_for("topic:bounded-context", "retrieval.context.bounded")}.md"},
        )
        self.assertIn(b"Authority: KNOWLEDGE_ONLY", files["SKILL.md"])

    def test_manifest_and_receipt_preserve_knowledge_and_compiler_identity(self):
        files = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        manifest = json.loads(files["manifest.json"])
        receipt = json.loads(files["SOURCE_RECEIPT.json"])
        self.assertEqual(manifest["knowledge_id"], "topic:bounded-context")
        self.assertEqual(receipt["knowledge_id"], manifest["knowledge_id"])
        self.assertEqual(receipt["compiler"], manifest["compiler"])
        self.assertEqual(
            manifest["compiler"]["compiler_profile_id"],
            profile()["compiler_profile_id"],
        )
        self.assertIn("topic:bounded-context@", manifest["skill_id"])
        self.assertEqual(receipt["candidate_sha256"], manifest["candidate_sha256"])
        self.assertEqual(manifest["schema_version"], "0.6")
        self.assertEqual(receipt["schema_version"], "0.6")
        self.assertEqual(manifest["units"][0]["support"], [])

    def test_manifest_binds_structured_claim_index(self):
        files = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        manifest = json.loads(files["manifest.json"])
        claim_index = json.loads(files["claim-index.json"])
        import hashlib

        self.assertEqual(manifest["claim_index"]["path"], "claim-index.json")
        self.assertEqual(manifest["claim_index"]["schema_version"], "0.1")
        self.assertEqual(manifest["claim_index"]["claim_count"], 1)
        self.assertEqual(
            manifest["claim_index"]["sha256"],
            "sha256:" + hashlib.sha256(files["claim-index.json"]).hexdigest(),
        )
        self.assertEqual(
            claim_index["claims"][0]["claim"]["claim_key"],
            "retrieval.context.bounded",
        )
        self.assertEqual(
            claim_index["claims"][0]["claim"]["value"],
            "bounded",
        )

    def test_receipt_hashes_emitted_non_receipt_artifacts(self):
        files = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        receipt = json.loads(files["SOURCE_RECEIPT.json"])
        self.assertEqual(
            set(receipt["artifact_hashes"]),
            {"SKILL.md", "manifest.json", "retrieval-index.json", "claim-index.json", f"units/{unit_id_for("topic:bounded-context", "retrieval.context.bounded")}.md"},
        )

    def test_build_is_deterministic_for_same_candidate_and_time(self):
        one = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        two = build_skill_package(candidate(), compiled_at="2026-09-23T00:00:00Z")
        self.assertEqual(one, two)

    def test_emit_refuses_nonempty_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "skill"
            root.mkdir()
            (root / "existing.txt").write_text("do not overwrite", encoding="utf-8")
            with self.assertRaisesRegex(SkillPackageError, "refusing silent rewrite"):
                emit_skill_package(candidate(), root, compiled_at="2026-09-23T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
