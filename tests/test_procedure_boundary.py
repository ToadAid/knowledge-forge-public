from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.procedure_boundary import (
    DEPLOY,
    PUSH,
    SIGN,
    SPEND,
    WRITE,
    ProcedureBoundaryError,
    evaluate_procedure_capability_request,
    project_procedure_boundary,
)
from knowledge_forge.temporal_integration import load_current_skill_context
from knowledge_forge.trust_wall import load_promoted_skill_context
from tests.test_trust_wall import _governed_package


def _procedure_load(tmp: str, *, runtime_tools=None):
    skill_root, registry, manifest, _ = _governed_package(Path(tmp))
    loaded = load_promoted_skill_context(
        skill_root,
        registry,
        skill_id=manifest["skill_id"],
        question="What is the deployment window?",
        runtime_tools=runtime_tools or [],
    )
    return loaded, skill_root, registry, manifest


class ProcedureBoundaryTests(unittest.TestCase):
    def test_procedure_projects_as_descriptive_zero_authority_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(tmp)
            projection = project_procedure_boundary(loaded)

        self.assertEqual(projection["procedure_count"], 1)
        self.assertEqual(projection["execution_authority"], "NONE")
        self.assertEqual(projection["procedure_tool_grants"], [])
        self.assertEqual(projection["procedure_capability_grants"], [])
        item = projection["procedures"][0]
        self.assertEqual(item["kind"], "PROCEDURE")
        self.assertFalse(item["executable"])
        self.assertEqual(item["execution_authority"], "NONE")
        self.assertEqual(item["tool_grants"], [])
        self.assertEqual(item["capability_grants"], [])
        self.assertTrue(item["requires_external_authority_for_execution"])

    def test_tool_name_inside_procedure_body_is_inert_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(
                tmp,
                runtime_tools=["market_price"],
            )
            projection = project_procedure_boundary(loaded)

        body = projection["procedures"][0]["content"]
        self.assertIn("wallet_send", body)
        self.assertEqual(
            projection["runtime_effective_tools"],
            ["market_price"],
        )
        self.assertEqual(projection["procedure_tool_grants"], [])

    def test_sensitive_capabilities_are_all_denied_by_procedure_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(tmp)

            for capability in (SPEND, SIGN, DEPLOY, PUSH, WRITE):
                verdict = evaluate_procedure_capability_request(
                    loaded,
                    capability=capability,
                )
                self.assertFalse(verdict["authorized_by_procedure"])
                self.assertEqual(verdict["execution_authority"], "NONE")
                self.assertEqual(verdict["capability_grants"], [])
                self.assertEqual(
                    verdict["reason"],
                    "PROCEDURE_KNOWLEDGE_HAS_ZERO_EXECUTION_AUTHORITY",
                )

    def test_existing_runtime_sensitive_tools_are_not_reclassified_as_skill_grants(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(
                tmp,
                runtime_tools=[
                    "wallet_sign",
                    "git_push",
                    "file_write",
                    "deploy_contract",
                ],
            )
            projection = project_procedure_boundary(loaded)

        self.assertEqual(
            projection["runtime_effective_tools"],
            [
                "deploy_contract",
                "file_write",
                "git_push",
                "wallet_sign",
            ],
        )
        self.assertEqual(projection["procedure_tool_grants"], [])
        self.assertEqual(projection["procedure_capability_grants"], [])

    def test_forged_executable_procedure_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(tmp)
            forged = deepcopy(loaded)
            forged["reasoning_context"][0]["executable"] = True

            with self.assertRaisesRegex(
                ProcedureBoundaryError,
                "executable must remain exactly false",
            ):
                project_procedure_boundary(forged)

    def test_forged_instruction_authority_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(tmp)
            forged = deepcopy(loaded)
            forged["reasoning_context"][0][
                "instruction_authority"
            ] = "EXECUTE"

            with self.assertRaisesRegex(
                ProcedureBoundaryError,
                "instruction authority mismatch",
            ):
                project_procedure_boundary(forged)

    def test_forged_execution_authority_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded, _, _, _ = _procedure_load(tmp)
            forged = deepcopy(loaded)
            forged["execution_authority"] = "WRITE"

            with self.assertRaisesRegex(
                ProcedureBoundaryError,
                "zero execution authority",
            ):
                project_procedure_boundary(forged)

    def test_nonprocedure_unit_does_not_create_procedure_authority_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(Path(tmp))
            loaded = load_promoted_skill_context(
                skill_root,
                registry,
                skill_id=manifest["skill_id"],
                question="What approval quorum is required?",
                runtime_tools=[],
            )
            projection = project_procedure_boundary(loaded)

        self.assertEqual(projection["procedure_count"], 0)
        self.assertEqual(projection["procedures"], [])
        with self.assertRaisesRegex(
            ProcedureBoundaryError,
            "no PROCEDURE unit is loaded",
        ):
            evaluate_procedure_capability_request(
                loaded,
                capability=WRITE,
            )

    def test_temporally_current_procedure_still_has_zero_execution_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            skill_root, registry, manifest, _ = _governed_package(Path(tmp))
            loaded = load_current_skill_context(
                str(skill_root),
                registry,
                skill_id=manifest["skill_id"],
                question="What is the deployment window?",
                runtime_tools=["git_push"],
                world_as_of="2026-09-25T11:00:00Z",
            )
            projection = project_procedure_boundary(loaded)

        self.assertEqual(projection["procedure_count"], 1)
        self.assertEqual(projection["execution_authority"], "NONE")
        self.assertEqual(projection["procedure_tool_grants"], [])
        self.assertEqual(projection["procedure_capability_grants"], [])
        self.assertEqual(
            loaded["reasoning_context"][0]["temporal_condition"],
            "CURRENT",
        )


if __name__ == "__main__":
    unittest.main()
