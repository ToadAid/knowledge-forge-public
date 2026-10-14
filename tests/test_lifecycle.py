from __future__ import annotations

from copy import deepcopy
import unittest

CLAIM_SHA = "sha256:" + ("e" * 64)


from knowledge_forge.lifecycle import (
    COMPILED_CANDIDATE,
    DISPUTED,
    PROMOTED,
    REVOKED,
    SUPERSEDED,
    SUSPENDED,
    VALIDATED,
    LifecycleError,
    create_lifecycle_record,
    evidence_sha256,
    transition_lifecycle,
    validate_lifecycle_record,
)


def fixtures():
    manifest = {
        "skill_id": "knowledge-forge:alpha",
        "skill_version": 1,
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": "knowledge-forge:alpha",
        "candidate_sha256": "sha256:" + ("9" * 64),
        "source_ids": ["sha256:" + ("a" * 64)],
        "units": [{"unit_id": "claim-test"}],
    }
    receipt = {
        "skill_id": "knowledge-forge:alpha",
        "skill_version": 1,
        "artifact_hashes": {"SKILL.md": "sha256:" + ("a" * 64)},
        "knowledge_id": "knowledge-forge:alpha",
        "candidate_sha256": "sha256:" + ("9" * 64),
        "source_ids": ["sha256:" + ("a" * 64)],
    }
    return manifest, receipt


def validation_evidence(record, *, valid=True, faithfulness_valid=True):
    validation_report = {
        "valid": valid,
        "errors": [] if valid else ["bad"],
        "security_findings": [],
        "claim_sha256_by_unit": {
            unit_id: CLAIM_SHA for unit_id in record["unit_ids"]
        },
    }
    faithfulness_report = {
        "schema_version": "0.1",
        "state": "VALIDATED" if faithfulness_valid else "FAILED_VALIDATION",
        "valid": faithfulness_valid,
        "candidate_sha256": record["candidate_sha256"],
        "knowledge_id": record["knowledge_id"],
        "source_ids": record["source_ids"],
        "judge_id": "test.faithfulness.v1",
        "unit_results": [{
            "unit_id": "claim-test",
            "claim_sha256": CLAIM_SHA,
            "entailment": "SUPPORTED" if faithfulness_valid else "UNSUPPORTED",
            "qualifiers_preserved": faithfulness_valid,
            "support": [{
                "source_id": record["source_ids"][0],
                "section_id": "s001",
                "section_sha256": "1" * 64,
                "start_byte": 0,
                "end_byte": 1,
                "span_sha256": "2" * 64,
            }],
        }],
        "errors": [] if faithfulness_valid else ["unsupported"],
    }
    return {
        "validator_id": "knowledge-forge.validate.v1",
        "package_receipt_sha256": record["package_receipt_sha256"],
        "validation_report": validation_report,
        "validation_report_sha256": evidence_sha256(validation_report),
        "faithfulness_report": faithfulness_report,
        "faithfulness_report_sha256": evidence_sha256(faithfulness_report),
    }



class LifecycleTests(unittest.TestCase):
    def _record(self):
        manifest, receipt = fixtures()
        return create_lifecycle_record(manifest, receipt, created_at="2026-09-23T01:00:00Z")

    def test_happy_path_is_linear_and_append_only(self):
        original = self._record()
        snapshot = deepcopy(original)
        validated = transition_lifecycle(
            original,
            VALIDATED,
            occurred_at="2026-09-23T01:01:00Z",
            actor="validator:test",
            evidence=validation_evidence(original),
        )
        promoted = transition_lifecycle(
            validated,
            PROMOTED,
            occurred_at="2026-09-23T01:02:00Z",
            actor="operator:tommy",
            evidence={"decision_ref": "local-approval:stage2-proof"},
        )
        superseded = transition_lifecycle(
            promoted,
            SUPERSEDED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:tommy",
            evidence={
                "replacement_skill_id": "knowledge-forge:alpha",
                "replacement_skill_version": 2,
            },
        )

        self.assertEqual(original, snapshot)
        self.assertEqual(original["state"], COMPILED_CANDIDATE)
        self.assertEqual(validated["state"], VALIDATED)
        self.assertEqual(promoted["state"], PROMOTED)
        self.assertEqual(superseded["state"], SUPERSEDED)
        self.assertEqual(len(superseded["events"]), 4)
        self.assertTrue(validate_lifecycle_record(superseded)["valid"])

    def test_state_skip_is_refused(self):
        with self.assertRaisesRegex(LifecycleError, "illegal lifecycle transition"):
            transition_lifecycle(
                self._record(),
                PROMOTED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="operator:test",
                evidence={"decision_ref": "nope"},
            )

    def test_validation_requires_clean_report(self):
        record = self._record()
        with self.assertRaisesRegex(LifecycleError, "valid validation_report"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=validation_evidence(record, valid=False),
            )

    def test_validation_evidence_must_bind_exact_package(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence["package_receipt_sha256"] = "sha256:" + ("0" * 64)
        with self.assertRaisesRegex(LifecycleError, "not bound to this package receipt"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_validation_requires_faithfulness_report(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence.pop("faithfulness_report")
        evidence.pop("faithfulness_report_sha256")
        with self.assertRaisesRegex(LifecycleError, "faithfulness_report"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_failed_faithfulness_cannot_validate(self):
        record = self._record()
        with self.assertRaisesRegex(LifecycleError, "valid faithfulness_report"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=validation_evidence(record, faithfulness_valid=False),
            )

    def test_faithfulness_candidate_binding_cannot_lie(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence["faithfulness_report"]["candidate_sha256"] = "sha256:" + ("0" * 64)
        evidence["faithfulness_report_sha256"] = evidence_sha256(evidence["faithfulness_report"])
        with self.assertRaisesRegex(LifecycleError, "not bound to lifecycle candidate"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_faithfulness_claim_digest_must_match_validated_package_claim(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence["faithfulness_report"]["unit_results"][0]["claim_sha256"] = (
            "sha256:" + ("f" * 64)
        )
        evidence["faithfulness_report_sha256"] = evidence_sha256(
            evidence["faithfulness_report"]
        )
        with self.assertRaisesRegex(
            LifecycleError,
            "claim digest does not match validated package claim",
        ):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_report_hash_mismatch_is_refused(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence["faithfulness_report_sha256"] = "sha256:" + ("0" * 64)
        with self.assertRaisesRegex(LifecycleError, "faithfulness_report hash mismatch"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_faithfulness_report_must_cover_exact_package_unit_set(self):
        manifest, receipt = fixtures()
        manifest["units"] = [
            {"unit_id": "claim-test"},
            {"unit_id": "claim-second"},
        ]
        record = create_lifecycle_record(
            manifest,
            receipt,
            created_at="2026-09-23T01:00:00Z",
        )
        with self.assertRaisesRegex(LifecycleError, "exact lifecycle package unit set"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=validation_evidence(record),
            )

    def test_faithfulness_report_requires_nonempty_verified_support(self):
        record = self._record()
        evidence = validation_evidence(record)
        evidence["faithfulness_report"]["unit_results"][0]["support"] = []
        evidence["faithfulness_report_sha256"] = evidence_sha256(
            evidence["faithfulness_report"]
        )
        with self.assertRaisesRegex(LifecycleError, "non-empty verified support"):
            transition_lifecycle(
                record,
                VALIDATED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="validator:test",
                evidence=evidence,
            )

    def test_promotion_requires_explicit_decision_reference(self):
        record = self._record()
        validated = transition_lifecycle(
            record,
            VALIDATED,
            occurred_at="2026-09-23T01:01:00Z",
            actor="validator:test",
            evidence=validation_evidence(record),
        )
        with self.assertRaisesRegex(LifecycleError, "decision_ref"):
            transition_lifecycle(
                validated,
                PROMOTED,
                occurred_at="2026-09-23T01:02:00Z",
                actor="operator:test",
                evidence={},
            )

    def _promoted_record(self):
        record = self._record()
        validated = transition_lifecycle(
            record,
            VALIDATED,
            occurred_at="2026-09-23T01:01:00Z",
            actor="validator:test",
            evidence=validation_evidence(record),
        )
        return transition_lifecycle(
            validated,
            PROMOTED,
            occurred_at="2026-09-23T01:02:00Z",
            actor="operator:test",
            evidence={"decision_ref": "approval:incident-test"},
        )

    def test_promoted_can_suspend_without_successor(self):
        promoted = self._promoted_record()
        suspended = transition_lifecycle(
            promoted,
            SUSPENDED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:source-risk",
                "reason": "Source applicability is under emergency review.",
            },
        )
        self.assertEqual(suspended["state"], SUSPENDED)
        self.assertNotIn("replacement_skill_id", suspended["events"][-1]["evidence"])
        self.assertTrue(validate_lifecycle_record(suspended)["valid"])

    def test_promoted_can_enter_dispute_without_successor(self):
        promoted = self._promoted_record()
        disputed = transition_lifecycle(
            promoted,
            DISPUTED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="reviewer:test",
            evidence={
                "incident_ref": "incident:claim-dispute",
                "reason": "A structured claim is disputed.",
            },
        )
        self.assertEqual(disputed["state"], DISPUTED)
        self.assertTrue(validate_lifecycle_record(disputed)["valid"])

    def test_revocation_is_terminal_and_requires_explicit_decision(self):
        promoted = self._promoted_record()
        with self.assertRaisesRegex(LifecycleError, "decision_ref"):
            transition_lifecycle(
                promoted,
                REVOKED,
                occurred_at="2026-09-23T01:03:00Z",
                actor="operator:test",
                evidence={
                    "incident_ref": "incident:bad-source",
                    "reason": "Package must be permanently withdrawn.",
                },
            )

        revoked = transition_lifecycle(
            promoted,
            REVOKED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:bad-source",
                "reason": "Package must be permanently withdrawn.",
                "decision_ref": "decision:revoke-1",
            },
        )
        self.assertEqual(revoked["state"], REVOKED)
        self.assertTrue(validate_lifecycle_record(revoked)["valid"])

        with self.assertRaisesRegex(LifecycleError, "illegal lifecycle transition"):
            transition_lifecycle(
                revoked,
                PROMOTED,
                occurred_at="2026-09-23T01:04:00Z",
                actor="operator:test",
                evidence={"decision_ref": "decision:no-restore"},
            )

    def test_suspended_may_restore_only_against_active_incident(self):
        promoted = self._promoted_record()
        suspended = transition_lifecycle(
            promoted,
            SUSPENDED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:temporary-stop",
                "reason": "Emergency stop while evidence is checked.",
            },
        )

        with self.assertRaisesRegex(LifecycleError, "active incident_ref"):
            transition_lifecycle(
                suspended,
                PROMOTED,
                occurred_at="2026-09-23T01:04:00Z",
                actor="operator:test",
                evidence={
                    "decision_ref": "decision:restore",
                    "incident_ref": "incident:wrong",
                    "resolution_ref": "resolution:proof",
                },
            )

        restored = transition_lifecycle(
            suspended,
            PROMOTED,
            occurred_at="2026-09-23T01:04:00Z",
            actor="operator:test",
            evidence={
                "decision_ref": "decision:restore",
                "incident_ref": "incident:temporary-stop",
                "resolution_ref": "resolution:proof",
            },
        )
        self.assertEqual(restored["state"], PROMOTED)
        self.assertTrue(validate_lifecycle_record(restored)["valid"])

    def test_incident_escalation_preserves_active_incident_ref(self):
        promoted = self._promoted_record()
        suspended = transition_lifecycle(
            promoted,
            SUSPENDED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:continuity",
                "reason": "Emergency stop.",
            },
        )

        with self.assertRaisesRegex(
            LifecycleError,
            "preserve the active incident_ref",
        ):
            transition_lifecycle(
                suspended,
                DISPUTED,
                occurred_at="2026-09-23T01:04:00Z",
                actor="reviewer:test",
                evidence={
                    "incident_ref": "incident:different",
                    "reason": "Escalated review.",
                },
            )

        disputed = transition_lifecycle(
            suspended,
            DISPUTED,
            occurred_at="2026-09-23T01:04:00Z",
            actor="reviewer:test",
            evidence={
                "incident_ref": "incident:continuity",
                "reason": "Escalated review.",
            },
        )
        self.assertEqual(disputed["state"], DISPUTED)
        self.assertTrue(validate_lifecycle_record(disputed)["valid"])

    def test_incident_revocation_preserves_active_incident_ref(self):
        promoted = self._promoted_record()
        disputed = transition_lifecycle(
            promoted,
            DISPUTED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="reviewer:test",
            evidence={
                "incident_ref": "incident:revocation-continuity",
                "reason": "Claim is disputed.",
            },
        )

        with self.assertRaisesRegex(
            LifecycleError,
            "reference the active incident_ref",
        ):
            transition_lifecycle(
                disputed,
                REVOKED,
                occurred_at="2026-09-23T01:04:00Z",
                actor="operator:test",
                evidence={
                    "incident_ref": "incident:different",
                    "reason": "Revoke package.",
                    "decision_ref": "decision:revoke",
                },
            )

        revoked = transition_lifecycle(
            disputed,
            REVOKED,
            occurred_at="2026-09-23T01:04:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:revocation-continuity",
                "reason": "Revoke package.",
                "decision_ref": "decision:revoke",
            },
        )
        self.assertEqual(revoked["state"], REVOKED)
        self.assertTrue(validate_lifecycle_record(revoked)["valid"])

    def test_incident_state_can_be_superseded_and_historical_version_can_revoke(self):
        promoted = self._promoted_record()
        disputed = transition_lifecycle(
            promoted,
            DISPUTED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="reviewer:test",
            evidence={
                "incident_ref": "incident:old-version",
                "reason": "Current version is disputed while replacement is prepared.",
            },
        )
        superseded = transition_lifecycle(
            disputed,
            SUPERSEDED,
            occurred_at="2026-09-23T01:04:00Z",
            actor="operator:test",
            evidence={
                "replacement_skill_id": "knowledge-forge:alpha",
                "replacement_skill_version": 2,
            },
        )
        revoked = transition_lifecycle(
            superseded,
            REVOKED,
            occurred_at="2026-09-23T01:05:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:historical-taint",
                "reason": "Historical package is confirmed invalid.",
                "decision_ref": "decision:historical-revoke",
            },
        )
        self.assertEqual(revoked["state"], REVOKED)
        self.assertTrue(validate_lifecycle_record(revoked)["valid"])

    def test_incident_transition_is_not_allowed_before_promotion(self):
        with self.assertRaisesRegex(LifecycleError, "illegal lifecycle transition"):
            transition_lifecycle(
                self._record(),
                SUSPENDED,
                occurred_at="2026-09-23T01:01:00Z",
                actor="operator:test",
                evidence={
                    "incident_ref": "incident:nope",
                    "reason": "Not promoted.",
                },
            )

    def test_replayed_lifecycle_rejects_rehashed_malformed_incident_evidence(self):
        promoted = self._promoted_record()
        suspended = transition_lifecycle(
            promoted,
            SUSPENDED,
            occurred_at="2026-09-23T01:03:00Z",
            actor="operator:test",
            evidence={
                "incident_ref": "incident:replay-proof",
                "reason": "Valid original reason.",
            },
        )
        forged = deepcopy(suspended)
        forged["events"][-1]["evidence"]["reason"] = ""
        payload = {
            key: deepcopy(value)
            for key, value in forged["events"][-1].items()
            if key != "event_sha256"
        }
        forged["events"][-1]["event_sha256"] = evidence_sha256(payload)

        report = validate_lifecycle_record(forged)
        self.assertFalse(report["valid"])
        self.assertTrue(
            any(
                "lifecycle transition evidence invalid" in error
                for error in report["errors"]
            )
        )

    def test_tampered_history_or_binding_is_refused(self):
        record = self._record()
        record["events"][0]["actor"] = "tampered"
        report = validate_lifecycle_record(record)
        self.assertFalse(report["valid"])
        self.assertIn("lifecycle event digest mismatch", report["errors"])

        record = self._record()
        record["skill_version"] = 99
        report = validate_lifecycle_record(record)
        self.assertFalse(report["valid"])
        self.assertIn("lifecycle package binding mismatch: skill_version", report["errors"])

    def test_supersede_requires_newer_or_different_replacement(self):
        record = self._record()
        validated = transition_lifecycle(
            record,
            VALIDATED,
            occurred_at="2026-09-23T01:01:00Z",
            actor="validator:test",
            evidence=validation_evidence(record),
        )
        promoted = transition_lifecycle(
            validated,
            PROMOTED,
            occurred_at="2026-09-23T01:02:00Z",
            actor="operator:test",
            evidence={"decision_ref": "approval:1"},
        )
        with self.assertRaisesRegex(LifecycleError, "different or newer"):
            transition_lifecycle(
                promoted,
                SUPERSEDED,
                occurred_at="2026-09-23T01:03:00Z",
                actor="operator:test",
                evidence={
                    "replacement_skill_id": "knowledge-forge:alpha",
                    "replacement_skill_version": 1,
                },
            )


if __name__ == "__main__":
    unittest.main()
