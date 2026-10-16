from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.consumer import SkillConsumerError, answer_from_candidate_skill
from knowledge_forge.emit import emit_skill_package
from knowledge_forge.provenance import build_compiler_profile


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
            "unit_id": unit_id_for("handbook:governance", claim_key),
            "kind": kind,
            "title": title,
            "body": body,
            "claim": {"claim_key":claim_key,"subject":claim_key.rsplit(".",1)[0],"predicate":"states","value":body,"qualifiers":{},"scope":{},"validity":{},"executable":False},
            "source_bindings": [{"source_id": source_id, "section_ids": [section]}],
        }

    return {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": "handbook:governance",
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
    answerer_id = "test.recording.v1"

    def __init__(self):
        self.seen = None

    def answer(self, *, question, context):
        self.seen = context
        if "exactly 7 members" in context[0]["content"]:
            return "7 members"
        return "unknown"


class ConsumerProofTests(unittest.TestCase):
    def _emit(self, base: Path):
        original = base / "original-source.md"
        original.write_text(
            "# Approval Quorum\nThe approval quorum is exactly 7 members.\n\n"
            "# Deployment Window\nDeployments happen on Tuesdays.\n\n"
            "# Tea Recipe\nUse 3 grams of tea per cup.\n"
        )
        source_id = f"sha256:{sha256(original.read_bytes()).hexdigest()}"
        skill_root = base / "skill"
        emit_skill_package(
            _candidate(source_id),
            skill_root,
            compiled_at="2026-09-23T00:00:00Z",
        )
        return original, skill_root

    def test_targeted_question_loads_one_unit_and_survives_source_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            original, skill_root = self._emit(Path(tmp))
            original.unlink()

            answerer = RecordingAnswerer()
            result = answer_from_candidate_skill(
                skill_root,
                "What approval quorum is required?",
                answerer,
            )

            self.assertEqual(result["answer"], "7 members")
            self.assertEqual(result["receipt"]["knowledge_id"], "handbook:governance")
            self.assertEqual(
                result["receipt"]["compiler_profile_id"],
                _profile()["compiler_profile_id"],
            )
            self.assertEqual(result["receipt"]["loaded_unit_ids"], [unit_id_for("handbook:governance", "governance.approval_quorum")])
            self.assertEqual(result["receipt"]["loaded_unit_count"], 1)
            self.assertEqual(result["receipt"]["unloaded_unit_count"], 2)
            self.assertEqual(len(answerer.seen), 1)
            self.assertNotIn("Deployments happen", answerer.seen[0]["content"])
            self.assertFalse(original.exists())
            total = sum(path.stat().st_size for path in skill_root.rglob("*") if path.is_file())
            self.assertLess(result["receipt"]["loaded_bytes"], total)

    def test_no_index_match_refuses_instead_of_loading_every_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, skill_root = self._emit(Path(tmp))
            with self.assertRaisesRegex(SkillConsumerError, "no relevant knowledge unit"):
                answer_from_candidate_skill(
                    skill_root,
                    "How many satellites orbit Mars?",
                    RecordingAnswerer(),
                )

    def test_selected_unit_hash_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, skill_root = self._emit(Path(tmp))
            (skill_root / f"units/{unit_id_for('handbook:governance', 'governance.approval_quorum')}.md").write_text("tampered\n")
            with self.assertRaisesRegex(SkillConsumerError, "artifact hash mismatch"):
                answer_from_candidate_skill(
                    skill_root,
                    "What approval quorum is required?",
                    RecordingAnswerer(),
                )

    def test_index_manifest_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, skill_root = self._emit(Path(tmp))
            skill_path = skill_root / "SKILL.md"
            skill = skill_path.read_text()
            skill = skill.replace(f"(`{unit_id_for('handbook:governance', 'governance.approval_quorum')}`)", "(`wrong-unit`)")
            # Write exactly the bytes hashed below (Windows text mode would add CR).
            skill_path.write_text(skill, newline="\n")

            receipt_path = skill_root / "SOURCE_RECEIPT.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["artifact_hashes"]["SKILL.md"] = f"sha256:{sha256(skill.encode()).hexdigest()}"
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

            with self.assertRaisesRegex(SkillConsumerError, "index does not match manifest"):
                answer_from_candidate_skill(
                    skill_root,
                    "What approval quorum is required?",
                    RecordingAnswerer(),
                )

    def test_receipt_compiler_provenance_mismatch_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, skill_root = self._emit(Path(tmp))
            receipt_path = skill_root / "SOURCE_RECEIPT.json"
            receipt = json.loads(receipt_path.read_text())
            receipt["compiler"]["model_identity"] = "tampered/model"
            receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")

            with self.assertRaisesRegex(SkillConsumerError, "compiler provenance"):
                answer_from_candidate_skill(
                    skill_root,
                    "What approval quorum is required?",
                    RecordingAnswerer(),
                )


if __name__ == "__main__":
    unittest.main()
