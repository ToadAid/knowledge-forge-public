from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from hashlib import sha256
import json
import tempfile
import unittest

from knowledge_forge.emit import build_skill_package
from knowledge_forge.lifecycle import (
    PROMOTED,
    VALIDATED,
    create_lifecycle_record,
    evidence_sha256,
    transition_lifecycle,
)
from knowledge_forge.registry import (
    create_skill_registry,
    register_promoted_skill,
)
from knowledge_forge.temporal_integration import (
    CLOCK_AGENT_AHEAD,
    CLOCK_AGENT_BEHIND,
    CLOCK_MATCH,
    SOURCE_CHANGED,
    SOURCE_UNCHANGED,
    TemporalIntegrationError,
    evaluate_loaded_temporal_truth,
    load_current_skill_context,
)
from knowledge_forge.trust_wall import load_promoted_skill_context
from knowledge_forge.validate import validate_or_raise
from tests.test_trust_wall import _candidate, _faithfulness_report


def _temporal_package(root: Path, validity):
    source = root / "source.md"
    source.write_text(
        "# Approval Quorum\n"
        "The approval quorum is exactly 7 members.\n\n"
        "# Deployment Window\n"
        "Deployments happen on Tuesdays.\n\n"
        "# Tea Recipe\n"
        "Use 3 grams of tea per cup.\n",
        encoding="utf-8",
    )
    source_id = "sha256:" + sha256(source.read_bytes()).hexdigest()

    candidate = _candidate(source_id)
    candidate["units"][0]["claim"]["validity"] = dict(validity)

    files = build_skill_package(
        candidate,
        compiled_at="2026-09-24T19:00:00Z",
    )
    validation_report = validate_or_raise(files)
    manifest = json.loads(files["manifest.json"])
    receipt = json.loads(files["SOURCE_RECEIPT.json"])
    claim_index = json.loads(files["claim-index.json"])

    lifecycle = create_lifecycle_record(
        manifest,
        receipt,
        created_at="2026-09-24T19:01:00Z",
    )
    faithfulness = _faithfulness_report(lifecycle, claim_index)
    lifecycle = transition_lifecycle(
        lifecycle,
        VALIDATED,
        occurred_at="2026-09-24T19:02:00Z",
        actor="validator:test",
        evidence={
            "validator_id": "knowledge-forge.validate.v1",
            "package_receipt_sha256": lifecycle[
                "package_receipt_sha256"
            ],
            "validation_report": validation_report,
            "validation_report_sha256": evidence_sha256(
                validation_report
            ),
            "faithfulness_report": faithfulness,
            "faithfulness_report_sha256": evidence_sha256(
                faithfulness
            ),
        },
    )
    lifecycle = transition_lifecycle(
        lifecycle,
        PROMOTED,
        occurred_at="2026-09-24T19:03:00Z",
        actor="operator:test",
        evidence={"decision_ref": "decision:test:p4e"},
    )
    registry = register_promoted_skill(
        create_skill_registry(
            created_at="2026-09-24T19:00:00Z"
        ),
        manifest,
        lifecycle,
        claim_index,
        receipt,
        registered_at="2026-09-24T19:04:00Z",
    )

    skill_root = root / "skill"
    for relative, payload in files.items():
        path = skill_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    return skill_root, registry, manifest, source_id


class TemporalIntegrationTests(unittest.TestCase):
    def test_world_clock_current_load_is_annotated_per_selected_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {
                    "valid_from": "2026-09-24T18:00:00Z",
                    "valid_until": "2026-09-24T22:00:00Z",
                    "as_of_source": "source:test",
                },
            )
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=["market_price"],
                world_as_of="2026-09-24T20:00:00Z",
                agent_as_of="2026-09-24T20:00:00Z",
            )

        self.assertEqual(
            loaded["temporal_evidence"]["clock_relation"],
            CLOCK_MATCH,
        )
        self.assertTrue(
            loaded["temporal_evidence"]["current_use_allowed"]
        )
        self.assertEqual(
            loaded["reasoning_context"][0]["temporal_condition"],
            "CURRENT",
        )
        self.assertEqual(
            loaded["reasoning_context"][0]["world_as_of"],
            "2026-09-24T20:00:00Z",
        )

    def test_world_clock_wins_when_agent_clock_is_behind_expiry(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {"valid_until": "2026-09-24T20:00:00Z"},
            )
            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "not current at world clock.*STALE",
            ):
                load_current_skill_context(
                    str(skill_root),
                    registry,
                    skill_id=manifest["skill_id"],
                    question="What approval quorum is required?",
                    runtime_tools=[],
                    world_as_of="2026-09-24T21:00:00Z",
                    agent_as_of="2026-09-24T19:00:00Z",
                )

    def test_agent_clock_ahead_cannot_make_world_current_claim_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {"valid_until": "2026-09-24T21:00:00Z"},
            )
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
                world_as_of="2026-09-24T20:00:00Z",
                agent_as_of="2026-09-24T22:00:00Z",
            )

        temporal = loaded["temporal_evidence"]
        self.assertEqual(
            temporal["clock_relation"],
            CLOCK_AGENT_AHEAD,
        )
        self.assertEqual(temporal["conditions"], ["CURRENT"])
        self.assertTrue(temporal["current_use_allowed"])

    def test_clock_drift_is_diagnostic_and_signed(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {},
            )
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
                world_as_of="2026-09-24T20:00:00Z",
                agent_as_of="2026-09-24T19:59:00Z",
            )

        temporal = loaded["temporal_evidence"]
        self.assertEqual(
            temporal["clock_relation"],
            CLOCK_AGENT_BEHIND,
        )
        self.assertEqual(
            temporal["agent_offset_seconds"],
            -60.0,
        )
        self.assertEqual(temporal["authority"], "WORLD_CLOCK")

    def test_not_yet_valid_selected_claim_refuses_before_current_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {"valid_from": "2026-09-25T00:00:00Z"},
            )
            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "NOT_YET_VALID",
            ):
                load_current_skill_context(
                    str(skill_root),
                    registry,
                    skill_id=manifest["skill_id"],
                    question="What approval quorum is required?",
                    runtime_tools=[],
                    world_as_of="2026-09-24T20:00:00Z",
                )

    def test_unevaluable_selected_claim_refuses_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {"valid_until": "not-a-time"},
            )
            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "UNEVALUABLE",
            ):
                load_current_skill_context(
                    str(skill_root),
                    registry,
                    skill_id=manifest["skill_id"],
                    question="What approval quorum is required?",
                    runtime_tools=[],
                    world_as_of="2026-09-24T20:00:00Z",
                )

    def test_exact_valid_until_boundary_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {"valid_until": "2026-09-24T20:00:00Z"},
            )
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
                world_as_of="2026-09-24T20:00:00Z",
            )

        self.assertEqual(
            loaded["temporal_evidence"]["conditions"],
            ["CURRENT"],
        )

    def test_changed_source_reverification_blocks_even_current_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, source_id = _temporal_package(
                Path(tmp),
                {},
            )
            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "source re-verification blocks current use",
            ):
                load_current_skill_context(
                    str(skill_root),
                    registry,
                    skill_id=manifest["skill_id"],
                    question="What approval quorum is required?",
                    runtime_tools=[],
                    world_as_of="2026-09-24T20:00:00Z",
                    source_reverification={
                        source_id: {
                            "status": SOURCE_CHANGED,
                            "checked_at": "2026-09-24T19:59:00Z",
                            "evidence_ref": "source-check:test:changed",
                        },
                    },
                )

    def test_unchanged_source_reverification_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, source_id = _temporal_package(
                Path(tmp),
                {},
            )
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
                world_as_of="2026-09-24T20:00:00Z",
                source_reverification={
                    source_id: {
                        "status": SOURCE_UNCHANGED,
                        "checked_at": "2026-09-24T19:59:00Z",
                        "evidence_ref": "source-check:test:unchanged",
                    },
                },
            )

        temporal = loaded["temporal_evidence"]
        self.assertTrue(temporal["source_reverification_complete"])
        check = temporal["unit_results"][0][
            "source_reverification"
        ][0]
        self.assertEqual(check["status"], SOURCE_UNCHANGED)
        self.assertFalse(check["blocks_current_use"])

    def test_missing_source_reverification_is_explicit_not_false_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {},
            )
            raw = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
            )
            report = evaluate_loaded_temporal_truth(
                raw,
                registry,
                world_as_of="2026-09-24T20:00:00Z",
            )

        self.assertTrue(report["current_use_allowed"])
        self.assertFalse(report["source_reverification_complete"])
        check = report["unit_results"][0][
            "source_reverification"
        ][0]
        self.assertEqual(check["status"], "NOT_REVERIFIED")

    def test_forged_execution_authority_refuses_temporal_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _temporal_package(
                Path(tmp),
                {},
            )
            raw = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
            )
            forged = deepcopy(raw)
            forged["reasoning_context"][0]["execution_authority"] = "WRITE"

            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "execution authority mismatch",
            ):
                evaluate_loaded_temporal_truth(
                    forged,
                    registry,
                    world_as_of="2026-09-24T20:00:00Z",
                )

    def test_future_source_check_relative_to_world_clock_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, source_id = _temporal_package(
                Path(tmp),
                {},
            )
            with self.assertRaisesRegex(
                TemporalIntegrationError,
                "cannot be after world_as_of",
            ):
                load_current_skill_context(
                    str(skill_root),
                    registry,
                    skill_id=manifest["skill_id"],
                    question="What approval quorum is required?",
                    runtime_tools=[],
                    world_as_of="2026-09-24T20:00:00Z",
                    source_reverification={
                        source_id: {
                            "status": SOURCE_UNCHANGED,
                            "checked_at": "2026-09-24T20:01:00Z",
                        },
                    },
                )


if __name__ == "__main__":
    unittest.main()
