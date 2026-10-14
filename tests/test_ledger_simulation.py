"""P2C-P3-P0: no-authority ledger rehearsal and adversarial replay tests."""
from __future__ import annotations

from copy import deepcopy
import ast
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_forge.host_auth_boundary_plan import build_untrusted_host_auth_requirements
from knowledge_forge.ledger_simulation import (
    KIND, MAX_EVENTS, LedgerSimulationError, create_rehearsal,
    rehearse_transition, validate_rehearsal,
)

T0 = "2026-10-14T15:05:00Z"
T1 = "2026-10-14T15:06:00Z"
T2 = "2026-10-14T15:07:00Z"
T3 = "2026-10-14T15:08:00Z"
EXP = "2026-10-14T15:10:00Z"


def sha(c):
    return "sha256:" + c * 64


def review():
    return {
        "schema_version": "0.1", "kind": "UNTRUSTED_OPERATOR_REVIEW_PLAN_NOT_AUTHORIZATION",
        "request_id": "review.synthetic.3001", "request_sha256": sha("a"),
        "target_skill_id": "knowledge-forge:roof.quote@" + sha("b"),
        "target_skill_version": 1, "package_receipt_sha256": sha("c"),
        "validated_lifecycle_head_sha256": sha("d"), "evidence_digest": sha("e"),
        "expected_registry_head_sha256": sha("f"),
        "observed_at": T0, "expires_at": EXP,
        "candidate_binding_source": "CALLER_SUPPLIED_UNTRUSTED",
        "registry_head_source": "CALLER_SUPPLIED_UNTRUSTED",
        "execution_authority": "NONE", "tool_grants": [],
        "independent_host_attestation_verified": False,
        "independent_candidate_recheck_verified": False,
        "independent_registry_recheck_verified": False,
        "human_authentication_verified": False,
        "protected_ledger_write_performed": False,
        "authorization_issued": False, "operator_invoked": False,
        "promotion_authorized": False, "registry_mutated": False,
    }


def setup():
    p = review()
    q = build_untrusted_host_auth_requirements(p, observed_at=T0)
    return p, q


def create():
    p, q = setup()
    return create_rehearsal(q, p, at=T0), q, p


def step(state, q, p, action, at):
    return rehearse_transition(state, q, p, action=action, at=at,
                               expected_revision_sha256=state["revision_sha256"])


class UntrustedLedgerRehearsalTests(unittest.TestCase):
    def test_new_state_roundtrip(self):
        s, q, p = create()
        self.assertEqual(s["kind"], KIND)
        self.assertEqual(s["state"], "PENDING")
        self.assertEqual(s["revision"], 0)
        self.assertEqual(validate_rehearsal(s, q, p), s)

    def test_permanent_zero_authority(self):
        s, q, p = create()
        for action, at in [("PREPARE_SIMULATION", T1), ("CONSUME_SIMULATION", T2), ("RESTART_SIMULATION", T3)]:
            s = step(s, q, p, action, at)
            self.assertEqual(s["execution_authority"], "NONE")
            self.assertEqual(s["tool_grants"], [])
            for key in ("human_authenticated", "authorization_issued", "operator_invoked",
                        "promotion_authorized", "registry_mutated", "atomic_consumption_verified",
                        "durable_storage_verified", "host_time_verified", "replay_protection_verified"):
                self.assertIs(s[key], False, key)

    def test_prepare_consume_once_in_one_snapshot(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        b = step(a, q, p, "CONSUME_SIMULATION", T2)
        self.assertEqual(b["state"], "CONSUMED_SIMULATION")
        self.assertEqual(b["revision"], 2)
        with self.assertRaises(LedgerSimulationError):
            step(b, q, p, "CONSUME_SIMULATION", T3)

    def test_consume_without_prepare_refused(self):
        s, q, p = create()
        with self.assertRaises(LedgerSimulationError):
            step(s, q, p, "CONSUME_SIMULATION", T1)

    def test_stale_revision_refused(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        with self.assertRaisesRegex(LedgerSimulationError, "stale snapshot"):
            rehearse_transition(a, q, p, action="CONSUME_SIMULATION", at=T2,
                                 expected_revision_sha256=s["revision_sha256"])

    def test_cancel_is_terminal(self):
        s, q, p = create()
        c = step(s, q, p, "CANCEL_SIMULATION", T1)
        self.assertEqual(c["state"], "CANCELLED")
        with self.assertRaises(LedgerSimulationError):
            step(c, q, p, "PREPARE_SIMULATION", T2)

    def test_cancel_prepared_is_terminal(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        c = step(a, q, p, "CANCEL_SIMULATION", T2)
        with self.assertRaises(LedgerSimulationError):
            step(c, q, p, "CONSUME_SIMULATION", T3)

    def test_expire_before_deadline_refused(self):
        s, q, p = create()
        with self.assertRaisesRegex(LedgerSimulationError, "not expired"):
            step(s, q, p, "EXPIRE_SIMULATION", T1)

    def test_expire_at_deadline_terminal(self):
        s, q, p = create()
        e = step(s, q, p, "EXPIRE_SIMULATION", EXP)
        self.assertEqual(e["state"], "EXPIRED")
        with self.assertRaises(LedgerSimulationError):
            step(e, q, p, "PREPARE_SIMULATION", EXP)

    def test_consume_after_expiry_refused(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        with self.assertRaises(LedgerSimulationError):
            step(a, q, p, "CONSUME_SIMULATION", EXP)

    def test_backward_clock_refused(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T2)
        with self.assertRaises(LedgerSimulationError):
            step(a, q, p, "CONSUME_SIMULATION", T1)

    def test_reboot_after_prepare_requires_reconciliation(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        r = step(a, q, p, "RESTART_SIMULATION", T2)
        self.assertEqual(r["state"], "RECONCILIATION_REQUIRED")
        with self.assertRaises(LedgerSimulationError):
            step(r, q, p, "CONSUME_SIMULATION", T3)

    def test_reboot_after_consume_requires_reconciliation(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        b = step(a, q, p, "CONSUME_SIMULATION", T2)
        r = step(b, q, p, "RESTART_SIMULATION", T3)
        self.assertEqual(r["state"], "RECONCILIATION_REQUIRED")
        with self.assertRaises(LedgerSimulationError):
            step(r, q, p, "PREPARE_SIMULATION", T3)

    def test_restart_even_after_expiry_is_reconciliation(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        r = step(a, q, p, "RESTART_SIMULATION", "2026-10-14T15:11:00Z")
        self.assertEqual(r["state"], "RECONCILIATION_REQUIRED")

    def test_reconciliation_close_no_effect_terminal_before_expiry(self):
        s, q, p = create()
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        r = step(a, q, p, "RESTART_SIMULATION", T2)
        closed = step(r, q, p, "CLOSE_NO_EFFECT_SIMULATION", T3)
        self.assertEqual(closed["state"], "CLOSED_NO_EFFECT")
        with self.assertRaises(LedgerSimulationError):
            step(closed, q, p, "CONSUME_SIMULATION", T3)

    def test_unknown_action_refused(self):
        s, q, p = create()
        with self.assertRaises(LedgerSimulationError):
            step(s, q, p, "APPROVE", T1)

    def test_injected_host_verification_refused(self):
        s, q, p = create()
        for key in ("authorization_issued", "human_authenticated", "promotion_authorized",
                    "durable_storage_verified", "atomic_consumption_verified"):
            fake = deepcopy(s); fake[key] = True
            with self.subTest(key=key), self.assertRaises(LedgerSimulationError):
                validate_rehearsal(fake, q, p)

    def test_injected_operator_cli_fields_refused(self):
        s, q, p = create()
        s["verified_by_host"] = True
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p)

    def test_tool_grants_refused(self):
        s, q, p = create(); s["tool_grants"] = ["operator:promote"]
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p)

    def test_digest_tamper_refused(self):
        s, q, p = create(); s["revision_sha256"] = sha("0")
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p)

    def test_event_tamper_refused(self):
        s, q, p = create(); a = step(s, q, p, "PREPARE_SIMULATION", T1)
        a["events"][0]["action"] = "CANCEL_SIMULATION"
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(a, q, p)

    def test_event_parent_tamper_refused(self):
        s, q, p = create(); a = step(s, q, p, "PREPARE_SIMULATION", T1)
        a["events"][0]["prev_revision_sha256"] = sha("9")
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(a, q, p)

    def test_event_missing_refused(self):
        s, q, p = create(); a = step(s, q, p, "PREPARE_SIMULATION", T1)
        a["events"].clear()
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(a, q, p)

    def test_revision_boolean_refused(self):
        s, q, p = create(); s["revision"] = False
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p)

    def test_changed_expected_registry_refused(self):
        s, q, p = create(); q2 = deepcopy(q)
        q2["expected_registry_head_sha256"] = sha("8")
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q2, p)

    def test_changed_review_plan_refused(self):
        s, q, p = create(); p2 = deepcopy(p)
        p2["expected_registry_head_sha256"] = sha("8")
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p2)

    def test_noncanonical_time_refused(self):
        s, q, p = create()
        with self.assertRaises(LedgerSimulationError):
            step(s, q, p, "PREPARE_SIMULATION", "2026-10-14T15:06Z")

    def test_rehydrate_json_in_synthetic_tempfile(self):
        s, q, p = create(); a = step(s, q, p, "PREPARE_SIMULATION", T1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "synthetic-only.json"
            path.write_text(json.dumps(a, sort_keys=True), encoding="utf-8")
            b = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(validate_rehearsal(b, q, p), a)
            r = step(b, q, p, "RESTART_SIMULATION", T2)
            self.assertEqual(r["state"], "RECONCILIATION_REQUIRED")

    def test_snapshots_are_not_global_atomicity(self):
        # Two readers retaining the same old value can BOTH simulate a consume.
        # The REAL host needs atomic storage+locking+trusted OS identity to prevent this.
        s, q, p = create(); prepared = step(s, q, p, "PREPARE_SIMULATION", T1)
        result_1 = step(prepared, q, p, "CONSUME_SIMULATION", T2)
        result_2 = step(deepcopy(prepared), q, p, "CONSUME_SIMULATION", T2)
        self.assertEqual(result_1, result_2)
        self.assertFalse(result_1["atomic_consumption_verified"])

    def test_deterministic_results_and_no_mutation(self):
        s, q, p = create(); before = deepcopy((s, q, p))
        a = step(s, q, p, "PREPARE_SIMULATION", T1)
        b = step(s, q, p, "PREPARE_SIMULATION", T1)
        self.assertEqual(a, b)
        self.assertEqual((s, q, p), before)

    def test_no_os_or_operator_imports(self):
        path = Path(__file__).resolve().parents[1] / "src" / "knowledge_forge" / "ledger_simulation.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        self.assertFalse(names & {"os", "pwd", "grp", "subprocess", "fcntl", "ctypes", "socket", "operator_cli", "operator_promotion", "tempfile", "pathlib"})

    def test_event_limit_refused(self):
        s, q, p = create()
        s["events"] = [{}] * (MAX_EVENTS + 1)
        s["revision"] = MAX_EVENTS + 1
        with self.assertRaises(LedgerSimulationError):
            validate_rehearsal(s, q, p)


if __name__ == "__main__":
    unittest.main()
