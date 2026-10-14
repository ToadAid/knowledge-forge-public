from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from knowledge_forge.staleness import (
    CURRENT,
    NOT_YET_VALID,
    STALE,
    UNEVALUABLE,
    StalenessError,
    evaluate_claim_index_staleness,
    evaluate_claim_validity,
)
from tests.test_registry import promoted_fixture


def claim(*, validity=None):
    return {
        "claim_key": "protocol.fee",
        "subject": "protocol.fee",
        "predicate": "equals",
        "value": 5,
        "qualifiers": {},
        "scope": {"chain": "base"},
        "validity": validity or {},
        "executable": False,
    }


class StalenessTests(unittest.TestCase):
    def test_expired_claim_is_stale_but_exact_expiry_is_current(self):
        value = claim(
            validity={"valid_until": "2026-09-24T12:00:00Z"},
        )

        at_boundary = evaluate_claim_validity(
            value,
            as_of="2026-09-24T12:00:00Z",
        )
        after_boundary = evaluate_claim_validity(
            value,
            as_of="2026-09-24T12:00:00.000001Z",
        )

        self.assertEqual(at_boundary["condition"], CURRENT)
        self.assertTrue(at_boundary["temporally_applicable"])
        self.assertEqual(after_boundary["condition"], STALE)
        self.assertFalse(after_boundary["temporally_applicable"])

    def test_future_claim_is_not_yet_valid(self):
        result = evaluate_claim_validity(
            claim(validity={"valid_from": "2026-10-01T00:00:00Z"}),
            as_of="2026-09-24T12:00:00Z",
        )
        self.assertEqual(result["condition"], NOT_YET_VALID)
        self.assertFalse(result["temporally_applicable"])

    def test_unbounded_claim_is_current_and_preserves_as_of_source(self):
        result = evaluate_claim_validity(
            claim(validity={"as_of_source": "source-published-at"}),
            as_of="2026-09-24T12:00:00Z",
        )
        self.assertEqual(result["condition"], CURRENT)
        self.assertTrue(result["temporally_applicable"])
        self.assertEqual(result["as_of_source"], "source-published-at")
        self.assertEqual(result["reason"], "no temporal validity bound")

    def test_malformed_claim_boundary_is_unevaluable_not_guessed(self):
        result = evaluate_claim_validity(
            claim(validity={"valid_until": "sometime-next-week"}),
            as_of="2026-09-24T12:00:00Z",
        )
        self.assertEqual(result["condition"], UNEVALUABLE)
        self.assertFalse(result["temporally_applicable"])
        self.assertIn("validity.valid_until", result["reason"])

    def test_reversed_validity_window_is_unevaluable(self):
        result = evaluate_claim_validity(
            claim(
                validity={
                    "valid_from": "2026-10-02T00:00:00Z",
                    "valid_until": "2026-10-01T00:00:00Z",
                }
            ),
            as_of="2026-09-24T12:00:00Z",
        )
        self.assertEqual(result["condition"], UNEVALUABLE)
        self.assertIn("after validity.valid_until", result["reason"])

    def test_timezone_offsets_compare_in_utc(self):
        result = evaluate_claim_validity(
            claim(validity={"valid_until": "2026-09-24T08:00:00-04:00"}),
            as_of="2026-09-24T12:00:01Z",
        )
        self.assertEqual(result["condition"], STALE)
        self.assertEqual(result["valid_until"], "2026-09-24T12:00:00Z")

    def test_invalid_as_of_is_refused(self):
        with self.assertRaisesRegex(StalenessError, "timezone-aware"):
            evaluate_claim_validity(
                claim(),
                as_of="2026-09-24T12:00:00",
            )

    def test_package_report_binds_exact_claim_index_and_stale_unit(self):
        _, _, claim_index, _ = promoted_fixture(
            validity={"valid_until": "2026-09-24T12:00:00Z"},
        )

        report = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:01Z",
        )

        self.assertEqual(report["condition"], STALE)
        self.assertFalse(report["temporally_applicable"])
        self.assertEqual(
            report["stale_unit_ids"],
            [claim_index["claims"][0]["unit_id"]],
        )
        self.assertEqual(report["not_yet_valid_unit_ids"], [])
        self.assertEqual(report["unevaluable_unit_ids"], [])
        self.assertTrue(report["claim_index_sha256"].startswith("sha256:"))

    def test_package_condition_is_unevaluable_when_no_claim_is_stale_but_bound_is_bad(self):
        _, _, claim_index, _ = promoted_fixture(
            validity={"valid_until": "not-a-time"},
        )

        report = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:01Z",
        )

        self.assertEqual(report["condition"], UNEVALUABLE)
        self.assertFalse(report["temporally_applicable"])
        self.assertEqual(report["stale_unit_ids"], [])
        self.assertEqual(
            report["unevaluable_unit_ids"],
            [claim_index["claims"][0]["unit_id"]],
        )

    def test_package_report_is_deterministic_for_same_index_and_as_of(self):
        _, _, claim_index, _ = promoted_fixture(
            validity={
                "valid_from": "2026-09-01T00:00:00Z",
                "valid_until": "2026-10-01T00:00:00Z",
            },
        )

        first = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:00-04:00",
        )
        second = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T16:00:00Z",
        )

        self.assertEqual(first, second)
        self.assertEqual(first["condition"], CURRENT)
        self.assertEqual(first["as_of"], "2026-09-24T16:00:00Z")

    def test_temporal_report_never_claims_generic_usability(self):
        _, _, claim_index, _ = promoted_fixture(
            validity={"valid_until": "2026-10-01T00:00:00Z"},
        )

        report = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:00Z",
        )

        self.assertEqual(report["condition"], CURRENT)
        self.assertTrue(report["temporally_applicable"])
        self.assertNotIn("usable", report)
        self.assertNotIn("usable", report["unit_results"][0])

    def test_computed_stale_does_not_mutate_promoted_lifecycle(self):
        _, lifecycle, claim_index, _ = promoted_fixture(
            validity={"valid_until": "2026-09-23T00:00:00Z"},
        )
        before = deepcopy(lifecycle)

        report = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:00Z",
        )

        self.assertEqual(report["condition"], STALE)
        self.assertEqual(lifecycle, before)
        self.assertEqual(lifecycle["state"], "PROMOTED")

    def test_caller_time_is_explicit_not_wall_clock(self):
        _, _, claim_index, _ = promoted_fixture(
            validity={"valid_until": "2026-09-24T13:00:00Z"},
        )

        earlier = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T12:00:00Z",
        )
        later = evaluate_claim_index_staleness(
            claim_index,
            as_of="2026-09-24T14:00:00Z",
        )

        self.assertEqual(earlier["condition"], CURRENT)
        self.assertEqual(later["condition"], STALE)


if __name__ == "__main__":
    unittest.main()
