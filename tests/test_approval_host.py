"""APPROVAL UX P1: signed host approval decisions completing real promotion.

Covers the whole chain — chat request → review receipt (non-authorizing) →
authenticated host approval → promotion through the unchanged engine →
registry verification — plus every refusal class the contract promises:
expired, altered, replayed, wrong-signed, rejected, and the exact
missing-dependency refusal when no trusted host is configured.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

from _platform_support import skip_posix_file_modes_on_windows

from knowledge_forge.approval import (
    ApprovalContractError,
    ReviewDecisionLedger,
    ReviewIdentity,
    build_approval_evidence,
    build_approval_request,
    build_current_candidate_binding,
    require_current_candidate,
    validate_review_receipt,
)
from knowledge_forge.approval_host import (
    ApprovalHostError,
    ConsumedDecisionLedger,
    authorize_from_host_decision,
    build_host_decision,
    decision_sha256,
)
import knowledge_forge.approval_host_cli
from knowledge_forge.approval_host_cli import main
from knowledge_forge.operator_promotion import (
    OperatorPromotionError,
    persist_operator_promotion,
    promote_validated_skill,
)
from knowledge_forge.registry import (
    create_skill_registry,
    load_skill_registry,
    save_skill_registry,
)
from knowledge_forge.validate import PackageValidationError, validate_or_raise
from tests.test_candidate_preview import fixture

REQUESTED_AT = "2026-10-16T12:00:00Z"
APPROVE_AT = "2026-10-16T13:00:00Z"
EXPIRES_AT = "2026-10-16T20:00:00Z"
AFTER_EXPIRY = "2026-10-16T21:00:00Z"


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )


def _sha_ref(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


class _Flow:
    """One staged candidate + contract artifacts + host key + registry."""

    def __init__(self) -> None:
        self._sandbox = tempfile.TemporaryDirectory(prefix="kf-approval-host-")
        self.root = Path(self._sandbox.name)
        self.state = self.root / "host-state"
        self.state.mkdir(parents=True, exist_ok=True)
        self.from_dir = self.root / "approval"
        self.bundle = self.root / "bundle"
        self.registry_path = self.state / "registry.json"
        self.host_key_path = self.root / "host-key.bin"
        self.host_key = secrets.token_bytes(32)

        _sandbox, self.skill_root, _lifecycle_path, self.files, (
            self.validated
        ), self.manifest = fixture()
        self.host_key_path.write_bytes(self.host_key)
        os.chmod(self.host_key_path, 0o600)

        self.source_receipt = json.loads(self.files["SOURCE_RECEIPT.json"])
        self.claim_index = json.loads(self.files["claim-index.json"])
        package_receipt = self.validated["package_receipt_sha256"]
        lifecycle_head = self.validated["events"][-1]["event_sha256"]

        self.evidence = build_approval_evidence(
            request_id="approval-host-p1-0001",
            target_skill_id=self.manifest["skill_id"],
            target_skill_version=self.manifest["skill_version"],
            package_receipt_sha256=package_receipt,
            validated_lifecycle_head_sha256=lifecycle_head,
            items=[
                {"label": "claim-index", "sha256": _sha_ref(self.files["claim-index.json"])},
                {"label": "manifest", "sha256": _sha_ref(self.files["manifest.json"])},
                {"label": "source-receipt", "sha256": _sha_ref(self.files["SOURCE_RECEIPT.json"])},
            ],
        )
        self.request = build_approval_request(
            request_id=self.evidence["request_id"],
            requesting_agent="claude-code:newcomer-pilot",
            requesting_principal="workspace-owner:tommy",
            target_skill_id=self.manifest["skill_id"],
            target_skill_version=self.manifest["skill_version"],
            package_receipt_sha256=package_receipt,
            validated_lifecycle_head_sha256=lifecycle_head,
            requested_at=REQUESTED_AT,
            expires_at=EXPIRES_AT,
            evidence_digest=self.evidence["evidence_digest"],
        )
        self.binding = build_current_candidate_binding(
            self.request, observed_at=REQUESTED_AT
        )

        _write_json(self.from_dir / "approval-request.json", self.request)
        _write_json(self.from_dir / "approval-evidence.json", self.evidence)
        _write_json(self.from_dir / "current-candidate.json", self.binding)

        (self.bundle / "package").mkdir(parents=True, exist_ok=True)
        for relative, payload in self.files.items():
            path = self.bundle / "package" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        _write_json(self.bundle / "lifecycle.validated.json", self.validated)

        save_skill_registry(
            create_skill_registry(created_at=REQUESTED_AT), self.registry_path
        )

    def teardown(self) -> None:
        self._sandbox.cleanup()

    def decision(self, *, decision="APPROVED", nonce="ab" * 16):
        return build_host_decision(
            self.request,
            decision=decision,
            decided_by="toadaid-app:user:42",
            host_id="toadaid-app",
            decision_nonce=nonce,
            decided_at=APPROVE_AT,
            host_key=self.host_key,
        )

    def authorize(self, decision=None, *, host_key="own", as_of=APPROVE_AT, nonce="ab" * 16):
        if host_key == "own":
            host_key = self.host_key
        if decision is None:
            decision = self.decision(nonce=nonce)
        return authorize_from_host_decision(
            self.request,
            decision,
            host_key=host_key,
            ledger_path=self.state / "approval-host-decisions.sqlite3",
            as_of=as_of,
        )


class ApprovalHostChainTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def test_full_chain_request_review_approval_promotion_registry(self):
        flow = self.flow
        # Review: the P2B0 receipt records the decision and stays non-authorizing.
        review_receipt = ReviewDecisionLedger().record(
            flow.request,
            flow.evidence,
            flow.binding,
            decision="APPROVE",
            reviewed_at=APPROVE_AT,
            identity=ReviewIdentity(principal_ref="local-terminal-reviewer"),
            review_surface="TERMINAL_TUI",
        )
        validate_review_receipt(review_receipt, request=flow.request)
        self.assertFalse(review_receipt["promotion_authorized"])
        self.assertFalse(review_receipt["host_authentication_verified"])
        with self.assertRaisesRegex(ApprovalHostError, "fields must match"):
            authorize_from_host_decision(
                flow.request,
                review_receipt,
                host_key=flow.host_key,
                ledger_path=flow.state / "approval-host-decisions.sqlite3",
                as_of=APPROVE_AT,
            )

        # Authenticated approval: host-signed decision on the exact request.
        decision = flow.decision()
        authorization = authorize_from_host_decision(
            flow.request,
            decision,
            host_key=flow.host_key,
            ledger_path=flow.state / "approval-host-decisions.sqlite3",
            as_of=APPROVE_AT,
        )
        self.assertTrue(authorization.verified_by_host)
        self.assertIn("approval-host:toadaid-app", authorization.principal_ref)

        # Promotion through the unchanged engine, then registry verification.
        persist_operator_promotion(
            promote_validated_skill(
                load_skill_registry(flow.registry_path),
                flow.manifest,
                flow.validated,
                flow.claim_index,
                flow.source_receipt,
                authorization=authorization,
                decision_ref=f"approval-host-decision:{decision_sha256(decision)}",
                occurred_at=APPROVE_AT,
            ),
            registry_path=flow.registry_path,
            promoted_lifecycle_path=flow.state / "promoted-lifecycle.json",
            receipt_path=flow.state / "receipt.json",
        )
        reloaded = load_skill_registry(flow.registry_path)
        skill_entry = reloaded["skills"][flow.manifest["skill_id"]]
        versions = skill_entry["versions"]
        promoted = [
            entry for _, entry in sorted(versions.items())
            if entry["lifecycle_record"]["state"] == "PROMOTED"
        ]
        self.assertTrue(promoted)
        self.assertEqual(
            promoted[0]["lifecycle_record"]["events"][-1]["actor"],
            authorization.principal_ref,
        )
        self.assertTrue((flow.state / "receipt.json").is_file())
        receipt = json.loads((flow.state / "receipt.json").read_text(encoding="utf-8"))
        self.assertFalse(receipt["model_authorized"])

        # A second approval for the same already-promoted candidate refuses:
        # the engine requires the P2C versioned-update path, never overwrite.
        second_authorization = flow.authorize(nonce="cd" * 16)
        with self.assertRaisesRegex(
            OperatorPromotionError,
            "already registered",
        ):
            promote_validated_skill(
                load_skill_registry(flow.registry_path),
                flow.manifest,
                flow.validated,
                flow.claim_index,
                flow.source_receipt,
                authorization=second_authorization,
                decision_ref="approval-host-decision:second-attempt",
                occurred_at=APPROVE_AT,
            )

    def test_expired_request_refuses_even_with_validly_signed_decision(self):
        flow = self.flow
        with self.assertRaisesRegex(ApprovalHostError, "expired"):
            flow.authorize(as_of=AFTER_EXPIRY)

    def test_altered_request_refuses_by_digest_binding(self):
        flow = self.flow
        decision = flow.decision(nonce="01" * 16)
        altered = {
            **deepcopy(flow.request),
            "package_receipt_sha256": "sha256:" + "f" * 64,
        }
        altered = build_approval_request(
            request_id=altered["request_id"],
            requesting_agent=altered["requesting_agent"],
            requesting_principal=altered["requesting_principal"],
            target_skill_id=altered["target_skill_id"],
            target_skill_version=altered["target_skill_version"],
            package_receipt_sha256=altered["package_receipt_sha256"],
            validated_lifecycle_head_sha256=altered["validated_lifecycle_head_sha256"],
            requested_at=altered["requested_at"],
            expires_at=altered["expires_at"],
            evidence_digest=altered["evidence_digest"],
        )
        with self.assertRaisesRegex(ApprovalHostError, "does not match"):
            authorize_from_host_decision(
                altered,
                decision,
                host_key=flow.host_key,
                ledger_path=flow.state / "approval-host-decisions.sqlite3",
                as_of=APPROVE_AT,
            )

    def test_replayed_decision_is_single_use(self):
        flow = self.flow
        first = flow.authorize(nonce="ee" * 16)
        self.assertTrue(first.verified_by_host)
        with self.assertRaisesRegex(ApprovalHostError, "single-use"):
            flow.authorize(nonce="ee" * 16)

    def test_wrong_host_key_refuses_forgery(self):
        flow = self.flow
        decision = flow.decision(nonce="ef" * 16)
        with self.assertRaisesRegex(ApprovalHostError, "signature invalid"):
            from knowledge_forge.approval_host import validate_host_decision
            validate_host_decision(
                decision,
                request=flow.request,
                host_key=b"another-host-secret",
                as_of=APPROVE_AT,
            )

    def test_missing_host_dependency_is_reported_exactly(self):
        flow = self.flow
        decision = flow.decision(nonce="0f" * 16)
        with self.assertRaisesRegex(
            ApprovalHostError, "no authenticated approval host is available"
        ):
            flow.authorize(decision=decision, host_key=None)

    def test_rejected_decision_never_authorizes(self):
        flow = self.flow
        with self.assertRaisesRegex(ApprovalHostError, "not APPROVED"):
            flow.authorize(decision=flow.decision(decision="REJECTED", nonce="02" * 16))

    def test_tampered_package_bytes_refuse(self):
        flow = self.flow
        tampered = dict(flow.files)
        tampered["manifest.json"] = tampered["manifest.json"] + b"\n"
        with self.assertRaises(PackageValidationError):
            validate_or_raise(tampered)

    def test_request_mismatched_with_candidate_binding_refuses(self):
        flow = self.flow
        evidence = build_approval_evidence(
            request_id=flow.request["request_id"],
            target_skill_id=flow.manifest["skill_id"],
            target_skill_version=flow.manifest["skill_version"],
            package_receipt_sha256="sha256:" + "d" * 64,
            validated_lifecycle_head_sha256=flow.request["validated_lifecycle_head_sha256"],
            items=[{"label": "manifest", "sha256": _sha_ref(flow.files["manifest.json"])}],
        )
        other_request = build_approval_request(
            request_id=evidence["request_id"],
            requesting_agent=flow.request["requesting_agent"],
            requesting_principal=flow.request["requesting_principal"],
            target_skill_id=flow.request["target_skill_id"],
            target_skill_version=flow.request["target_skill_version"],
            package_receipt_sha256=evidence["package_receipt_sha256"],
            validated_lifecycle_head_sha256=flow.request["validated_lifecycle_head_sha256"],
            requested_at=flow.request["requested_at"],
            expires_at=flow.request["expires_at"],
            evidence_digest=evidence["evidence_digest"],
        )
        with self.assertRaisesRegex(
            ApprovalContractError, "stale candidate binding: package_receipt_sha256"
        ):
            require_current_candidate(other_request, flow.binding)

    def test_ledger_rows_are_one_per_decision(self):
        flow = self.flow
        ledger_path = flow.state / "approval-host-decisions.sqlite3"
        ledger = ConsumedDecisionLedger(ledger_path)
        try:
            first = ledger.claim(
                flow.decision(nonce="03" * 16),
                principal_ref="approval-host:toadaid-app:principal:x",
                consumed_at=APPROVE_AT,
            )
            with self.assertRaisesRegex(ApprovalHostError, "single-use"):
                ledger.claim(
                    flow.decision(nonce="03" * 16),
                    principal_ref="anything",
                    consumed_at=APPROVE_AT,
                )
            second = ledger.claim(
                flow.decision(nonce="04" * 16),
                principal_ref="approval-host:toadaid-app:principal:x",
                consumed_at=APPROVE_AT,
            )
            self.assertNotEqual(first, second)
        finally:
            ledger.close()

    @skip_posix_file_modes_on_windows
    def test_host_key_construction_rules(self):
        import tempfile as _tempfile
        root = Path(_tempfile.mkdtemp(prefix="kf-host-key-rules-"))
        try:
            loose = root / "loose.bin"
            loose.write_bytes(b"k" * 32)
            os.chmod(loose, 0o644)
            from knowledge_forge.approval_host_cli import _read_host_key, ApprovalHostCliError
            with self.assertRaisesRegex(ApprovalHostCliError, "0600"):
                _read_host_key(loose)
            empty = root / "empty.bin"
            empty.write_bytes(b"")
            os.chmod(empty, 0o600)
            with self.assertRaisesRegex(ApprovalHostCliError, "1..4096 bytes"):
                _read_host_key(empty)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class ApprovalHostCliTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def test_single_action_cli_promotes_via_signed_decision(self):
        flow = self.flow
        _write_json(
            flow.from_dir / "approval-host-decision.json",
            flow.decision(nonce="05" * 16),
        )
        out = StringIO()
        from knowledge_forge.approval_host_cli import ApprovalHostCliError
        from knowledge_forge.operator_promotion import (
            persist_operator_promotion as persist,  # unused here; silence flake
        )
        del persist
        with redirect_stdout(out):
            code = main([
                str(flow.from_dir),
                "--bundle", str(flow.bundle),
                "--registry", str(flow.registry_path),
                "--host-key", str(flow.host_key_path),
                "--promoted-lifecycle-out", str(flow.state / "promoted-lifecycle.json"),
                "--receipt-out", str(flow.state / "receipt.json"),
                "--now", APPROVE_AT,
            ])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["promoted"])
        self.assertFalse(payload["model_authorized"])
        reloaded = load_skill_registry(flow.registry_path)
        for _, entry in reloaded["skills"][flow.manifest["skill_id"]]["versions"].items():
            if entry["lifecycle_record"]["state"] == "PROMOTED":
                break
        else:
            self.fail("registry did not reach PROMOTED through the CLI flow")

    def test_cli_refuses_before_promotion_on_bad_host_key(self):
        flow = self.flow
        _write_json(
            flow.from_dir / "approval-host-decision.json",
            {"bogus": True},
        )
        out, err = StringIO(), StringIO()
        from contextlib import redirect_stderr
        with redirect_stdout(out), redirect_stderr(err):
            code = main([
                str(flow.from_dir),
                "--bundle", str(flow.bundle),
                "--registry", str(flow.registry_path),
                "--host-key", str(flow.root / "missing-key.bin"),
                "--promoted-lifecycle-out", str(flow.state / "l.json"),
                "--receipt-out", str(flow.state / "r.json"),
                "--now", APPROVE_AT,
            ])
        self.assertEqual(code, 2)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["promoted"])
        self.assertIn("host key", payload["error"])

    def test_module_entrypoint_actually_executes(self):
        # The trusted host invokes the CLI as
        # `python3 -m knowledge_forge.approval_host_cli <args>` through
        # bin/knowledge-forge-approve. A module without a __main__
        # guard would import, exit 0, print nothing, and promote
        # nothing — a silent no-op that looks like success.
        src_dir = Path(
            knowledge_forge.approval_host_cli.__file__
        ).resolve().parents[1]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(src_dir) + os.pathsep + env.get("PYTHONPATH", "")
        for args, expect_rc in (
            (["--help"], 0),
            ([], 2),  # argparse missing-required-arguments failure, not silence
        ):
            with self.subTest(args=args):
                proc = subprocess.run(
                    [sys.executable, "-m", "knowledge_forge.approval_host_cli", *args],
                    capture_output=True, text=True, env=env, timeout=60,
                )
                self.assertEqual(proc.returncode, expect_rc)
                self.assertNotEqual(
                    proc.stdout + proc.stderr,
                    "",
                    "module ran as a silent no-op",
                )


if __name__ == "__main__":
    import unittest as _unittest
    _unittest.main()