from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.faithfulness import support_evidence_for_exact_text
from knowledge_forge.ingest import ingest_source
from knowledge_forge.normalize import normalize_source
from knowledge_forge.provenance import build_compiler_profile
from knowledge_forge.stability import (
    carry_forward_unit,
    plan_recompile,
    semantic_unit_sha256,
)

STAMP = datetime(2026, 9, 24, tzinfo=timezone.utc)
KNOWLEDGE_ID = "handbook:governance"
CLAIM_KEY = "governance.approval_quorum"


def profile(profile_suffix="c"):
    return build_compiler_profile(
        compiler_digest="sha256:" + (profile_suffix * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


def normalized(path: Path, text: str):
    path.write_text(text, encoding="utf-8")
    return normalize_source(path, ingest_source(path, ingested_at=STAMP))


def candidate(source):
    section = next(s for s in source["sections"] if s["heading"] == "Stable")
    support = support_evidence_for_exact_text(
        source,
        section_id=section["section_id"],
        exact_text="approval from exactly 7 council members",
    )
    return {
        "schema_version": "0.3",
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": KNOWLEDGE_ID,
        "compiler": {"compiler_profile_id": profile()["compiler_profile_id"]},
        "source_ids": [source["source_id"]],
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
                "source_id": source["source_id"],
                "section_ids": [section["section_id"]],
            }],
            "support": [support],
        }],
    }


class StabilityTests(unittest.TestCase):
    def test_identical_source_carries_forward_exact_unit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.md"
            source = normalized(path, "# Stable\nProduction requires approval from exactly 7 council members.\n")
            previous = candidate(source)
            plan = plan_recompile(
                previous,
                [source],
                [source],
                source_replacements={source["source_id"]: source["source_id"]},
                current_compiler_profile_id=profile()["compiler_profile_id"],
            )
            decision = plan["decisions"][0]
            self.assertEqual(decision["action"], "CARRY_FORWARD")
            carried = carry_forward_unit(previous["units"][0], decision)
            self.assertEqual(carried, previous["units"][0])

    def test_inserted_section_rebinds_provenance_without_semantic_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = normalized(root / "old.md", "# Stable\nProduction requires approval from exactly 7 council members.\n")
            new = normalized(
                root / "new.md",
                "# New\nNew material.\n\n# Stable\nProduction requires approval from exactly 7 council members.\n",
            )
            previous = candidate(old)
            plan = plan_recompile(
                previous,
                [old],
                [new],
                source_replacements={old["source_id"]: new["source_id"]},
                current_compiler_profile_id=profile()["compiler_profile_id"],
            )
            decision = plan["decisions"][0]
            self.assertEqual(decision["action"], "CARRY_FORWARD")
            before = semantic_unit_sha256(previous["units"][0])
            carried = carry_forward_unit(previous["units"][0], decision)
            self.assertEqual(semantic_unit_sha256(carried), before)
            self.assertNotEqual(
                carried["source_bindings"][0]["source_id"],
                previous["units"][0]["source_bindings"][0]["source_id"],
            )
            self.assertNotEqual(
                carried["source_bindings"][0]["section_ids"],
                previous["units"][0]["source_bindings"][0]["section_ids"],
            )
            self.assertEqual(
                carried["support"][0]["span_sha256"],
                previous["units"][0]["support"][0]["span_sha256"],
            )
            self.assertTrue(plan["new_sections"])

    def test_changed_bound_section_requires_recompile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = normalized(root / "old.md", "# Stable\nProduction requires approval from exactly 7 council members.\n")
            new = normalized(root / "new.md", "# Stable\nProduction requires approval from exactly 8 council members.\n")
            previous = candidate(old)
            plan = plan_recompile(
                previous,
                [old],
                [new],
                source_replacements={old["source_id"]: new["source_id"]},
                current_compiler_profile_id=profile()["compiler_profile_id"],
            )
            self.assertEqual(plan["decisions"][0]["action"], "RECOMPILE")
            self.assertEqual(plan["decisions"][0]["reason"], "SOURCE_SECTION_CHANGED")

    def test_compiler_profile_change_requires_recompile_even_when_source_is_same(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "one.md"
            source = normalized(path, "# Stable\nProduction requires approval from exactly 7 council members.\n")
            previous = candidate(source)
            changed_profile = profile("e")["compiler_profile_id"]
            plan = plan_recompile(
                previous,
                [source],
                [source],
                source_replacements={source["source_id"]: source["source_id"]},
                current_compiler_profile_id=changed_profile,
            )
            self.assertTrue(plan["compiler_profile_changed"])
            self.assertEqual(plan["decisions"][0]["reason"], "COMPILER_CHANGED")

    def test_duplicate_hash_match_refuses_carry_forward_as_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = normalized(root / "old.md", "# Stable\nProduction requires approval from exactly 7 council members.\n")
            new = normalized(
                root / "new.md",
                "# A\nProduction requires approval from exactly 7 council members.\n\n"
                "# B\nProduction requires approval from exactly 7 council members.\n",
            )
            previous = candidate(old)
            plan = plan_recompile(
                previous,
                [old],
                [new],
                source_replacements={old["source_id"]: new["source_id"]},
                current_compiler_profile_id=profile()["compiler_profile_id"],
            )
            self.assertEqual(plan["decisions"][0]["action"], "RECOMPILE")
            self.assertEqual(plan["decisions"][0]["reason"], "AMBIGUOUS_SECTION_MATCH")


if __name__ == "__main__":
    unittest.main()
