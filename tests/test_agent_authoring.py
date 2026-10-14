from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.agent_authoring import (
    AgentAuthoringError,
    finalize_agent_draft,
    prepare_agent_draft,
)
from knowledge_forge.lifecycle import validate_lifecycle_record
from knowledge_forge.validate import validate_skill_package

STAMP = "2026-10-01T06:00:00Z"


def claim():
    return {
        "claim_key": "pool.liquidity.active_in_range",
        "subject": "pool.liquidity",
        "predicate": "active_in_range",
        "value": True,
        "qualifiers": {},
        "scope": {},
        "validity": {"as_of_source": "test-source"},
        "executable": False,
    }


class AgentAuthoringTests(unittest.TestCase):
    def fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        source = root / "source.md"
        source.write_text(
            "# Concentrated Liquidity\n"
            "Liquidity is active only while the current price is inside the position range.\n\n"
            "# Other\n"
            "Unrelated material.\n",
            encoding="utf-8",
        )
        digest = sha256(source.read_bytes()).hexdigest()
        draft = root / "draft.md"
        draft.write_text(
            "# SKILL: test\n"
            f"# source_sha256: {digest}\n\n"
            "# Liquidity is range bounded\n\n"
            "Liquidity becomes inactive outside the selected price range.\n\n"
            "## Structured claim\n\n"
            "```json\n"
            + json.dumps(claim(), indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )
        return root, source, draft

    def test_prepare_ignores_legacy_binding_identity_and_exposes_exact_sections(self):
        _, source, draft = self.fixture()
        prepared = prepare_agent_draft(
            source,
            draft,
            knowledge_id="test:liquidity",
            skill_slug="liquidity",
            title="Liquidity",
            default_kind="DECISION_RULE",
            occurred_at=STAMP,
        )
        self.assertEqual(len(prepared["units"]), 1)
        self.assertFalse(prepared["draft"]["legacy_source_binding_hints_authoritative"])
        self.assertEqual(prepared["normalized_source"]["sections"][0]["section_id"], "s001")
        self.assertFalse(prepared["promotion_authorized"])

    def test_legacy_validity_note_is_preserved_as_qualifier_without_schema_widening(self):
        _, source, draft = self.fixture()
        raw = json.loads(json.dumps(claim()))
        raw["validity"]["note"] = "World-as-of source; re-verify current governance."
        digest = sha256(source.read_bytes()).hexdigest()
        draft.write_text(
            "# SKILL: test\n"
            f"# source_sha256: {digest}\n\n"
            "# Governance is source-bounded\n\n"
            "Historical governance statement.\n\n"
            "## Structured claim\n\n"
            "```json\n"
            + json.dumps(raw, indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )

        prepared = prepare_agent_draft(
            source,
            draft,
            knowledge_id="test:liquidity",
            skill_slug="liquidity",
            title="Liquidity",
            default_kind="DECISION_RULE",
            occurred_at=STAMP,
        )
        unit = prepared["units"][0]
        self.assertNotIn("note", unit["claim"]["validity"])
        self.assertEqual(
            unit["claim"]["qualifiers"]["validity_note"],
            "World-as-of source; re-verify current governance.",
        )
        self.assertEqual(
            unit["compatibility_migrations"],
            ["validity.note->qualifiers.validity_note"],
        )

    def test_legacy_validity_note_conflict_refuses(self):
        _, source, draft = self.fixture()
        raw = json.loads(json.dumps(claim()))
        raw["validity"]["note"] = "note-a"
        raw["qualifiers"]["validity_note"] = "note-b"
        digest = sha256(source.read_bytes()).hexdigest()
        draft.write_text(
            "# SKILL: test\n"
            f"# source_sha256: {digest}\n\n"
            "# Conflicting note\n\n"
            "Conflict fixture.\n\n"
            "## Structured claim\n\n"
            "```json\n"
            + json.dumps(raw, indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(AgentAuthoringError, "conflicts"):
            prepare_agent_draft(
                source,
                draft,
                knowledge_id="test:liquidity",
                skill_slug="liquidity",
                title="Liquidity",
                default_kind="DECISION_RULE",
                occurred_at=STAMP,
            )

    def test_other_unknown_validity_keys_still_refuse(self):
        _, source, draft = self.fixture()
        raw = json.loads(json.dumps(claim()))
        raw["validity"]["mystery"] = "nope"
        digest = sha256(source.read_bytes()).hexdigest()
        draft.write_text(
            "# SKILL: test\n"
            f"# source_sha256: {digest}\n\n"
            "# Unknown validity\n\n"
            "Unknown validity fixture.\n\n"
            "## Structured claim\n\n"
            "```json\n"
            + json.dumps(raw, indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(AgentAuthoringError, "unsupported keys: mystery"):
            prepare_agent_draft(
                source,
                draft,
                knowledge_id="test:liquidity",
                skill_slug="liquidity",
                title="Liquidity",
                default_kind="DECISION_RULE",
                occurred_at=STAMP,
            )

    def test_finalize_builds_validated_package_but_never_promotes(self):
        root, source, draft = self.fixture()
        out = root / "materialized"
        plan = {
            "schema_version": "0.1",
            "judge_id": "test:model:faithfulness-v1",
            "units": [
                {
                    "claim_key": claim()["claim_key"],
                    "section_ids": ["s001"],
                    "entailment": "SUPPORTED",
                    "qualifiers_preserved": True,
                }
            ],
        }
        result = finalize_agent_draft(
            source,
            draft,
            plan,
            out,
            knowledge_id="test:liquidity",
            skill_slug="liquidity",
            title="Liquidity",
            default_kind="DECISION_RULE",
            occurred_at=STAMP,
            validator_id="automated-validator:test",
        )
        self.assertEqual(result["lifecycle_state"], "VALIDATED")
        self.assertFalse(result["promotion_performed"])
        package_root = Path(result["package_root"])
        files = {
            p.relative_to(package_root).as_posix(): p.read_bytes()
            for p in package_root.rglob("*")
            if p.is_file()
        }
        self.assertTrue(validate_skill_package(files)["valid"])
        lifecycle = json.loads(Path(result["validated_lifecycle"]).read_text())
        self.assertTrue(validate_lifecycle_record(lifecycle)["valid"])
        self.assertEqual(lifecycle["state"], "VALIDATED")

    def test_unsupported_model_verdict_refuses_without_output(self):
        root, source, draft = self.fixture()
        out = root / "materialized"
        plan = {
            "schema_version": "0.1",
            "judge_id": "test:model:faithfulness-v1",
            "units": [
                {
                    "claim_key": claim()["claim_key"],
                    "section_ids": ["s001"],
                    "entailment": "UNSUPPORTED",
                    "qualifiers_preserved": True,
                }
            ],
        }
        with self.assertRaisesRegex(AgentAuthoringError, "faithfulness validation refused"):
            finalize_agent_draft(
                source,
                draft,
                plan,
                out,
                knowledge_id="test:liquidity",
                skill_slug="liquidity",
                title="Liquidity",
                default_kind="DECISION_RULE",
                occurred_at=STAMP,
                validator_id="automated-validator:test",
            )
        self.assertFalse(out.exists())

    def test_prepare_binding_refusal_tells_the_agent_what_binding_is_expected(self):
        # Live agent failure 2026-10-01: a draft snapshot predating its source
        # binding read as a bare refusal; the agent burned attempts guessing.
        root, source, draft = self.fixture()
        unbound = root / "unbound.md"
        unbound.write_text(
            "# SKILL: test\n\n"
            "# Claim heading\n\n"
            "Body.\n\n"
            "## Structured claim\n\n"
            "```json\n"
            + json.dumps(claim(), indent=2, sort_keys=True)
            + "\n```\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AgentAuthoringError, r"sha256:.*# source_sha256"):
            prepare_agent_draft(
                source,
                unbound,
                knowledge_id="test:liquidity",
                skill_slug="liquidity",
                title="Liquidity",
                occurred_at=STAMP,
            )

    def test_prepare_claimless_draft_refusal_names_the_expected_block_format(self):
        # Live agent failure 2026-10-01: rewrites that dropped the
        # '## Structured claim' sections got no hint about the contract.
        root, source, draft = self.fixture()
        digest = sha256(source.read_bytes()).hexdigest()
        claimless = root / "claimless.md"
        claimless.write_text(
            "# SKILL: test\n"
            "# source_sha256: " + digest + "\n\n"
            "## DECISION RULES\n\n"
            "### R1 some rule\n"
            "Free prose without any structured claim section.\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(AgentAuthoringError, r"## Structured claim.*json fence"):
            prepare_agent_draft(
                source,
                claimless,
                knowledge_id="test:liquidity",
                skill_slug="liquidity",
                title="Liquidity",
                occurred_at=STAMP,
            )


if __name__ == "__main__":
    unittest.main()
