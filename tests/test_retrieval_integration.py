from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.consumer import SkillConsumerError, answer_from_candidate_skill
from knowledge_forge.emit import build_skill_package, emit_skill_package
from knowledge_forge.provenance import build_compiler_profile
from knowledge_forge.validate import validate_skill_package


KNOWLEDGE_ID = "handbook:governance"


def _profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


def _candidate(source_id: str):
    def unit(claim_key, kind, title, body, section):
        return {
            "unit_id": unit_id_for(KNOWLEDGE_ID, claim_key),
            "kind": kind,
            "title": title,
            "body": body,
            "claim": {
                "claim_key": claim_key,
                "subject": claim_key.rsplit(".", 1)[0],
                "predicate": "states",
                "value": body,
                "qualifiers": {},
                "scope": {},
                "validity": {},
                "executable": False,
            },
            "source_bindings": [{"source_id": source_id, "section_ids": [section]}],
        }

    return {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": KNOWLEDGE_ID,
        "skill": {
            "slug": "governance-handbook",
            "title": "Governance Handbook",
            "purpose": "reference",
        },
        "compiler": {
            "contract_version": "0.2",
            "adapter_id": "fixture",
            **_profile(),
        },
        "source_ids": [source_id],
        "units": [
            unit(
                "governance.approval_quorum",
                "DECISION_RULE",
                "Approval Quorum",
                "The approval quorum is exactly 7 members.",
                "s001",
            ),
            unit(
                "governance.deployment_window",
                "PROCEDURE",
                "Deployment Window",
                "Deployments happen on Tuesdays.",
                "s002",
            ),
            unit(
                "tea.grams_per_cup",
                "EXAMPLE",
                "Tea Recipe",
                "Use 3 grams of tea per cup.",
                "s003",
            ),
        ],
    }


class RecordingAnswerer:
    answerer_id = "test.retrieval-integration.v1"

    def answer(self, *, question, context):
        if context and "exactly 7 members" in context[0]["content"]:
            return "7 members"
        return "unknown"


class RetrievalIntegrationTests(unittest.TestCase):
    def _emit(self, base: Path):
        source = base / "source.md"
        source.write_text(
            "# Approval Quorum\nThe approval quorum is exactly 7 members.\n\n"
            "# Deployment Window\nDeployments happen on Tuesdays.\n\n"
            "# Tea Recipe\nUse 3 grams of tea per cup.\n",
            encoding="utf-8",
        )
        source_id = f"sha256:{sha256(source.read_bytes()).hexdigest()}"
        root = base / "skill"
        emit_skill_package(
            _candidate(source_id),
            root,
            compiled_at="2026-09-23T00:00:00Z",
        )
        return root

    def test_package_emits_hash_bound_router_without_body_text(self):
        source_id = "sha256:" + ("a" * 64)
        files = build_skill_package(
            _candidate(source_id),
            compiled_at="2026-09-23T00:00:00Z",
        )
        manifest = json.loads(files["manifest.json"])
        receipt = json.loads(files["SOURCE_RECEIPT.json"])
        retrieval = files["retrieval-index.json"].decode("utf-8")
        self.assertEqual(manifest["retrieval_index"]["path"], "retrieval-index.json")
        self.assertEqual(manifest["retrieval_index"]["max_units_per_turn"], 3)
        self.assertIn("retrieval-index.json", receipt["artifact_hashes"])
        self.assertNotIn("exactly 7 members", retrieval)
        self.assertNotIn("Deployments happen", retrieval)

    def test_consumer_routes_from_claim_index_and_receipts_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._emit(Path(tmp))
            result = answer_from_candidate_skill(
                root,
                "What approval quorum is required?",
                RecordingAnswerer(),
            )
            self.assertEqual(result["answer"], "7 members")
            self.assertEqual(result["receipt"]["retrieval_contract_version"], "0.1")
            self.assertEqual(result["receipt"]["max_units"], 1)
            loaded = result["receipt"]["loaded_units"]
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["claim_key"], "governance.approval_quorum")
            self.assertEqual(loaded[0]["source_bindings"][0]["section_ids"], ["s001"])

    def test_body_only_phrase_refuses_instead_of_using_unit_content_for_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._emit(Path(tmp))
            with self.assertRaisesRegex(SkillConsumerError, "no relevant knowledge unit"):
                answer_from_candidate_skill(
                    root,
                    "What is exactly 7 members?",
                    RecordingAnswerer(),
                )

    def test_consumer_hard_budget_cannot_be_widened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._emit(Path(tmp))
            with self.assertRaisesRegex(SkillConsumerError, "between 1 and 3"):
                answer_from_candidate_skill(
                    root,
                    "governance",
                    RecordingAnswerer(),
                    max_units=4,
                )

    def test_router_hash_tamper_refuses_before_answering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._emit(Path(tmp))
            path = root / "retrieval-index.json"
            index = json.loads(path.read_text())
            index["units"][0]["terms"].append("tampered")
            path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
            with self.assertRaisesRegex(SkillConsumerError, "artifact hash mismatch"):
                answer_from_candidate_skill(
                    root,
                    "What approval quorum is required?",
                    RecordingAnswerer(),
                )

    def test_rehashed_router_semantic_tamper_fails_package_validation(self):
        source_id = "sha256:" + ("a" * 64)
        files = build_skill_package(
            _candidate(source_id),
            compiled_at="2026-09-23T00:00:00Z",
        )
        index = json.loads(files["retrieval-index.json"])
        index["units"][0]["claim_key"] = "tampered.claim"
        files["retrieval-index.json"] = (
            json.dumps(index, indent=2, sort_keys=True) + "\n"
        ).encode()
        receipt = json.loads(files["SOURCE_RECEIPT.json"])
        receipt["artifact_hashes"]["retrieval-index.json"] = (
            "sha256:" + sha256(files["retrieval-index.json"]).hexdigest()
        )
        files["SOURCE_RECEIPT.json"] = (
            json.dumps(receipt, indent=2, sort_keys=True) + "\n"
        ).encode()
        report = validate_skill_package(files)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "retrieval index claim_key does not match manifest" in error
                for error in report["errors"]
            )
        )


if __name__ == "__main__":
    unittest.main()
