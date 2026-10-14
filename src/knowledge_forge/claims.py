"""Stable structured claim identity for compiled knowledge units."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Mapping

from .provenance import ProvenanceError, normalize_knowledge_id

_STABLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")
_VALIDITY_KEYS = frozenset({"valid_from", "valid_until", "as_of_source"})


class ClaimError(ValueError):
    """Raised when a structured claim is incomplete or unstable."""


def _stable_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _STABLE_ID_RE.fullmatch(value):
        raise ClaimError(f"{name} must be a stable lowercase identifier")
    return value


def normalize_claim_key(value: Any) -> str:
    return _stable_id(value, "claim_key")


def _canonical_json(value: Any, name: str) -> Any:
    copied = deepcopy(value)
    try:
        json.dumps(copied, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ClaimError(f"{name} must be canonical JSON data") from exc
    return copied


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ClaimError(f"{name} must be a mapping")
    copied = _canonical_json(dict(value), name)
    if any(not isinstance(key, str) or not key for key in copied):
        raise ClaimError(f"{name} keys must be non-empty strings")
    return copied


def _validity(value: Any) -> dict[str, Any]:
    raw = _mapping(value, "validity")
    unknown = set(raw) - _VALIDITY_KEYS
    if unknown:
        raise ClaimError(f"validity contains unsupported keys: {', '.join(sorted(unknown))}")
    normalized: dict[str, Any] = {}
    for key in ("valid_from", "valid_until", "as_of_source"):
        if key not in raw:
            continue
        item = raw[key]
        if item is None:
            normalized[key] = None
        elif isinstance(item, str) and item.strip():
            normalized[key] = item.strip()
        else:
            raise ClaimError(f"validity.{key} must be a non-empty string or null")
    return normalized


def normalize_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(claim, Mapping):
        raise ClaimError("claim must be a mapping")
    required = {
        "claim_key", "subject", "predicate", "value",
        "qualifiers", "scope", "validity", "executable",
    }
    missing = sorted(required - set(claim))
    if missing:
        raise ClaimError(f"claim missing required fields: {', '.join(missing)}")
    unknown = sorted(set(claim) - required)
    if unknown:
        raise ClaimError(f"claim contains unsupported fields: {', '.join(unknown)}")
    if claim.get("executable") is not False:
        raise ClaimError("claim executable must be exactly false")
    return {
        "claim_key": normalize_claim_key(claim.get("claim_key")),
        "subject": _stable_id(claim.get("subject"), "claim subject"),
        "predicate": _stable_id(claim.get("predicate"), "claim predicate"),
        "value": _canonical_json(claim.get("value"), "claim value"),
        "qualifiers": _mapping(claim.get("qualifiers"), "claim qualifiers"),
        "scope": _mapping(claim.get("scope"), "claim scope"),
        "validity": _validity(claim.get("validity")),
        "executable": False,
    }


def unit_id_for(knowledge_id: str, claim_key: str) -> str:
    """Identity = knowledge subject + stable claim key, never prose/value."""
    try:
        knowledge = normalize_knowledge_id(knowledge_id)
    except ProvenanceError as exc:
        raise ClaimError(str(exc)) from exc
    key = normalize_claim_key(claim_key)
    payload = json.dumps(
        {"knowledge_id": knowledge, "claim_key": key},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return f"claim-{sha256(payload).hexdigest()}"
