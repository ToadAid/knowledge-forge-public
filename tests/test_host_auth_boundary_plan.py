"""P2C-P2: the proposed Linux host-auth checklist NEVER grants authority."""
from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.host_auth_boundary_plan import (
    EXPECTED_LAYOUT, HOST_FAMILY, KIND, PROOF_IDS, REFUSAL_FLAGS,
    HostAuthBoundaryPlanError, build_untrusted_host_auth_requirements,
    validate_untrusted_host_auth_requirements,
)


def sha(c: str) -> str:
    return "sha256:" + c * 64


def review():
    return {
        "schema_version": "0.1",
        "kind": "UNTRUSTED_OPERATOR_REVIEW_PLAN_NOT_AUTHORIZATION",
        "request_id": "review.synthetic.3001",
        "request_sha256": sha("a"),
        "target_skill_id": "knowledge-forge:roof.quote@" + sha("b"),
        "target_skill_version": 1,
        "package_receipt_sha256": sha("c"),
        "validated_lifecycle_head_sha256": sha("d"),
        "evidence_digest": sha("e"),
        "expected_registry_head_sha256": sha("f"),
        "observed_at": "2026-10-14T15:05:00Z",
        "expires_at": "2026-10-14T15:10:00Z",
        "candidate_binding_source": "CALLER_SUPPLIED_UNTRUSTED",
        "registry_head_source": "CALLER_SUPPLIED_UNTRUSTED",
        "execution_authority": "NONE",
        "tool_grants": [],
        "independent_host_attestation_verified": False,
        "independent_candidate_recheck_verified": False,
        "independent_registry_recheck_verified": False,
        "human_authentication_verified": False,
        "protected_ledger_write_performed": False,
        "authorization_issued": False,
        "operator_invoked": False,
        "promotion_authorized": False,
        "registry_mutated": False,
    }


def build(plan=None, when="2026-10-14T15:06:00Z", host="linux"):
    return build_untrusted_host_auth_requirements(
        review() if plan is None else plan, observed_at=when, host_family=host,
    )


class HostAuthBoundaryPlanTests(unittest.TestCase):
    def test_zero_authority_fields_and_all_flags_false(self):
        obj = build()
        self.assertEqual(obj["kind"], KIND)
        self.assertEqual(obj["host_family"], HOST_FAMILY)
        self.assertEqual(obj["execution_authority"], "NONE")
        self.assertEqual(obj["tool_grants"], [])
        for name in REFUSAL_FLAGS:
            self.assertIs(obj[name], False, name)

    def test_verified_output_round_trip_is_still_untrusted(self):
        plan = review()
        obj = build(plan)
        self.assertEqual(validate_untrusted_host_auth_requirements(obj, plan), obj)
        self.assertEqual(obj["security_verdict"], "UNVERIFIED_NO_HOST_ATTESTATION")

    def test_exact_untrusted_bindings(self):
        obj = build()
        plan = review()
        for key in ("request_id", "request_sha256", "target_skill_id", "target_skill_version",
                    "package_receipt_sha256", "validated_lifecycle_head_sha256",
                    "evidence_digest", "expected_registry_head_sha256", "expires_at"):
            self.assertEqual(obj[key], plan[key])

    def test_thirteen_proof_questions_no_claims(self):
        self.assertEqual(len(PROOF_IDS), 13)
        self.assertEqual(len(set(PROOF_IDS)), 13)
        self.assertEqual(build()["proof_questions"], list(PROOF_IDS))

    def test_expected_layout_is_data(self):
        obj = build()
        self.assertEqual(obj["expected_layout"], EXPECTED_LAYOUT)
        obj["expected_layout"]["operator_identity"] = "tampered"
        self.assertEqual(EXPECTED_LAYOUT["operator_identity"], "kf-operator")

    def test_no_nonlinux_platform(self):
        for candidate in ("windows", "wsl", "macos", "Linux", "", None, []):
            with self.subTest(candidate=candidate), self.assertRaises(HostAuthBoundaryPlanError):
                build(host=candidate)

    def test_equal_expiry_refused(self):
        with self.assertRaises(HostAuthBoundaryPlanError):
            build(when="2026-10-14T15:10:00Z")

    def test_past_observation_refused(self):
        with self.assertRaises(HostAuthBoundaryPlanError):
            build(when="2026-10-14T15:04:59Z")

    def test_noncanonical_observation_refused(self):
        for bad in ("2026-10-14T15:06Z", "2026-10-14T15:06:00+00:00", None, "x", 1):
            with self.subTest(value=bad), self.assertRaises(HostAuthBoundaryPlanError):
                build(when=bad)

    def test_invalid_clock_refused(self):
        with self.assertRaises(HostAuthBoundaryPlanError):
            build(when="2026-99-99T15:06:00Z")

    def test_mutated_request_id_refused(self):
        obj = build()
        obj["request_id"] = "review.tampered.3001"
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_mutated_registry_head_refused(self):
        obj = build()
        obj["expected_registry_head_sha256"] = sha("1")
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_mutated_plan_digest_refused(self):
        obj = build()
        obj["review_plan_sha256"] = sha("0")
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_mutated_candidate_refused(self):
        plan = review()
        obj = build(plan)
        plan["package_receipt_sha256"] = sha("5")
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, plan)

    def test_attestation_boolean_injection_refused(self):
        for flag in REFUSAL_FLAGS:
            obj = build()
            obj[flag] = True
            with self.subTest(flag=flag), self.assertRaises(HostAuthBoundaryPlanError):
                validate_untrusted_host_auth_requirements(obj, review())

    def test_unapproved_security_verdict_refused(self):
        obj = build()
        obj["security_verdict"] = "HOST_ATTESTED"
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_tool_grant_injection_refused(self):
        obj = build()
        obj["tool_grants"] = ["operator:promote"]
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_extra_trusted_field_refused(self):
        obj = build()
        obj["verified_by_host"] = True
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_missing_required_field_refused(self):
        obj = build()
        del obj["expected_layout"]
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_proof_list_modification_refused(self):
        obj = build()
        obj["proof_questions"].append("MODEL_APPROVED")
        with self.assertRaises(HostAuthBoundaryPlanError):
            validate_untrusted_host_auth_requirements(obj, review())

    def test_read_only_nonmutating_repeatable(self):
        plan = review()
        original = deepcopy(plan)
        first = build(plan)
        second = build(plan)
        self.assertEqual(plan, original)
        self.assertEqual(first, second)

    def test_no_admin_or_operator_imports(self):
        from pathlib import Path
        import ast
        path = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "host_auth_boundary_plan.py"
        nodes = ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        found = set()
        for node in nodes:
            if isinstance(node, ast.Import):
                found.update(x.name.split(".")[0] for x in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.split(".")[0])
        self.assertFalse(found & {"os", "pwd", "grp", "subprocess", "ctypes", "winreg", "socket", "operator_cli", "operator_promotion"})


if __name__ == "__main__":
    unittest.main()
