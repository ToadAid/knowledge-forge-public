from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.operator_promotion import (
    AUTHORIZATION_BOUNDARY,
    OperatorAuthorization,
    OperatorPromotionError,
    build_operator_routing_projection,
    persist_operator_promotion,
    promote_validated_skill,
    validate_operator_routing_projection,
)
from knowledge_forge.registry import (
    create_skill_registry,
    load_skill_registry,
    registry_bytes,
)
from test_registry import promoted_fixture


class OperatorPromotionTests(unittest.TestCase):
    def _fixture(self):
        manifest, promoted, claim_index, source_receipt = promoted_fixture(
            knowledge_id="knowledge-forge:operator-p7b"
        )
        validated = deepcopy(promoted)
        validated["events"] = validated["events"][:-1]
        validated["state"] = "VALIDATED"
        registry = create_skill_registry(
            created_at="2026-09-30T20:00:00Z"
        )
        authorization = OperatorAuthorization(
            principal_ref="operator:tommy",
            authorization_ref="host-auth:telegram:principal-1",
            verified_by_host=True,
        )
        return (
            registry,
            manifest,
            validated,
            claim_index,
            source_receipt,
            authorization,
        )

    def _promote(self):
        (
            registry,
            manifest,
            validated,
            claim_index,
            source_receipt,
            authorization,
        ) = self._fixture()
        result = promote_validated_skill(
            registry,
            manifest,
            validated,
            claim_index,
            source_receipt,
            authorization=authorization,
            decision_ref="operator-decision:p7b:001",
            occurred_at="2026-09-30T20:01:00Z",
        )
        return result, (
            registry,
            manifest,
            validated,
            claim_index,
            source_receipt,
        )

    def test_human_authorized_validated_skill_promotes_and_registers(self):
        result, _inputs = self._promote()

        lifecycle = result["promoted_lifecycle"]
        registry = result["registry"]
        receipt = result["receipt"]

        self.assertEqual(lifecycle["state"], "PROMOTED")
        self.assertEqual(
            lifecycle["events"][-1]["actor"],
            "operator:tommy",
        )
        self.assertEqual(
            lifecycle["events"][-1]["evidence"]["decision_ref"],
            "operator-decision:p7b:001",
        )
        self.assertIn(lifecycle["skill_id"], registry["skills"])
        self.assertEqual(
            receipt["authorization_boundary"],
            AUTHORIZATION_BOUNDARY,
        )
        self.assertFalse(receipt["model_authorized"])
        self.assertTrue(receipt["consumer_catalog_refresh_required"])
        self.assertEqual(receipt["execution_authority"], "NONE")
        self.assertEqual(receipt["tool_grants"], [])

    def test_unverified_host_authorization_is_refused(self):
        registry, manifest, validated, claim_index, source_receipt, _auth = (
            self._fixture()
        )
        bad = OperatorAuthorization(
            principal_ref="operator:tommy",
            authorization_ref="host-auth:missing",
            verified_by_host=False,
        )

        with self.assertRaisesRegex(OperatorPromotionError, "not verified"):
            promote_validated_skill(
                registry,
                manifest,
                validated,
                claim_index,
                source_receipt,
                authorization=bad,
                decision_ref="decision:1",
                occurred_at="2026-09-30T20:01:00Z",
            )

    def test_non_validated_candidate_cannot_skip_gate(self):
        registry, manifest, validated, claim_index, source_receipt, auth = (
            self._fixture()
        )
        compiled = deepcopy(validated)
        compiled["events"] = compiled["events"][:1]
        compiled["state"] = "COMPILED_CANDIDATE"

        with self.assertRaisesRegex(OperatorPromotionError, "VALIDATED"):
            promote_validated_skill(
                registry,
                manifest,
                compiled,
                claim_index,
                source_receipt,
                authorization=auth,
                decision_ref="decision:1",
                occurred_at="2026-09-30T20:01:00Z",
            )

    def test_empty_decision_ref_is_refused(self):
        registry, manifest, validated, claim_index, source_receipt, auth = (
            self._fixture()
        )
        with self.assertRaisesRegex(OperatorPromotionError, "decision_ref"):
            promote_validated_skill(
                registry,
                manifest,
                validated,
                claim_index,
                source_receipt,
                authorization=auth,
                decision_ref="   ",
                occurred_at="2026-09-30T20:01:00Z",
            )

    def test_package_semantic_tamper_is_refused_by_registry_binding(self):
        registry, manifest, validated, claim_index, source_receipt, auth = (
            self._fixture()
        )
        tampered = deepcopy(claim_index)
        tampered["claims"][0]["claim"]["value"] = 999

        with self.assertRaisesRegex(
            OperatorPromotionError,
            "promotion refused",
        ):
            promote_validated_skill(
                registry,
                manifest,
                validated,
                tampered,
                source_receipt,
                authorization=auth,
                decision_ref="decision:1",
                occurred_at="2026-09-30T20:01:00Z",
            )

    def test_promotion_does_not_mutate_caller_inputs(self):
        registry, manifest, validated, claim_index, source_receipt, auth = (
            self._fixture()
        )
        originals = [
            deepcopy(value)
            for value in (
                registry,
                manifest,
                validated,
                claim_index,
                source_receipt,
            )
        ]

        promote_validated_skill(
            registry,
            manifest,
            validated,
            claim_index,
            source_receipt,
            authorization=auth,
            decision_ref="decision:1",
            occurred_at="2026-09-30T20:01:00Z",
        )

        self.assertEqual(
            [
                registry,
                manifest,
                validated,
                claim_index,
                source_receipt,
            ],
            originals,
        )

    def test_routing_projection_is_registry_bound_zero_authority_evidence(self):
        result, _inputs = self._promote()
        projection = result["routing_projection"]
        report = validate_operator_routing_projection(projection)

        self.assertTrue(report["valid"])
        self.assertIn(
            "NOT_CONSUMER_CATALOG",
            projection["projection_kind"],
        )
        self.assertEqual(projection["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(projection["execution_authority"], "NONE")
        self.assertEqual(projection["tool_grants"], [])
        self.assertEqual(len(projection["skills"]), 1)
        self.assertEqual(
            projection["registry_sha256"],
            "sha256:"
            + sha256(registry_bytes(result["registry"])).hexdigest(),
        )

    def test_routing_projection_rejects_forged_tool_grant(self):
        result, _inputs = self._promote()
        bad = deepcopy(result["routing_projection"])
        bad["tool_grants"] = ["shell"]

        report = validate_operator_routing_projection(bad)
        self.assertFalse(report["valid"])
        self.assertIn(
            "routing projection must not grant tools",
            report["errors"],
        )

    def test_persistence_updates_registry_and_writes_immutable_receipts(self):
        result, inputs = self._promote()
        original_registry = inputs[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = root / "registry.json"
            lifecycle_path = root / "receipts" / "promoted-lifecycle.json"
            receipt_path = root / "receipts" / "promotion-receipt.json"
            registry_path.write_bytes(registry_bytes(original_registry))

            paths = persist_operator_promotion(
                result,
                registry_path=registry_path,
                promoted_lifecycle_path=lifecycle_path,
                receipt_path=receipt_path,
            )

            loaded = load_skill_registry(registry_path)
            self.assertEqual(loaded, result["registry"])
            self.assertEqual(
                json.loads(lifecycle_path.read_text(encoding="utf-8")),
                result["promoted_lifecycle"],
            )
            self.assertEqual(
                json.loads(receipt_path.read_text(encoding="utf-8")),
                result["receipt"],
            )
            self.assertEqual(paths["registry"], str(registry_path))
            self.assertNotIn("catalog", paths)

            # Restore only the authoritative pre-promotion registry inside this
            # test so the second call reaches the immutable-output overwrite
            # guard. Stale-registry refusal is covered independently below.
            registry_path.write_bytes(registry_bytes(original_registry))

            with self.assertRaisesRegex(
                OperatorPromotionError,
                "already exists",
            ):
                persist_operator_promotion(
                    result,
                    registry_path=registry_path,
                    promoted_lifecycle_path=lifecycle_path,
                    receipt_path=receipt_path,
                )

    def test_stale_registry_refuses_before_writing_outputs(self):
        result, _inputs = self._promote()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = root / "registry.json"
            lifecycle_path = root / "receipts" / "promoted-lifecycle.json"
            receipt_path = root / "receipts" / "promotion-receipt.json"

            stale = create_skill_registry(
                created_at="2026-09-30T20:00:01Z"
            )
            registry_path.write_bytes(registry_bytes(stale))

            with self.assertRaisesRegex(
                OperatorPromotionError,
                "changed since promotion was prepared",
            ):
                persist_operator_promotion(
                    result,
                    registry_path=registry_path,
                    promoted_lifecycle_path=lifecycle_path,
                    receipt_path=receipt_path,
                )

            self.assertFalse(lifecycle_path.exists())
            self.assertFalse(receipt_path.exists())

    def test_symlink_ancestor_on_immutable_output_is_refused(self):
        result, inputs = self._promote()
        original_registry = inputs[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            registry_path = root / "registry.json"
            real = root / "real"
            real.mkdir()
            linked = root / "linked"
            os.symlink(real, linked)
            registry_path.write_bytes(registry_bytes(original_registry))

            with self.assertRaisesRegex(
                OperatorPromotionError,
                "symlink",
            ):
                persist_operator_promotion(
                    result,
                    registry_path=registry_path,
                    promoted_lifecycle_path=linked / "promoted-lifecycle.json",
                    receipt_path=root / "promotion-receipt.json",
                )

            self.assertFalse((real / "promoted-lifecycle.json").exists())
            self.assertFalse((root / "promotion-receipt.json").exists())

    def test_build_projection_refuses_invalid_registry(self):
        registry, *_rest = self._fixture()
        bad = deepcopy(registry)
        bad["schema_version"] = "forged"
        with self.assertRaisesRegex(
            OperatorPromotionError,
            "invalid registry",
        ):
            build_operator_routing_projection(bad)


if __name__ == "__main__":
    unittest.main()
