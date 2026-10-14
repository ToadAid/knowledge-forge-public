from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.claim_index import claim_index_artifact_sha256
from knowledge_forge.claims import unit_id_for
from knowledge_forge.conflicts import claim_sha256
from knowledge_forge.lifecycle import (
    PROMOTED,
    VALIDATED,
    create_lifecycle_record,
    evidence_sha256,
    transition_lifecycle,
)
from knowledge_forge.provenance import skill_id_for
from knowledge_forge.registry import (
    SkillRegistryError,
    create_skill_registry,
    discover_skill,
    load_skill_registry,
    register_promoted_skill,
    registry_bytes,
    save_skill_registry,
    validate_skill_registry,
)


PROFILE_ID = "sha256:" + ("8" * 64)


def _digest_for(*parts):
    raw = "|".join(str(part) for part in parts).encode("utf-8")
    return "sha256:" + sha256(raw).hexdigest()


def promoted_fixture(
    *,
    version: int = 1,
    knowledge_id: str = "knowledge-forge:alpha",
    value=5,
    scope=None,
    qualifiers=None,
    validity=None,
    subject="protocol.fee",
    predicate="equals",
    decision_ref: str | None = None,
):
    skill_id = skill_id_for(knowledge_id, PROFILE_ID)
    claim_key = "fee.value"
    unit_id = unit_id_for(knowledge_id, claim_key)
    source_id = _digest_for("source", knowledge_id, version, value)
    candidate_digest = _digest_for("candidate", knowledge_id, version, value, scope)
    claim = {
        "claim_key": claim_key,
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "qualifiers": qualifiers or {},
        "scope": scope or {},
        "validity": validity or {},
        "executable": False,
    }
    semantic_digest = claim_sha256(claim)
    claim_index = {
        "schema_version": "0.1",
        "skill_id": skill_id,
        "skill_version": version,
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "compiler_profile_id": PROFILE_ID,
        "source_ids": [source_id],
        "claims": [{
            "claim_ref": f"{skill_id}#{unit_id}",
            "unit_id": unit_id,
            "claim_key": claim_key,
            "kind": "DEFINITION",
            "claim_sha256": semantic_digest,
            "claim": claim,
            "source_bindings": [{
                "source_id": source_id,
                "section_ids": ["s001"],
            }],
        }],
    }
    claim_index_digest = claim_index_artifact_sha256(claim_index)
    manifest = {
        "schema_version": "0.6",
        "skill_id": skill_id,
        "skill_version": version,
        "state": "COMPILED_CANDIDATE",
        "authority": "KNOWLEDGE_ONLY",
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "title": f"{knowledge_id} v{version}",
        "purpose": "reference",
        "source_ids": [source_id],
        "compiler": {
            "compiler_profile_id": PROFILE_ID,
        },
        "claim_index": {
            "path": "claim-index.json",
            "schema_version": "0.1",
            "sha256": claim_index_digest,
            "claim_count": 1,
        },
        "units": [{
            "unit_id": unit_id,
            "claim_key": claim_key,
            "kind": "DEFINITION",
        }],
    }
    source_receipt = {
        "skill_id": skill_id,
        "skill_version": version,
        "artifact_hashes": {
            "SKILL.md": _digest_for("skill", knowledge_id, version),
            "claim-index.json": claim_index_digest,
        },
        "knowledge_id": knowledge_id,
        "candidate_sha256": candidate_digest,
        "source_ids": [source_id],
    }
    lifecycle = create_lifecycle_record(
        manifest,
        source_receipt,
        created_at="2026-09-23T01:00:00Z",
    )
    lifecycle = transition_lifecycle(
        lifecycle,
        VALIDATED,
        occurred_at="2026-09-23T01:01:00Z",
        actor="validator:test",
        evidence=(lambda validation_report, faithfulness_report: {
            "validator_id": "knowledge-forge.validate.v1",
            "package_receipt_sha256": lifecycle["package_receipt_sha256"],
            "validation_report": validation_report,
            "validation_report_sha256": evidence_sha256(validation_report),
            "faithfulness_report": faithfulness_report,
            "faithfulness_report_sha256": evidence_sha256(faithfulness_report),
        })(
            {
                "valid": True,
                "errors": [],
                "security_findings": [],
                "claim_sha256_by_unit": {
                    unit_id: semantic_digest,
                },
            },
            {
                "schema_version": "0.1",
                "state": "VALIDATED",
                "valid": True,
                "candidate_sha256": lifecycle["candidate_sha256"],
                "knowledge_id": lifecycle["knowledge_id"],
                "source_ids": lifecycle["source_ids"],
                "judge_id": "test.faithfulness.v1",
                "unit_results": [{
                    "unit_id": unit_id,
                    "claim_sha256": semantic_digest,
                    "entailment": "SUPPORTED",
                    "qualifiers_preserved": True,
                    "support": [{
                        "source_id": source_id,
                        "section_id": "s001",
                        "section_sha256": "1" * 64,
                        "start_byte": 0,
                        "end_byte": 1,
                        "span_sha256": "2" * 64,
                    }],
                }],
                "errors": [],
            },
        ),
    )
    lifecycle = transition_lifecycle(
        lifecycle,
        PROMOTED,
        occurred_at="2026-09-23T01:02:00Z",
        actor="operator:test",
        evidence={
            "decision_ref": decision_ref
            or f"decision:test:{knowledge_id}:v{version}"
        },
    )
    return manifest, lifecycle, claim_index, source_receipt


class SkillRegistryTests(unittest.TestCase):
    def test_promoted_skill_registers_verified_claim_index(self):
        registry = create_skill_registry(created_at="2026-09-23T01:00:00Z")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()

        updated = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="2026-09-23T01:03:00Z",
        )

        self.assertEqual(registry["skills"], {})
        current = discover_skill(updated, manifest["skill_id"])
        explicit = discover_skill(updated, manifest["skill_id"], version=1)
        self.assertEqual(current, explicit)
        self.assertEqual(current["state"], "PROMOTED")
        self.assertEqual(current["claim_index"], claim_index)
        self.assertEqual(current["source_receipt"], source_receipt)
        self.assertEqual(
            current["claim_index_sha256"],
            manifest["claim_index"]["sha256"],
        )
        self.assertEqual(updated["conflict_sets"], [])

    def test_non_promoted_lifecycle_is_refused(self):
        registry = create_skill_registry(created_at="t0")
        manifest, promoted, claim_index, source_receipt = promoted_fixture()

        candidate = deepcopy(promoted)
        candidate["state"] = "COMPILED_CANDIDATE"
        candidate["events"] = candidate["events"][:1]

        validated = deepcopy(promoted)
        validated["state"] = "VALIDATED"
        validated["events"] = validated["events"][:2]

        for lifecycle in (candidate, validated):
            with self.assertRaisesRegex(SkillRegistryError, "PROMOTED"):
                register_promoted_skill(
                    registry,
                    manifest,
                    lifecycle,
                    claim_index,
                    source_receipt,
                    registered_at="t1",
                )

    def test_tampered_lifecycle_is_refused(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        lifecycle["events"][1]["actor"] = "tampered"

        with self.assertRaisesRegex(SkillRegistryError, "invalid lifecycle"):
            register_promoted_skill(
                registry,
                manifest,
                lifecycle,
                claim_index,
                source_receipt,
                registered_at="t1",
            )

    def test_manifest_lifecycle_identity_mismatch_is_refused(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        manifest["skill_version"] = 2

        with self.assertRaisesRegex(SkillRegistryError, "skill_version"):
            register_promoted_skill(
                registry,
                manifest,
                lifecycle,
                claim_index,
                source_receipt,
                registered_at="t1",
            )

    def test_duplicate_or_new_version_requires_p2c(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        registry = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="t1",
        )

        with self.assertRaisesRegex(SkillRegistryError, "already registered"):
            register_promoted_skill(
                registry,
                manifest,
                lifecycle,
                claim_index,
                source_receipt,
                registered_at="t2",
            )

        manifest_v2, lifecycle_v2, claim_index_v2, source_receipt_v2 = promoted_fixture(version=2)
        with self.assertRaisesRegex(SkillRegistryError, "P2C"):
            register_promoted_skill(
                registry,
                manifest_v2,
                lifecycle_v2,
                claim_index_v2,
                source_receipt_v2,
                registered_at="t2",
            )

    def test_registry_refuses_rehashed_claim_index_semantics_not_validated(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        tampered = deepcopy(claim_index)
        tampered["claims"][0]["claim"]["value"] = 99
        tampered["claims"][0]["claim_sha256"] = claim_sha256(
            tampered["claims"][0]["claim"]
        )
        manifest = deepcopy(manifest)
        manifest["claim_index"]["sha256"] = claim_index_artifact_sha256(tampered)

        with self.assertRaisesRegex(
            SkillRegistryError,
            "source receipt claim-index artifact hash mismatch",
        ):
            register_promoted_skill(
                registry,
                manifest,
                lifecycle,
                tampered,
                source_receipt,
                registered_at="t1",
            )

    def test_registry_validation_refuses_rehashed_stored_claim_semantics(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        registry = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="t1",
        )
        bad = deepcopy(registry)
        entry = bad["skills"][manifest["skill_id"]]["versions"]["1"]
        entry["claim_index"]["claims"][0]["claim"]["value"] = 99
        entry["claim_index"]["claims"][0]["claim_sha256"] = claim_sha256(
            entry["claim_index"]["claims"][0]["claim"]
        )
        entry["claim_index_sha256"] = claim_index_artifact_sha256(
            entry["claim_index"]
        )

        report = validate_skill_registry(bad)

        self.assertFalse(report["valid"])
        self.assertIn(
            f"registry claim_index semantics do not match validated lifecycle: {manifest['skill_id']}@1",
            report["errors"],
        )

    def test_registry_refuses_claim_index_not_bound_to_promoted_package_receipt(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        tampered = deepcopy(claim_index)
        tampered["claims"][0]["source_bindings"][0]["section_ids"] = ["s999"]
        manifest = deepcopy(manifest)
        manifest["claim_index"]["sha256"] = claim_index_artifact_sha256(tampered)

        with self.assertRaisesRegex(
            SkillRegistryError,
            "source receipt claim-index artifact hash mismatch",
        ):
            register_promoted_skill(
                registry,
                manifest,
                lifecycle,
                tampered,
                source_receipt,
                registered_at="t1",
            )

    def test_registry_validation_refuses_provenance_only_claim_index_tamper(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        registry = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="t1",
        )
        bad = deepcopy(registry)
        entry = bad["skills"][manifest["skill_id"]]["versions"]["1"]
        entry["claim_index"]["claims"][0]["source_bindings"][0]["section_ids"] = [
            "s999"
        ]
        entry["claim_index_sha256"] = claim_index_artifact_sha256(
            entry["claim_index"]
        )

        report = validate_skill_registry(bad)

        self.assertFalse(report["valid"])
        self.assertIn(
            f"registry source_receipt claim-index artifact mismatch: {manifest['skill_id']}@1",
            report["errors"],
        )

    def test_durable_round_trip_is_deterministic(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        registry = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="t1",
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            save_skill_registry(registry, path)
            first = path.read_bytes()
            loaded = load_skill_registry(path)
            save_skill_registry(loaded, path)
            second = path.read_bytes()

        self.assertEqual(first, second)
        self.assertEqual(first, registry_bytes(registry))

    def test_registry_revalidates_nested_lifecycle_proof(self):
        registry = create_skill_registry(created_at="t0")
        manifest, lifecycle, claim_index, source_receipt = promoted_fixture()
        registry = register_promoted_skill(
            registry,
            manifest,
            lifecycle,
            claim_index,
            source_receipt,
            registered_at="t1",
        )

        bad = deepcopy(registry)
        entry = bad["skills"][manifest["skill_id"]]["versions"]["1"]
        entry["lifecycle_record"]["events"][1]["actor"] = "tampered"

        report = validate_skill_registry(bad)
        self.assertFalse(report["valid"])
        self.assertIn(
            f"registry lifecycle invalid: {manifest['skill_id']}@1",
            report["errors"],
        )


if __name__ == "__main__":
    unittest.main()
