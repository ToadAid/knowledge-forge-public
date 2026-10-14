from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.memory_boundary import (
    AUTOBIOGRAPHICAL_MEMORY,
    KNOWLEDGE_REFERENCE_CLASS,
    LIVED_EVIDENCE,
    USER_STATED_MEMORY,
    MemoryBoundaryError,
    build_knowledge_reference,
    project_skill_origin,
    validate_knowledge_reference,
)
from knowledge_forge.trust_wall import load_promoted_skill_context
from tests.test_trust_wall import _governed_package


def _loaded(tmp: str, *, question: str = "What approval quorum is required?"):
    skill_root, registry, manifest, _ = _governed_package(Path(tmp))
    result = load_promoted_skill_context(
        skill_root,
        registry,
        skill_id=manifest["skill_id"],
        question=question,
        runtime_tools=["market_price", "journal_append"],
    )
    return result


class MemoryBoundaryTests(unittest.TestCase):
    def test_skill_load_projects_only_provenance_knowledge_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            reference = build_knowledge_reference(loaded)

        self.assertTrue(validate_knowledge_reference(reference)["valid"])
        self.assertEqual(
            reference["storage_class"],
            "KNOWLEDGE_REFERENCE",
        )
        self.assertEqual(
            reference["origin"],
            "GOVERNED_SKILL_KNOWLEDGE",
        )
        self.assertEqual(
            reference["memory_mutation_authority"],
            "NONE",
        )
        self.assertEqual(reference["execution_authority"], "NONE")
        self.assertFalse(reference["autobiographical_evidence"])
        self.assertFalse(reference["user_stated_evidence"])
        self.assertFalse(reference["lived_evidence"])
        self.assertNotIn("content", reference)
        self.assertNotIn("reasoning_context", reference)
        for unit in reference["loaded_units"]:
            self.assertNotIn("content", unit)

    def test_skill_origin_cannot_become_autobiographical_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "cannot become AUTOBIOGRAPHICAL_MEMORY",
            ):
                project_skill_origin(
                    loaded,
                    target_storage_class=AUTOBIOGRAPHICAL_MEMORY,
                )

    def test_skill_origin_cannot_become_user_stated_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "cannot become USER_STATED_MEMORY",
            ):
                project_skill_origin(
                    loaded,
                    target_storage_class=USER_STATED_MEMORY,
                )

    def test_skill_origin_cannot_become_lived_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "cannot become LIVED_EVIDENCE",
            ):
                project_skill_origin(
                    loaded,
                    target_storage_class=LIVED_EVIDENCE,
                )

    def test_only_safe_cross_boundary_target_is_knowledge_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            projected = project_skill_origin(
                loaded,
                target_storage_class=KNOWLEDGE_REFERENCE_CLASS,
            )

        self.assertTrue(validate_knowledge_reference(projected)["valid"])

        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "may only target KNOWLEDGE_REFERENCE",
            ):
                project_skill_origin(
                    loaded,
                    target_storage_class="LONG_TERM_MEMORY",
                )

    def test_forged_trust_class_cannot_cross_memory_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            forged = deepcopy(loaded)
            forged["trust_class"] = "LIVED_EVIDENCE"

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "requires untrusted knowledge data",
            ):
                build_knowledge_reference(forged)

    def test_forged_executable_context_cannot_cross_memory_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            forged = deepcopy(loaded)
            forged["reasoning_context"][0]["executable"] = True

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "must remain non-executable",
            ):
                build_knowledge_reference(forged)

    def test_forged_execution_authority_cannot_cross_memory_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            forged = deepcopy(loaded)
            forged["reasoning_context"][0]["execution_authority"] = "WRITE"

            with self.assertRaisesRegex(
                MemoryBoundaryError,
                "execution authority mismatch",
            ):
                build_knowledge_reference(forged)

    def test_tampered_reference_cannot_claim_memory_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            reference = build_knowledge_reference(loaded)

        for field in (
            "autobiographical_evidence",
            "user_stated_evidence",
            "lived_evidence",
        ):
            forged = deepcopy(reference)
            forged[field] = True
            report = validate_knowledge_reference(forged)
            self.assertFalse(report["valid"])

        forged = deepcopy(reference)
        forged["memory_mutation_authority"] = "WRITE"
        self.assertFalse(
            validate_knowledge_reference(forged)["valid"]
        )

    def test_knowledge_reference_is_deterministic_for_same_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded(tmp)
            first = build_knowledge_reference(loaded)
            second = build_knowledge_reference(deepcopy(loaded))

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
