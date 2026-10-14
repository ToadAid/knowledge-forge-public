"""Fail-closed boundary between protected canon and ordinary skill knowledge.

Knowledge Forge may compile/version ordinary external knowledge. Constitution,
doctrine, witness law, authority policy, and other canon are governing law and
must not enter the supersedable skill pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

ORDINARY_KNOWLEDGE = "ORDINARY_KNOWLEDGE"
CANON = "CANON"
CONSTITUTION = "CONSTITUTION"
DOCTRINE = "DOCTRINE"
WITNESS_LAW = "WITNESS_LAW"
AUTHORITY_POLICY = "AUTHORITY_POLICY"

PROTECTED_CANON_ROLES = frozenset({
    CANON,
    CONSTITUTION,
    DOCTRINE,
    WITNESS_LAW,
    AUTHORITY_POLICY,
})
VALID_SOURCE_ROLES = frozenset({ORDINARY_KNOWLEDGE, *PROTECTED_CANON_ROLES})

_RESERVED_BASENAMES = {
    "canon.md": CANON,
    "canon.txt": CANON,
    "constitution.md": CONSTITUTION,
    "constitution.txt": CONSTITUTION,
    "doctrine.md": DOCTRINE,
    "doctrine.txt": DOCTRINE,
    "witness.md": WITNESS_LAW,
    "witness.txt": WITNESS_LAW,
    "witness-law.md": WITNESS_LAW,
    "witness-law.txt": WITNESS_LAW,
    "witness_law.md": WITNESS_LAW,
    "witness_law.txt": WITNESS_LAW,
    "authority-policy.md": AUTHORITY_POLICY,
    "authority-policy.txt": AUTHORITY_POLICY,
    "authority_policy.md": AUTHORITY_POLICY,
    "authority_policy.txt": AUTHORITY_POLICY,
}

_RESERVED_PATH_SEGMENTS = {
    "canon": CANON,
    "constitution": CONSTITUTION,
    "doctrine": DOCTRINE,
    "witness-law": WITNESS_LAW,
    "witness_law": WITNESS_LAW,
    "authority-policy": AUTHORITY_POLICY,
    "authority_policy": AUTHORITY_POLICY,
}


class CanonicalBoundaryError(ValueError):
    """Raised when protected canon attempts to enter the skill pipeline."""


def normalize_source_role(value: Any) -> str:
    if value is None:
        return ORDINARY_KNOWLEDGE
    if not isinstance(value, str) or value not in VALID_SOURCE_ROLES:
        raise CanonicalBoundaryError(
            "source_role must be one of " + ", ".join(sorted(VALID_SOURCE_ROLES))
        )
    return value


def classify_source_role(
    source: str | Path,
    *,
    declared_role: str | None = None,
) -> str:
    """Classify a local source without using model/content heuristics.

    Explicit protected roles always win. For the ordinary/default role, known
    canon filenames and dedicated canon path segments are protected
    deterministically so the repository's governing files cannot be compiled by
    simply passing them through the ordinary ingest path.
    """

    declared = normalize_source_role(declared_role)
    if declared in PROTECTED_CANON_ROLES:
        return declared

    path = Path(source)
    basename_role = _RESERVED_BASENAMES.get(path.name.lower())
    if basename_role is not None:
        return basename_role

    for part in path.parts[:-1]:
        role = _RESERVED_PATH_SEGMENTS.get(part.lower())
        if role is not None:
            return role

    return ORDINARY_KNOWLEDGE


def require_ordinary_skill_role(value: Any) -> str:
    """Require explicit ordinary-knowledge classification at compile boundary."""

    if value is None:
        raise CanonicalBoundaryError(
            "normalized source requires explicit source_role; CANON != SKILL"
        )
    role = normalize_source_role(value)
    if role != ORDINARY_KNOWLEDGE:
        raise CanonicalBoundaryError(
            f"CANON != SKILL: protected source_role {role} cannot enter ordinary skill compilation/versioning"
        )
    return role
