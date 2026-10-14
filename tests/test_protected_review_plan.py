"""P3B-P2C-P1: all plan outputs are tainted; none carry promotion authority."""
from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.approval import (
    ApprovalContractError,
    build_approval_evidence,
    build_approval_request,
    build_current_candidate_binding,
)
from knowledge_forge.protected_review_plan import (
    KIND,
    ProtectedReviewPlanError,
    build_untrusted_operator_review_plan,
    validate_untrusted_operator_review_plan,
)

T0 = "2026-10-14T15:00:00Z"
T1 = "2026-10-14T15:05:00Z"
T2 = "2026-10-14T15:10:00Z"


def sha(ch):
    return "sha256:" + ch * 64


def fixture(*, expiry=T2):
    evidence = build_approval_evidence(
        request_id="review.synthetic.2222",
        target_skill_id="knowledge-forge:roof.quote@" + sha("a"),
        target_skill_version=1,
        package_receipt_sha256=sha("b"),
        validated_lifecycle_head_sha256=sha("c"),
        items=[{"label": "manifest", "sha256": sha("d")}],
    )
    request = build_approval_request(
        request_id=evidence["request_id"],
        requesting_agent="Claude Code",
        requesting_principal="untrusted-agent-session",
        target_skill_id=evidence["target_skill_id"],
        target_skill_version=evidence["target_skill_version"],
        package_receipt_sha256=evidence["package_receipt_sha256"],
        validated_lifecycle_head_sha256=evidence["validated_lifecycle_head_sha256"],
        requested_at=T0, expires_at=expiry,
        evidence_digest=evidence["evidence_digest"],
    )
    binding = build_current_candidate_binding(request, observed_at=T1)
    return request, evidence, binding


def plan(*, expiry=T2):
    req, ev, binding = fixture(expiry=expiry)
    return build_untrusted_operator_review_plan(
        req, ev, binding,
        expected_registry_head_sha256=sha("e"), observed_at=T1,
    )


class ProtectedReviewPlanTests(unittest.TestCase):
    def test_all_outputs_zero_authority(self):
        value = plan()
        self.assertEqual(value["kind"], KIND)
        self.assertEqual(value["execution_authority"], "NONE")
        self.assertEqual(value["tool_grants"], [])
        for key, val in value.items():
            if key.endswith("_verified") or key in ("authorization_issued", "operator_invoked", "promotion_authorized", "registry_mutated", "protected_ledger_write_performed"):
                self.assertIs(val, False, key)
        self.assertEqual(validate_untrusted_operator_review_plan(value), value)

    def test_exact_candidate_and_registry_comparison_targets(self):
        value = plan()
        self.assertEqual(value["package_receipt_sha256"], sha("b"))
        self.assertEqual(value["validated_lifecycle_head_sha256"], sha("c"))
        self.assertEqual(value["expected_registry_head_sha256"], sha("e"))
        self.assertEqual(value["candidate_binding_source"], "CALLER_SUPPLIED_UNTRUSTED")
        self.assertEqual(value["registry_head_source"], "CALLER_SUPPLIED_UNTRUSTED")

    def test_tampered_evidence_refused(self):
        req, ev, b = fixture()
        ev["items"][0]["sha256"] = sha("f")
        with self.assertRaises(ApprovalContractError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at=T1)

    def test_tampered_request_refused(self):
        req, ev, b = fixture()
        req["target_skill_version"] = 2
        with self.assertRaises(ApprovalContractError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at=T1)

    def test_stale_candidate_refused(self):
        req, ev, b = fixture()
        b["validated_lifecycle_head_sha256"] = sha("f")
        with self.assertRaises(ApprovalContractError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at=T1)

    def test_registry_head_syntax_refusal(self):
        req, ev, b = fixture()
        for bad in ("e"*64, "sha256:" + "E"*64, "sha256:" + "g"*64, None):
            with self.subTest(bad=bad), self.assertRaises(ProtectedReviewPlanError):
                build_untrusted_operator_review_plan(req, ev, b,
                    expected_registry_head_sha256=bad, observed_at=T1)

    def test_expired_observation_refused(self):
        req, ev, b = fixture()
        with self.assertRaises(ProtectedReviewPlanError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at=T2)

    def test_earlier_than_request_refused(self):
        req, ev, b = fixture()
        with self.assertRaises(ProtectedReviewPlanError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at="2026-10-14T14:59:59Z")

    def test_overlong_request_refused(self):
        req, ev, b = fixture(expiry="2026-10-14T15:16:00Z")
        with self.assertRaises(ProtectedReviewPlanError):
            build_untrusted_operator_review_plan(req, ev, b,
                expected_registry_head_sha256=sha("e"), observed_at=T1)

    def test_bad_time_refused(self):
        req, ev, b = fixture()
        for bad in ("2026-10-14T15:05Z", "2026-10-14T15:05:00+00:00", "bogus", None):
            with self.subTest(bad=bad), self.assertRaises(ProtectedReviewPlanError):
                build_untrusted_operator_review_plan(req, ev, b,
                    expected_registry_head_sha256=sha("e"), observed_at=bad)

    def test_caller_boolean_verification_refused(self):
        for flag in (
            "human_authentication_verified", "authorization_issued",
            "independent_host_attestation_verified", "operator_invoked", "promotion_authorized",
            "independent_registry_recheck_verified", "protected_ledger_write_performed",
        ):
            obj = plan()
            obj[flag] = True
            with self.subTest(flag=flag), self.assertRaises(ProtectedReviewPlanError):
                validate_untrusted_operator_review_plan(obj)

    def test_privilege_fields_refused(self):
        for key, val in (("tool_grants", ["operator"]), ("execution_authority", "HOST")):
            obj = plan()
            obj[key] = val
            with self.subTest(key=key), self.assertRaises(ProtectedReviewPlanError):
                validate_untrusted_operator_review_plan(obj)

    def test_caller_source_cannot_be_host_trusted(self):
        for key in ("candidate_binding_source", "registry_head_source"):
            obj = plan()
            obj[key] = "ROOT_ATTESTED"
            with self.subTest(key=key), self.assertRaises(ProtectedReviewPlanError):
                validate_untrusted_operator_review_plan(obj)

    def test_injected_authority_field_refused(self):
        obj = plan()
        obj["verified_by_host"] = True
        with self.assertRaises(ProtectedReviewPlanError):
            validate_untrusted_operator_review_plan(obj)

    def test_removed_required_field_refused(self):
        obj = plan()
        del obj["request_sha256"]
        with self.assertRaises(ProtectedReviewPlanError):
            validate_untrusted_operator_review_plan(obj)

    def test_invalid_observation_and_expiry_refused(self):
        obj = plan()
        obj["observed_at"] = obj["expires_at"]
        with self.assertRaises(ProtectedReviewPlanError):
            validate_untrusted_operator_review_plan(obj)

    def test_unchanged_input_and_deterministic_projection(self):
        req, ev, b = fixture()
        original = deepcopy((req, ev, b))
        first = build_untrusted_operator_review_plan(req, ev, b,
            expected_registry_head_sha256=sha("e"), observed_at=T1)
        second = build_untrusted_operator_review_plan(req, ev, b,
            expected_registry_head_sha256=sha("e"), observed_at=T1)
        self.assertEqual(first, second)
        self.assertEqual((req, ev, b), original)

    def test_malformed_version_refused(self):
        obj = plan()
        obj["target_skill_version"] = True
        with self.assertRaises(ProtectedReviewPlanError):
            validate_untrusted_operator_review_plan(obj)


if __name__ == "__main__":
    unittest.main()
