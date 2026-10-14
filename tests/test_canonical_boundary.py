from __future__ import annotations

from pathlib import Path
import unittest

from knowledge_forge.canonical_boundary import (
    AUTHORITY_POLICY,
    CANON,
    CONSTITUTION,
    DOCTRINE,
    ORDINARY_KNOWLEDGE,
    WITNESS_LAW,
    CanonicalBoundaryError,
    classify_source_role,
    require_ordinary_skill_role,
)


class CanonicalBoundaryTests(unittest.TestCase):
    def test_ordinary_source_stays_ordinary(self) -> None:
        self.assertEqual(classify_source_role(Path("manuals/api.md")), ORDINARY_KNOWLEDGE)

    def test_reserved_canon_names_are_protected_case_insensitively(self) -> None:
        cases = {
            "DOCTRINE.md": DOCTRINE,
            "constitution.txt": CONSTITUTION,
            "Witness-Law.md": WITNESS_LAW,
            "authority_policy.txt": AUTHORITY_POLICY,
            "canon.md": CANON,
        }
        for name, role in cases.items():
            with self.subTest(name=name):
                self.assertEqual(classify_source_role(Path(name)), role)

    def test_dedicated_canon_directory_is_protected(self) -> None:
        self.assertEqual(
            classify_source_role(Path("canon/rules.md")),
            CANON,
        )

    def test_explicit_protected_role_overrides_generic_filename(self) -> None:
        self.assertEqual(
            classify_source_role(Path("notes.md"), declared_role=AUTHORITY_POLICY),
            AUTHORITY_POLICY,
        )

    def test_compile_boundary_requires_explicit_ordinary_role(self) -> None:
        with self.assertRaisesRegex(CanonicalBoundaryError, "explicit source_role"):
            require_ordinary_skill_role(None)
        with self.assertRaisesRegex(CanonicalBoundaryError, "CANON != SKILL"):
            require_ordinary_skill_role(DOCTRINE)
        self.assertEqual(require_ordinary_skill_role(ORDINARY_KNOWLEDGE), ORDINARY_KNOWLEDGE)


if __name__ == "__main__":
    unittest.main()
