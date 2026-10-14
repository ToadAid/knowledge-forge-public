"""Deterministic temporal applicability for structured knowledge claims.

P2E-C does not read the wall clock and does not mutate lifecycle history.
The caller (normally the Time/live-evidence layer) supplies an explicit
timezone-aware ``as_of`` value. Knowledge Forge computes temporal condition
from the structured validity metadata already bound into claim-index.json.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .claim_index import (
    ClaimIndexError,
    claim_index_artifact_sha256,
    validate_claim_index,
)
from .claims import ClaimError, normalize_claim

STALENESS_SCHEMA_VERSION = "0.1"

CURRENT = "CURRENT"
NOT_YET_VALID = "NOT_YET_VALID"
STALE = "STALE"
UNEVALUABLE = "UNEVALUABLE"
CONDITIONS = frozenset({CURRENT, NOT_YET_VALID, STALE, UNEVALUABLE})


class StalenessError(ValueError):
    """Raised when the caller's temporal input or package is invalid."""


def _parse_rfc3339(value: Any, name: str) -> tuple[datetime, str]:
    if not isinstance(value, str) or not value.strip():
        raise StalenessError(f"{name} must be a non-empty RFC3339 timestamp")
    raw = value.strip()
    if "T" not in raw:
        raise StalenessError(f"{name} must be a timezone-aware RFC3339 timestamp")
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise StalenessError(
            f"{name} must be a timezone-aware RFC3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise StalenessError(f"{name} must be timezone-aware")
    utc = parsed.astimezone(timezone.utc)
    canonical = utc.isoformat().replace("+00:00", "Z")
    return utc, canonical


def _claim_boundary(
    validity: Mapping[str, Any],
    key: str,
) -> tuple[datetime | None, str | None, str | None]:
    value = validity.get(key)
    if value is None:
        return None, None, None
    try:
        parsed, canonical = _parse_rfc3339(value, f"validity.{key}")
    except StalenessError as exc:
        return None, value if isinstance(value, str) else None, str(exc)
    return parsed, canonical, None


def evaluate_claim_validity(
    claim: Mapping[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Compute one claim's temporal condition without changing the claim."""

    try:
        normalized = normalize_claim(claim)
    except ClaimError as exc:
        raise StalenessError(f"claim invalid: {exc}") from exc

    as_of_dt, as_of_canonical = _parse_rfc3339(as_of, "as_of")
    validity = normalized["validity"]

    valid_from_dt, valid_from, from_error = _claim_boundary(
        validity,
        "valid_from",
    )
    valid_until_dt, valid_until, until_error = _claim_boundary(
        validity,
        "valid_until",
    )
    as_of_source = validity.get("as_of_source")

    if from_error or until_error:
        errors = [item for item in (from_error, until_error) if item]
        return {
            "schema_version": STALENESS_SCHEMA_VERSION,
            "condition": UNEVALUABLE,
            "temporally_applicable": False,
            "as_of": as_of_canonical,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "as_of_source": as_of_source,
            "reason": "; ".join(errors),
        }

    if (
        valid_from_dt is not None
        and valid_until_dt is not None
        and valid_from_dt > valid_until_dt
    ):
        return {
            "schema_version": STALENESS_SCHEMA_VERSION,
            "condition": UNEVALUABLE,
            "temporally_applicable": False,
            "as_of": as_of_canonical,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "as_of_source": as_of_source,
            "reason": "validity.valid_from is after validity.valid_until",
        }

    if valid_from_dt is not None and as_of_dt < valid_from_dt:
        condition = NOT_YET_VALID
        reason = "as_of precedes validity.valid_from"
    elif valid_until_dt is not None and as_of_dt > valid_until_dt:
        condition = STALE
        reason = "as_of is after validity.valid_until"
    else:
        condition = CURRENT
        reason = (
            "no temporal validity bound"
            if valid_from_dt is None and valid_until_dt is None
            else "as_of is within the validity window"
        )

    return {
        "schema_version": STALENESS_SCHEMA_VERSION,
        "condition": condition,
        "temporally_applicable": condition == CURRENT,
        "as_of": as_of_canonical,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "as_of_source": as_of_source,
        "reason": reason,
    }


def evaluate_claim_index_staleness(
    claim_index: Mapping[str, Any],
    *,
    as_of: str,
) -> dict[str, Any]:
    """Compute package-level temporal condition from exact receipt-bound claim truth.

    ``temporally_applicable`` answers only the Time-layer question. It does not
    grant lifecycle/registry usability: a SUSPENDED, DISPUTED, REVOKED, or
    otherwise unavailable package remains unusable regardless of freshness.

    Conservative package rule:
    - any stale claim => STALE
    - otherwise any unevaluable claim => UNEVALUABLE
    - otherwise any not-yet-valid claim => NOT_YET_VALID
    - otherwise CURRENT

    This report is computed data. It never rewrites lifecycle state.
    """

    try:
        validate_claim_index(claim_index)
        index_digest = claim_index_artifact_sha256(claim_index)
    except ClaimIndexError as exc:
        raise StalenessError(f"claim index invalid: {exc}") from exc

    _, as_of_canonical = _parse_rfc3339(as_of, "as_of")

    unit_results: list[dict[str, Any]] = []
    stale_unit_ids: list[str] = []
    not_yet_valid_unit_ids: list[str] = []
    unevaluable_unit_ids: list[str] = []

    claims = claim_index["claims"]
    for item in claims:
        result = evaluate_claim_validity(
            item["claim"],
            as_of=as_of_canonical,
        )
        unit_result = {
            "claim_ref": item["claim_ref"],
            "unit_id": item["unit_id"],
            "claim_key": item["claim_key"],
            **result,
        }
        unit_results.append(unit_result)

        if result["condition"] == STALE:
            stale_unit_ids.append(item["unit_id"])
        elif result["condition"] == NOT_YET_VALID:
            not_yet_valid_unit_ids.append(item["unit_id"])
        elif result["condition"] == UNEVALUABLE:
            unevaluable_unit_ids.append(item["unit_id"])

    unit_results.sort(key=lambda item: item["claim_ref"])
    stale_unit_ids.sort()
    not_yet_valid_unit_ids.sort()
    unevaluable_unit_ids.sort()

    if stale_unit_ids:
        condition = STALE
    elif unevaluable_unit_ids:
        condition = UNEVALUABLE
    elif not_yet_valid_unit_ids:
        condition = NOT_YET_VALID
    else:
        condition = CURRENT

    return {
        "schema_version": STALENESS_SCHEMA_VERSION,
        "skill_id": claim_index["skill_id"],
        "skill_version": claim_index["skill_version"],
        "knowledge_id": claim_index["knowledge_id"],
        "claim_index_sha256": index_digest,
        "as_of": as_of_canonical,
        "condition": condition,
        "temporally_applicable": condition == CURRENT,
        "stale_unit_ids": stale_unit_ids,
        "not_yet_valid_unit_ids": not_yet_valid_unit_ids,
        "unevaluable_unit_ids": unevaluable_unit_ids,
        "unit_results": unit_results,
    }
