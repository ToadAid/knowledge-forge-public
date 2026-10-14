from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.conflicts import claim_sha256
from knowledge_forge.faithfulness import (
    FaithfulnessError,
    candidate_sha256,
    support_evidence_for_exact_text,
    validate_candidate_faithfulness,
)
from knowledge_forge.ingest import ingest_source
from knowledge_forge.normalize import normalize_source

STAMP = datetime(2026, 9, 24, tzinfo=timezone.utc)
KNOWLEDGE_ID = "handbook:governance"
CLAIM_KEY = "governance.approval_quorum"


class Judge:
    judge_id = "test.faithfulness.v1"

    def __init__(self, entailment="SUPPORTED", qualifiers_preserved=True):
        self.entailment = entailment
        self.qualifiers_preserved = qualifiers_preserved
        self.seen = None

    def evaluate(self, *, claim, support_spans):
        self.seen = (claim, support_spans)
        return {
            "entailment": self.entailment,
            "qualifiers_preserved": self.qualifiers_preserved,
        }


def fixture():
    temp = tempfile.TemporaryDirectory()
    path = Path(temp.name) / "governance.md"
    path.write_text(
        "# Approval Quorum\n"
        "Production deployment requires approval from exactly 7 council members.\n\n"
        "# Other\n"
        "Unrelated material.\n",
        encoding="utf-8",
    )
    normalized = normalize_source(path, ingest_source(path, ingested_at=STAMP))
    section = next(s for s in normalized["sections"] if s["heading"] == "Approval Quorum")
    support = support_evidence_for_exact_text(
        normalized,
        section_id=section["section_id"],
        exact_text="requires approval from exactly 7 council members",
    )
    item = {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": KNOWLEDGE_ID,
        "source_ids": [normalized["source_id"]],
        "units": [{
            "unit_id": unit_id_for(KNOWLEDGE_ID, CLAIM_KEY),
            "kind": "DECISION_RULE",
            "title": "Approval Quorum",
            "body": "Production deployment requires 7 council approvals.",
            "claim": {
                "claim_key": CLAIM_KEY,
                "subject": "governance.approval",
                "predicate": "requires_quorum",
                "value": 7,
                "qualifiers": {"environment": "production"},
                "scope": {},
                "validity": {},
                "executable": False,
            },
            "source_bindings": [{
                "source_id": normalized["source_id"],
                "section_ids": [section["section_id"]],
            }],
            "support": [support],
        }],
    }
    return temp, normalized, item


class FaithfulnessTests(unittest.TestCase):
    def test_exact_span_and_semantic_verdict_are_separate(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        judge = Judge()
        report = validate_candidate_faithfulness(item, [normalized], judge)
        self.assertTrue(report["valid"])
        self.assertEqual(report["state"], "VALIDATED")
        self.assertEqual(report["candidate_sha256"], candidate_sha256(item))
        self.assertEqual(
            report["unit_results"][0]["claim_sha256"],
            claim_sha256(item["units"][0]["claim"]),
        )
        self.assertIn("exactly 7 council", judge.seen[1][0]["text"])
        self.assertNotIn("exactly 7 council", json.dumps(report))

    def test_tampered_span_hash_fails(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        item["units"][0]["support"][0]["span_sha256"] = "0" * 64
        report = validate_candidate_faithfulness(item, [normalized], Judge())
        self.assertFalse(report["valid"])
        self.assertEqual(report["state"], "FAILED_VALIDATION")
        self.assertTrue(any("span_sha256 mismatch" in e for e in report["errors"]))

    def test_unsupported_entailment_fails(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        report = validate_candidate_faithfulness(item, [normalized], Judge("UNSUPPORTED"))
        self.assertFalse(report["valid"])
        self.assertEqual(report["state"], "FAILED_VALIDATION")

    def test_qualifier_loss_fails(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        report = validate_candidate_faithfulness(item, [normalized], Judge(qualifiers_preserved=False))
        self.assertFalse(report["valid"])
        self.assertTrue(any("qualifiers were not preserved" in e for e in report["errors"]))

    def test_binding_mismatch_fails(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        other = next(s for s in normalized["sections"] if s["heading"] == "Other")
        item["units"][0]["source_bindings"][0]["section_ids"] = [other["section_id"]]
        report = validate_candidate_faithfulness(item, [normalized], Judge())
        self.assertFalse(report["valid"])

    def test_missing_support_fails(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        item["units"][0].pop("support")
        report = validate_candidate_faithfulness(item, [normalized], Judge())
        self.assertFalse(report["valid"])
        self.assertEqual(report["state"], "FAILED_VALIDATION")

    def test_ambiguous_exact_span_refuses(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "dup.txt"
        path.write_text("same\nsame\n", encoding="utf-8")
        normalized = normalize_source(path, ingest_source(path, ingested_at=STAMP))
        with self.assertRaisesRegex(FaithfulnessError, "exactly once"):
            support_evidence_for_exact_text(
                normalized,
                section_id=normalized["sections"][0]["section_id"],
                exact_text="same",
            )

    def test_candidate_hash_changes_with_semantics(self):
        temp, normalized, item = fixture()
        self.addCleanup(temp.cleanup)
        changed = deepcopy(item)
        changed["units"][0]["claim"]["value"] = 8
        self.assertNotEqual(candidate_sha256(item), candidate_sha256(changed))


if __name__ == "__main__":
    unittest.main()
