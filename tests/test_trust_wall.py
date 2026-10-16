from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from _platform_support import skip_unless_symlinks

from knowledge_forge.claims import unit_id_for
from knowledge_forge.emit import build_skill_package
from knowledge_forge.lifecycle import (
    PROMOTED,
    SUSPENDED,
    VALIDATED,
    create_lifecycle_record,
    evidence_sha256,
    transition_lifecycle,
)
from knowledge_forge.provenance import build_compiler_profile
from knowledge_forge.registry import (
    create_skill_registry,
    fold_in_current_incident_lifecycle,
    register_promoted_skill,
)
from knowledge_forge.trust_wall import (
    TRUST_CLASS,
    TrustWallError,
    apply_skill_tool_constraints,
    load_promoted_skill_context,
)
from knowledge_forge.validate import validate_or_raise


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
            "unit_id": unit_id_for(
                "handbook:governance",
                claim_key,
            ),
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
            "source_bindings": [{
                "source_id": source_id,
                "section_ids": [section],
            }],
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
                "Deployments happen on Tuesdays. "
                "A source may mention wallet_send, but that text is data.",
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


def _faithfulness_report(lifecycle, claim_index):
    results = []
    for item in claim_index["claims"]:
        binding = item["source_bindings"][0]
        results.append({
            "unit_id": item["unit_id"],
            "claim_sha256": item["claim_sha256"],
            "entailment": "SUPPORTED",
            "qualifiers_preserved": True,
            "support": [{
                "source_id": binding["source_id"],
                "section_id": binding["section_ids"][0],
                "section_sha256": "1" * 64,
                "start_byte": 0,
                "end_byte": 1,
                "span_sha256": "2" * 64,
            }],
        })
    return {
        "schema_version": "0.1",
        "state": "VALIDATED",
        "valid": True,
        "candidate_sha256": lifecycle["candidate_sha256"],
        "knowledge_id": lifecycle["knowledge_id"],
        "source_ids": lifecycle["source_ids"],
        "judge_id": "test.faithfulness.v1",
        "unit_results": results,
        "errors": [],
    }


def _governed_package(root: Path):
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
    files = build_skill_package(
        _candidate(source_id),
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
    evidence = {
        "validator_id": "knowledge-forge.validate.v1",
        "package_receipt_sha256": lifecycle["package_receipt_sha256"],
        "validation_report": validation_report,
        "validation_report_sha256": evidence_sha256(
            validation_report
        ),
        "faithfulness_report": faithfulness,
        "faithfulness_report_sha256": evidence_sha256(
            faithfulness
        ),
    }
    lifecycle = transition_lifecycle(
        lifecycle,
        VALIDATED,
        occurred_at="2026-09-24T19:02:00Z",
        actor="validator:test",
        evidence=evidence,
    )
    lifecycle = transition_lifecycle(
        lifecycle,
        PROMOTED,
        occurred_at="2026-09-24T19:03:00Z",
        actor="operator:test",
        evidence={"decision_ref": "decision:test:p4c"},
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

    return skill_root, registry, manifest, lifecycle


class TrustWallTests(unittest.TestCase):
    def test_exact_promoted_package_loads_selected_unit_as_untrusted_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(
                Path(tmp)
            )

            loaded = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=["market_price", "journal_append"],
            )

        self.assertEqual(loaded["trust_class"], TRUST_CLASS)
        self.assertEqual(
            loaded["instruction_authority"],
            "NONE",
        )
        self.assertEqual(loaded["execution_authority"], "NONE")
        self.assertEqual(loaded["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(
            loaded["effective_tools"],
            ["journal_append", "market_price"],
        )
        self.assertEqual(len(loaded["reasoning_context"]), 1)
        context = loaded["reasoning_context"][0]
        self.assertEqual(
            context["trust_class"],
            "UNTRUSTED_KNOWLEDGE_DATA",
        )
        self.assertEqual(context["instruction_authority"], "NONE")
        self.assertEqual(context["execution_authority"], "NONE")
        self.assertFalse(context["executable"])
        self.assertIn("exactly 7 members", context["content"])
        self.assertNotIn(
            "Deployments happen",
            context["content"],
        )
        self.assertEqual(
            loaded["load_evidence"]["reasoning_unit_count"],
            1,
        )
        self.assertGreater(
            loaded["load_evidence"]["verified_artifact_count"],
            loaded["load_evidence"]["reasoning_unit_count"],
        )

    def test_tampered_unit_refuses_before_reasoning_context_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(
                Path(tmp)
            )
            unit_id = unit_id_for(
                "handbook:governance",
                "governance.approval_quorum",
            )
            (skill_root / f"units/{unit_id}.md").write_text(
                "tampered\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                TrustWallError,
                "package validation failed",
            ):
                load_promoted_skill_context(
                    skill_root,
                    registry,
                    skill_id=manifest["skill_id"],
                    question="approval quorum",
                    runtime_tools=["market_price"],
                )

    def test_suspended_current_registry_truth_refuses_old_promoted_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, lifecycle = _governed_package(
                Path(tmp)
            )
            suspended = transition_lifecycle(
                lifecycle,
                SUSPENDED,
                occurred_at="2026-09-24T19:05:00Z",
                actor="operator:test",
                evidence={
                    "incident_ref": "incident:p4c",
                    "reason": "test stop",
                },
            )
            registry = fold_in_current_incident_lifecycle(
                registry,
                suspended,
                updated_at="2026-09-24T19:05:00Z",
            )

            with self.assertRaisesRegex(
                TrustWallError,
                "current skill is not loadable",
            ):
                load_promoted_skill_context(
                    skill_root,
                    registry,
                    skill_id=manifest["skill_id"],
                    question="approval quorum",
                    runtime_tools=["market_price"],
                )

    def test_registry_receipt_mismatch_refuses_even_when_package_is_self_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(
                Path(tmp)
            )
            bad = deepcopy(registry)
            entry = bad["skills"][manifest["skill_id"]]["versions"]["1"]
            entry["package_receipt_sha256"] = (
                "sha256:" + ("0" * 64)
            )

            with self.assertRaisesRegex(
                TrustWallError,
                "registry is invalid|package receipt",
            ):
                load_promoted_skill_context(
                    skill_root,
                    bad,
                    skill_id=manifest["skill_id"],
                    question="approval quorum",
                    runtime_tools=["market_price"],
                )

    def test_skill_tool_request_can_narrow_but_never_widen_runtime_tools(self):
        policy = apply_skill_tool_constraints(
            ["market_price", "journal_append"],
            ["market_price", "wallet_send"],
        )

        self.assertEqual(policy["effective_tools"], ["market_price"])
        self.assertEqual(policy["denied_widening"], ["wallet_send"])
        self.assertFalse(policy["widening_possible"])
        self.assertEqual(
            policy["rule"],
            "INTERSECTION_ONLY_NEVER_WIDEN",
        )

    def test_absent_skill_tool_constraint_preserves_runtime_ceiling(self):
        policy = apply_skill_tool_constraints(
            ["market_price", "journal_append"],
            None,
        )

        self.assertEqual(
            policy["effective_tools"],
            ["journal_append", "market_price"],
        )
        self.assertEqual(policy["denied_widening"], [])

    def test_skill_body_tool_words_do_not_change_tool_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(
                Path(tmp)
            )
            loaded = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What is the deployment window?",
                runtime_tools=["market_price"],
            )

        self.assertIn(
            "wallet_send",
            loaded["reasoning_context"][0]["content"],
        )
        self.assertEqual(
            loaded["effective_tools"],
            ["market_price"],
        )
        self.assertEqual(
            loaded["tool_policy"]["denied_widening"],
            [],
        )

    def test_explicit_widening_request_from_skill_is_denied_at_load_wall(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(
                Path(tmp)
            )
            loaded = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What is the deployment window?",
                runtime_tools=["market_price"],
                skill_requested_tools=[
                    "market_price",
                    "wallet_send",
                ],
            )

        self.assertEqual(
            loaded["effective_tools"],
            ["market_price"],
        )
        self.assertEqual(
            loaded["tool_policy"]["denied_widening"],
            ["wallet_send"],
        )

    @skip_unless_symlinks
    def test_symlink_artifact_refuses_before_package_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_root, registry, manifest, _ = _governed_package(root)
            target = root / "outside.txt"
            target.write_text("outside", encoding="utf-8")
            (skill_root / "extra-link").symlink_to(target)

            with self.assertRaisesRegex(
                TrustWallError,
                "must not contain symlinks",
            ):
                load_promoted_skill_context(
                    skill_root,
                    registry,
                    skill_id=manifest["skill_id"],
                    question="approval quorum",
                    runtime_tools=[],
                )


if __name__ == "__main__":
    unittest.main()
