from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge.consumption import (
    ConsumptionError,
    append_consumption_receipt,
    assess_consumption_taint,
    create_consumption_ledger,
    create_consumption_receipt,
    evaluate_registered_knowledge_taint,
    load_consumption_ledger,
    query_consumptions,
    query_tainted_consumptions,
    save_consumption_ledger,
    validate_consumption_ledger,
    validate_consumption_receipt,
)
from knowledge_forge.lifecycle import DISPUTED, REVOKED, SUSPENDED, transition_lifecycle
from knowledge_forge.registry import (
    create_skill_registry,
    fold_in_current_incident_lifecycle,
    register_promoted_skill,
)
from tests.test_registry import promoted_fixture


def registered(*, validity=None):
    registry = create_skill_registry(created_at="2026-09-24T12:00:00Z")
    manifest, lifecycle, claim_index, source_receipt = promoted_fixture(
        validity=validity or {},
    )
    registry = register_promoted_skill(
        registry,
        manifest,
        lifecycle,
        claim_index,
        source_receipt,
        registered_at="2026-09-24T12:01:00Z",
    )
    return registry, manifest, lifecycle, claim_index


def receipt_for(registry, claim_index, *, ref="answer:001", kind="ANSWER"):
    return create_consumption_receipt(
        registry,
        skill_id=claim_index["skill_id"],
        loaded_unit_ids=[claim_index["claims"][0]["unit_id"]],
        outcome_kind=kind,
        outcome_ref=ref,
        consumed_at="2026-09-24T12:02:00-04:00",
    )


def incident(lifecycle, target):
    if target in {SUSPENDED, DISPUTED}:
        evidence = {
            "incident_ref": "incident:knowledge-risk",
            "reason": f"{target.lower()} during review",
        }
    else:
        evidence = {
            "incident_ref": "incident:knowledge-risk",
            "reason": "confirmed invalid",
            "decision_ref": "decision:revoke",
        }
    return transition_lifecycle(
        lifecycle,
        target,
        occurred_at="2026-09-24T17:00:00Z",
        actor="operator:test",
        evidence=evidence,
    )


class ConsumptionReceiptTests(unittest.TestCase):
    def test_receipt_binds_exact_promoted_package_units_and_provenance(self):
        registry, _, _, claim_index = registered()
        receipt = receipt_for(registry, claim_index)

        report = validate_consumption_receipt(receipt)

        self.assertTrue(report["valid"], report["errors"])
        self.assertEqual(receipt["outcome_kind"], "ANSWER")
        self.assertEqual(receipt["outcome_ref"], "answer:001")
        self.assertEqual(
            receipt["claim_index_sha256"],
            registry["skills"][claim_index["skill_id"]]["versions"]["1"][
                "claim_index_sha256"
            ],
        )
        self.assertEqual(
            receipt["lifecycle_head_sha256"],
            registry["skills"][claim_index["skill_id"]]["versions"]["1"][
                "lifecycle_head_sha256"
            ],
        )
        self.assertEqual(len(receipt["loaded_units"]), 1)
        self.assertEqual(
            receipt["loaded_units"][0]["claim_sha256"],
            claim_index["claims"][0]["claim_sha256"],
        )
        self.assertEqual(
            receipt["loaded_units"][0]["source_bindings"],
            claim_index["claims"][0]["source_bindings"],
        )
        self.assertEqual(receipt["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(receipt["consumed_at"], "2026-09-24T16:02:00Z")

    def test_stopped_current_skill_cannot_create_new_consumption_receipt(self):
        registry, _, lifecycle, claim_index = registered()
        suspended = incident(lifecycle, SUSPENDED)
        registry = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="2026-09-24T17:00:00Z",
        )

        with self.assertRaisesRegex(ConsumptionError, "not consumable"):
            receipt_for(registry, claim_index)

    def test_loaded_unit_must_exist_in_exact_registered_claim_index(self):
        registry, _, _, claim_index = registered()

        with self.assertRaisesRegex(ConsumptionError, "not in registered claim_index"):
            create_consumption_receipt(
                registry,
                skill_id=claim_index["skill_id"],
                loaded_unit_ids=["claim-does-not-exist"],
                outcome_kind="PLAN",
                outcome_ref="plan:001",
                consumed_at="2026-09-24T16:02:00Z",
            )

    def test_receipt_id_detects_rehashed_content_tamper_boundary(self):
        registry, _, _, claim_index = registered()
        receipt = receipt_for(registry, claim_index)
        forged = deepcopy(receipt)
        forged["outcome_ref"] = "answer:forged"

        report = validate_consumption_receipt(forged)

        self.assertFalse(report["valid"])
        self.assertIn("consumption_id mismatch", report["errors"])


class ConsumptionLedgerTests(unittest.TestCase):
    def test_append_and_query_by_package_unit_and_output_kind(self):
        registry, _, _, claim_index = registered()
        answer = receipt_for(registry, claim_index, ref="answer:001", kind="ANSWER")
        plan = receipt_for(registry, claim_index, ref="plan:001", kind="PLAN")
        ledger = create_consumption_ledger(
            created_at="2026-09-24T16:00:00Z"
        )
        ledger = append_consumption_receipt(ledger, answer)
        ledger = append_consumption_receipt(ledger, plan)

        self.assertTrue(validate_consumption_ledger(ledger)["valid"])
        self.assertEqual(
            [item["outcome_ref"] for item in query_consumptions(
                ledger,
                skill_id=claim_index["skill_id"],
                skill_version=1,
            )],
            ["answer:001", "plan:001"],
        )
        self.assertEqual(
            [item["outcome_ref"] for item in query_consumptions(
                ledger,
                unit_id=claim_index["claims"][0]["unit_id"],
                outcome_kind="PLAN",
            )],
            ["plan:001"],
        )

    def test_ledger_round_trip_is_durable_and_deterministic(self):
        registry, _, _, claim_index = registered()
        receipt = receipt_for(registry, claim_index)
        ledger = append_consumption_receipt(
            create_consumption_ledger(created_at="2026-09-24T16:00:00Z"),
            receipt,
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "CONSUMPTION_LEDGER.json"
            save_consumption_ledger(ledger, path)
            first = path.read_bytes()
            loaded = load_consumption_ledger(path)
            save_consumption_ledger(loaded, path)
            second = path.read_bytes()

        self.assertEqual(loaded, ledger)
        self.assertEqual(first, second)


class ConsumptionTaintTests(unittest.TestCase):
    def test_later_suspend_taints_prior_answer_and_preserves_consumption_head(self):
        registry, _, lifecycle, claim_index = registered()
        receipt = receipt_for(registry, claim_index)
        old_head = receipt["lifecycle_head_sha256"]

        suspended = incident(lifecycle, SUSPENDED)
        registry = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="2026-09-24T17:00:00Z",
        )
        current_head = registry["skills"][claim_index["skill_id"]]["versions"]["1"][
            "lifecycle_head_sha256"
        ]

        assessment = assess_consumption_taint(
            registry,
            receipt,
            as_of="2026-09-24T17:01:00Z",
        )

        self.assertNotEqual(old_head, current_head)
        self.assertTrue(assessment["tainted"])
        self.assertIn("LIFECYCLE_SUSPENDED", assessment["taint_reasons"])
        self.assertEqual(assessment["outcome_ref"], "answer:001")

    def test_dispute_and_revoke_each_taint_prior_consumption(self):
        for target, reason in (
            (DISPUTED, "LIFECYCLE_DISPUTED"),
            (REVOKED, "LIFECYCLE_REVOKED"),
        ):
            with self.subTest(target=target):
                registry, _, lifecycle, claim_index = registered()
                receipt = receipt_for(registry, claim_index)
                registry = fold_in_current_incident_lifecycle(
                    registry,
                    incident(lifecycle, target),
                    updated_at="2026-09-24T17:00:00Z",
                )

                assessment = assess_consumption_taint(
                    registry,
                    receipt,
                    as_of="2026-09-24T17:01:00Z",
                )

                self.assertTrue(assessment["tainted"])
                self.assertIn(reason, assessment["taint_reasons"])

    def test_stale_loaded_unit_taints_prior_answer_without_lifecycle_mutation(self):
        registry, _, lifecycle, claim_index = registered(
            validity={"valid_until": "2026-09-24T17:00:00Z"},
        )
        receipt = receipt_for(registry, claim_index)
        lifecycle_before = deepcopy(
            registry["skills"][claim_index["skill_id"]]["versions"]["1"][
                "lifecycle_record"
            ]
        )

        assessment = assess_consumption_taint(
            registry,
            receipt,
            as_of="2026-09-24T17:00:01Z",
        )

        self.assertTrue(assessment["tainted"])
        self.assertIn("TEMPORAL_STALE", assessment["taint_reasons"])
        self.assertEqual(assessment["lifecycle_state"], "PROMOTED")
        self.assertEqual(
            registry["skills"][claim_index["skill_id"]]["versions"]["1"][
                "lifecycle_record"
            ],
            lifecycle_before,
        )

    def test_exact_expiry_is_not_tainted(self):
        registry, _, _, claim_index = registered(
            validity={"valid_until": "2026-09-24T17:00:00Z"},
        )
        receipt = receipt_for(registry, claim_index)

        assessment = assess_consumption_taint(
            registry,
            receipt,
            as_of="2026-09-24T17:00:00Z",
        )

        self.assertFalse(assessment["tainted"])
        self.assertEqual(assessment["taint_reasons"], [])

    def test_package_and_unit_taint_queries_are_explicit(self):
        registry, _, lifecycle, claim_index = registered()
        unit_id = claim_index["claims"][0]["unit_id"]

        package = evaluate_registered_knowledge_taint(
            registry,
            skill_id=claim_index["skill_id"],
            skill_version=1,
            as_of="2026-09-24T17:00:00Z",
        )
        unit = evaluate_registered_knowledge_taint(
            registry,
            skill_id=claim_index["skill_id"],
            skill_version=1,
            unit_id=unit_id,
            as_of="2026-09-24T17:00:00Z",
        )

        self.assertEqual(package["target"]["kind"], "PACKAGE")
        self.assertEqual(unit["target"]["kind"], "UNIT")
        self.assertFalse(package["tainted"])
        self.assertFalse(unit["tainted"])

        suspended = incident(lifecycle, SUSPENDED)
        stopped = fold_in_current_incident_lifecycle(
            registry,
            suspended,
            updated_at="2026-09-24T17:00:00Z",
        )
        stopped_unit = evaluate_registered_knowledge_taint(
            stopped,
            skill_id=claim_index["skill_id"],
            skill_version=1,
            unit_id=unit_id,
            as_of="2026-09-24T17:01:00Z",
        )
        self.assertTrue(stopped_unit["tainted"])
        self.assertIn("LIFECYCLE_SUSPENDED", stopped_unit["taint_reasons"])

    def test_unloaded_package_staleness_does_not_leak_into_output_taint_reasons(self):
        registry, _, lifecycle, claim_index = registered()
        receipt = receipt_for(registry, claim_index)
        registry = fold_in_current_incident_lifecycle(
            registry,
            incident(lifecycle, SUSPENDED),
            updated_at="2026-09-24T17:00:00Z",
        )

        package_result = {
            "target": {
                "kind": "PACKAGE",
                "skill_id": claim_index["skill_id"],
                "skill_version": 1,
            },
            "lifecycle_state": SUSPENDED,
            "temporal_condition": "STALE",
            "temporally_applicable": False,
            "tainted": True,
            "taint_reasons": ["LIFECYCLE_SUSPENDED", "TEMPORAL_STALE"],
            "as_of": "2026-09-24T17:01:00Z",
            "claim_index_sha256": registry["skills"][
                claim_index["skill_id"]
            ]["versions"]["1"]["claim_index_sha256"],
        }
        loaded_unit_result = {
            "target": {
                "kind": "UNIT",
                "skill_id": claim_index["skill_id"],
                "skill_version": 1,
                "unit_id": claim_index["claims"][0]["unit_id"],
            },
            "lifecycle_state": SUSPENDED,
            "temporal_condition": "CURRENT",
            "temporally_applicable": True,
            "tainted": True,
            "taint_reasons": ["LIFECYCLE_SUSPENDED"],
            "as_of": "2026-09-24T17:01:00Z",
            "claim_index_sha256": registry["skills"][
                claim_index["skill_id"]
            ]["versions"]["1"]["claim_index_sha256"],
        }

        with patch(
            "knowledge_forge.consumption.evaluate_registered_knowledge_taint",
            side_effect=[package_result, loaded_unit_result],
        ):
            assessment = assess_consumption_taint(
                registry,
                receipt,
                as_of="2026-09-24T17:01:00Z",
            )

        self.assertTrue(assessment["tainted"])
        self.assertEqual(
            assessment["taint_reasons"],
            ["LIFECYCLE_SUSPENDED"],
        )
        self.assertEqual(
            assessment["package_temporal_condition"],
            "STALE",
        )
        self.assertEqual(
            assessment["loaded_unit_taints"][0]["temporal_condition"],
            "CURRENT",
        )

    def test_query_tainted_consumptions_answers_which_outputs_depended_on_tainted_knowledge(self):
        registry, _, lifecycle, claim_index = registered()
        answer = receipt_for(registry, claim_index, ref="answer:001", kind="ANSWER")
        decision = receipt_for(
            registry,
            claim_index,
            ref="decision:042",
            kind="DECISION",
        )
        ledger = create_consumption_ledger(
            created_at="2026-09-24T16:00:00Z"
        )
        ledger = append_consumption_receipt(ledger, answer)
        ledger = append_consumption_receipt(ledger, decision)

        registry = fold_in_current_incident_lifecycle(
            registry,
            incident(lifecycle, REVOKED),
            updated_at="2026-09-24T17:00:00Z",
        )

        results = query_tainted_consumptions(
            registry,
            ledger,
            as_of="2026-09-24T17:01:00Z",
        )

        self.assertEqual(
            [item["outcome_ref"] for item in results],
            ["answer:001", "decision:042"],
        )
        self.assertTrue(all(item["tainted"] for item in results))
        self.assertTrue(
            all("LIFECYCLE_REVOKED" in item["taint_reasons"] for item in results)
        )


if __name__ == "__main__":
    unittest.main()
