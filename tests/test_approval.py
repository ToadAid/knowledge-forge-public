from __future__ import annotations

from copy import deepcopy
import unittest

from knowledge_forge.approval import (
    ApprovalContractError,
    ReviewDecisionLedger,
    ReviewIdentity,
    approval_request_sha256,
    bind_request_evidence,
    build_approval_evidence,
    build_approval_request,
    build_current_candidate_binding,
    issue_operator_authorization_from_review,
    project_approval_request,
    reconcile_review_state,
    validate_approval_request,
    validate_review_receipt,
)


REQUESTED = "2026-10-13T16:00:00Z"
REVIEWED = "2026-10-13T16:05:00Z"
EXPIRES = "2026-10-13T16:30:00Z"


def sha(char: str) -> str:
    return "sha256:" + char * 64


def fixture():
    evidence = build_approval_evidence(
        request_id="review.synthetic.0001",
        target_skill_id="skill.synthetic.roofing",
        target_skill_version=3,
        package_receipt_sha256=sha("a"),
        validated_lifecycle_head_sha256=sha("b"),
        items=[
            {"label": "faithfulness-report", "sha256": sha("c")},
            {"label": "validation-report", "sha256": sha("d")},
        ],
    )
    request = build_approval_request(
        request_id=evidence["request_id"],
        requesting_agent="Claude Code",
        requesting_principal="agent-session-0001",
        target_skill_id=evidence["target_skill_id"],
        target_skill_version=evidence["target_skill_version"],
        package_receipt_sha256=evidence["package_receipt_sha256"],
        validated_lifecycle_head_sha256=evidence["validated_lifecycle_head_sha256"],
        requested_at=REQUESTED,
        expires_at=EXPIRES,
        evidence_digest=evidence["evidence_digest"],
    )
    binding = build_current_candidate_binding(request, observed_at=REVIEWED)
    identity = ReviewIdentity(principal_ref="local-terminal-reviewer")
    return request, evidence, binding, identity


class ApprovalContractTests(unittest.TestCase):
    def test_valid_request_construction_and_exact_bindings(self):
        request, evidence, binding, _identity = fixture()
        self.assertEqual(validate_approval_request(request), request)
        self.assertEqual(bind_request_evidence(request, evidence), (request, evidence))
        self.assertEqual(binding["package_receipt_sha256"], request["package_receipt_sha256"])
        self.assertEqual(
            binding["validated_lifecycle_head_sha256"],
            request["validated_lifecycle_head_sha256"],
        )
        self.assertRegex(approval_request_sha256(request), r"^sha256:[0-9a-f]{64}$")

    def test_tampered_evidence_and_stale_candidate_refuse(self):
        request, evidence, binding, identity = fixture()
        tampered = deepcopy(evidence)
        tampered["items"][0]["sha256"] = sha("e")
        with self.assertRaisesRegex(ApprovalContractError, "digest or content mismatch"):
            bind_request_evidence(request, tampered)

        stale = deepcopy(binding)
        stale["validated_lifecycle_head_sha256"] = sha("f")
        with self.assertRaisesRegex(ApprovalContractError, "stale candidate"):
            ReviewDecisionLedger().record(
                request,
                evidence,
                stale,
                decision="APPROVE",
                reviewed_at=REVIEWED,
                identity=identity,
                review_surface="TERMINAL_TUI",
            )

    def test_expired_request_refuses_approval_and_accepts_expiry_state(self):
        request, evidence, binding, identity = fixture()
        after = "2026-10-13T16:31:00Z"
        with self.assertRaisesRegex(ApprovalContractError, "expired approval request"):
            ReviewDecisionLedger().record(
                request,
                evidence,
                binding,
                decision="APPROVE",
                reviewed_at=after,
                identity=identity,
                review_surface="TERMINAL_TUI",
            )
        receipt = ReviewDecisionLedger().record(
            request,
            evidence,
            binding,
            decision="EXPIRE",
            reviewed_at=after,
            identity=identity,
            review_surface="TERMINAL_TUI",
        )
        self.assertEqual(receipt["state"], "EXPIRED")
        self.assertFalse(receipt["authorization_granted"])

    def test_duplicate_and_replay_refuse_and_conflicts_require_reconciliation(self):
        request, evidence, binding, identity = fixture()
        ledger = ReviewDecisionLedger()
        receipt = ledger.record(
            request,
            evidence,
            binding,
            decision="APPROVE",
            reviewed_at=REVIEWED,
            identity=identity,
            review_surface="TERMINAL_TUI",
        )
        with self.assertRaisesRegex(ApprovalContractError, "replay or duplicate"):
            ledger.record(
                request,
                evidence,
                binding,
                decision="APPROVE",
                reviewed_at=REVIEWED,
                identity=identity,
                review_surface="TERMINAL_TUI",
            )
        self.assertEqual(
            reconcile_review_state(request, [receipt, receipt], as_of=REVIEWED),
            "RECONCILIATION_REQUIRED",
        )

    def test_unknown_schema_and_unknown_fields_fail_closed(self):
        request, _evidence, _binding, _identity = fixture()
        unknown_schema = {**request, "schema_version": "9.9"}
        with self.assertRaisesRegex(ApprovalContractError, "unsupported"):
            validate_approval_request(unknown_schema)
        unknown_field = {**request, "telegram_chat_id": "forged"}
        with self.assertRaisesRegex(ApprovalContractError, "unknown"):
            validate_approval_request(unknown_field)

    def test_forged_principal_and_model_self_review_refuse(self):
        request, evidence, binding, _identity = fixture()
        forged = ReviewIdentity(
            principal_ref="human-admin",
            identity_kind="AUTHENTICATED_HOST_OPERATOR",
            source="MODEL_SUPPLIED",
            host_authenticated=True,
        )
        with self.assertRaisesRegex(ApprovalContractError, "host authentication"):
            ReviewDecisionLedger().record(
                request,
                evidence,
                binding,
                decision="APPROVE",
                reviewed_at=REVIEWED,
                identity=forged,
                review_surface="TERMINAL_TUI",
            )
        self_review = ReviewIdentity(principal_ref=request["requesting_principal"])
        with self.assertRaisesRegex(ApprovalContractError, "cannot review its own"):
            ReviewDecisionLedger().record(
                request,
                evidence,
                binding,
                decision="APPROVE",
                reviewed_at=REVIEWED,
                identity=self_review,
                review_surface="TERMINAL_TUI",
            )

    def test_missing_protected_host_authentication_never_becomes_authorization(self):
        request, evidence, binding, identity = fixture()
        receipt = ReviewDecisionLedger().record(
            request,
            evidence,
            binding,
            decision="APPROVE",
            reviewed_at=REVIEWED,
            identity=identity,
            review_surface="TERMINAL_TUI",
        )
        self.assertFalse(receipt["host_authentication_verified"])
        self.assertFalse(receipt["authorization_granted"])
        self.assertFalse(receipt["promotion_authorized"])
        with self.assertRaisesRegex(ApprovalContractError, "cannot create OperatorAuthorization"):
            issue_operator_authorization_from_review(receipt)

    def test_tool_or_authority_widening_refuses(self):
        request, evidence, binding, identity = fixture()
        with self.assertRaisesRegex(ApprovalContractError, "unknown"):
            validate_approval_request({**request, "tool_grants": ["knowledge-forge-operator"]})
        receipt = ReviewDecisionLedger().record(
            request,
            evidence,
            binding,
            decision="APPROVE",
            reviewed_at=REVIEWED,
            identity=identity,
            review_surface="TERMINAL_TUI",
        )
        forged = {**receipt, "tool_grants": ["knowledge-forge-operator"]}
        with self.assertRaisesRegex(ApprovalContractError, "widen"):
            validate_review_receipt(forged, request=request)

    def test_required_decisions_and_states_are_non_authorizing(self):
        expected = {
            "APPROVE": "REVIEWED_APPROVED",
            "REJECT": "REVIEWED_REJECTED",
            "REQUEST_CHANGES": "CHANGES_REQUESTED",
            "CANCEL": "CANCELLED",
        }
        for decision, state in expected.items():
            request, evidence, binding, identity = fixture()
            receipt = ReviewDecisionLedger().record(
                request,
                evidence,
                binding,
                decision=decision,
                reviewed_at=REVIEWED,
                identity=identity,
                review_surface="DESKTOP_CARD",
                reason="synthetic reason" if decision in {"REJECT", "REQUEST_CHANGES"} else None,
            )
            self.assertEqual(receipt["state"], state)
            self.assertFalse(receipt["authorization_granted"])
            self.assertFalse(receipt["operator_cli_invoked"])
            self.assertFalse(receipt["production_registry_mutated"])

    def test_multi_surface_projection_is_deterministic_and_transport_neutral(self):
        request, evidence, _binding, _identity = fixture()
        projections = []
        for surface in ("TERMINAL_TUI", "TELEGRAM_CARD", "DESKTOP_CARD", "WEB_TEMPLE"):
            first = project_approval_request(request, evidence, surface=surface, as_of=REVIEWED)
            second = project_approval_request(request, evidence, surface=surface, as_of=REVIEWED)
            self.assertEqual(first, second)
            self.assertEqual(first["transport_authority"], "NONE")
            self.assertFalse(first["authorization_granted"])
            self.assertEqual(first["tool_grants"], [])
            projections.append(first)
        shared = {key: value for key, value in projections[0].items() if key != "surface"}
        for projection in projections[1:]:
            self.assertEqual(
                shared,
                {key: value for key, value in projection.items() if key != "surface"},
            )

    def test_identical_decision_inputs_produce_identical_receipts(self):
        request, evidence, binding, identity = fixture()
        receipts = [
            ReviewDecisionLedger().record(
                request,
                evidence,
                binding,
                decision="REQUEST_CHANGES",
                reviewed_at=REVIEWED,
                identity=identity,
                review_surface="WEB_TEMPLE",
                reason="Bind exact updated evidence",
            )
            for _index in range(2)
        ]
        self.assertEqual(receipts[0], receipts[1])


if __name__ == "__main__":
    unittest.main()
