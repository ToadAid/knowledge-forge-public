from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import Mock, patch

from knowledge_forge.codex_approval_bridge import (
    CODEX_APPROVAL_CHALLENGE_KIND,
    CODEX_USER_VERIFICATION_METHOD,
    CodexApprovalBridgeError,
    complete_codex_native_approval,
    prepare_codex_native_approval,
)
from knowledge_forge.registry import load_skill_registry
from tests.test_approval_host import APPROVE_AT, _Flow


NONCE = "17" * 32
CREDENTIAL = "codex-device-credential-0001"
SIGNATURE = base64.urlsafe_b64encode(b"signed-device-proof" * 4).decode().rstrip("=")


class CodexApprovalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def prepare(self, **kwargs):
        with patch(
            "knowledge_forge.codex_approval_bridge.secrets.token_hex",
            return_value=NONCE,
        ):
            return prepare_codex_native_approval(
                self.flow.from_dir,
                self.flow.bundle,
                observed_at=APPROVE_AT,
                **kwargs,
            )

    def proof(self):
        return {
            "proof": {
                "credentialId": CREDENTIAL,
                "signature": SIGNATURE,
            }
        }

    def complete(self, prepared, result, verifier, *, now=APPROVE_AT):
        return complete_codex_native_approval(
            self.flow.from_dir,
            self.flow.bundle,
            prepared=prepared,
            verification_result=result,
            verify_proof=verifier,
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            now=now,
        )

    def test_prepare_builds_bounded_non_authorizing_native_rpc(self):
        prepared = self.prepare()
        rpc = prepared["codex_user_verification"]
        record = prepared["challenge_record"]
        self.assertEqual(rpc["method"], CODEX_USER_VERIFICATION_METHOD)
        self.assertEqual(record["kind"], CODEX_APPROVAL_CHALLENGE_KIND)
        self.assertEqual(record["adapter_id"], "codex")
        self.assertEqual(record["action"], "PROMOTE_VALIDATED_SKILL_ONCE")
        self.assertEqual(record["request_sha256"], prepared["request_sha256"])
        self.assertEqual(record["approval_id"], f"codex:{NONCE}")
        self.assertFalse(prepared["authorization_granted"])
        self.assertFalse(prepared["promotion_performed"])
        decoded = base64.urlsafe_b64decode(
            rpc["params"]["challenge"]
            + "=" * (-len(rpc["params"]["challenge"]) % 4)
        )
        self.assertEqual(
            json.loads(decoded.decode("utf-8")), prepared["challenge_record"]
        )

    def test_verified_device_proof_promotes_once(self):
        prepared = self.prepare()
        verifier = Mock(return_value="codex:user:tommy")
        result = self.complete(prepared, self.proof(), verifier)
        self.assertTrue(result["promoted"])
        self.assertTrue(result["device_proof_verified"])
        self.assertEqual(result["interaction"], "CODEX_NATIVE_USER_VERIFICATION")
        self.assertEqual(result["credential_id"], CREDENTIAL)
        challenge, credential, signature = verifier.call_args.args
        self.assertEqual(
            json.loads(challenge.decode("utf-8")), prepared["challenge_record"]
        )
        self.assertEqual(credential, CREDENTIAL)
        self.assertEqual(signature, SIGNATURE)
        registry = load_skill_registry(self.flow.registry_path)
        self.assertIn(self.flow.manifest["skill_id"], registry["skills"])

    def test_chat_text_cannot_stand_in_for_native_proof(self):
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "chat text"):
            self.complete(self.prepare(), "APPROVE", verifier)  # type: ignore[arg-type]
        verifier.assert_not_called()

    def test_protected_verifier_failure_refuses_before_promotion(self):
        verifier = Mock(side_effect=ValueError("bad P-256 signature"))
        with self.assertRaisesRegex(CodexApprovalBridgeError, "not verified"):
            self.complete(self.prepare(), self.proof(), verifier)
        self.assertEqual(load_skill_registry(self.flow.registry_path)["skills"], {})

    def test_empty_principal_from_verifier_refuses(self):
        with self.assertRaisesRegex(CodexApprovalBridgeError, "authenticated principal"):
            self.complete(self.prepare(), self.proof(), lambda *_: "")

    def test_changed_request_binding_refuses_before_verification(self):
        prepared = self.prepare()
        prepared["challenge_record"]["request_sha256"] = "sha256:" + "f" * 64
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "current request"):
            self.complete(prepared, self.proof(), verifier)
        verifier.assert_not_called()

    def test_changed_display_refuses_before_verification(self):
        prepared = self.prepare()
        prepared["codex_user_verification"]["params"]["description"] += "\nTrust me"
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "display changed"):
            self.complete(prepared, self.proof(), verifier)
        verifier.assert_not_called()

    def test_changed_challenge_bytes_refuse_before_verification(self):
        prepared = self.prepare()
        encoded = prepared["codex_user_verification"]["params"]["challenge"]
        prepared["codex_user_verification"]["params"]["challenge"] = (
            ("A" if encoded[0] != "A" else "B") + encoded[1:]
        )
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "do not match"):
            self.complete(prepared, self.proof(), verifier)
        verifier.assert_not_called()

    def test_expired_challenge_refuses_before_verification(self):
        prepared = self.prepare(lifetime_seconds=60)
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "stale or expired"):
            self.complete(
                prepared,
                self.proof(),
                verifier,
                now="2026-10-16T13:01:00Z",
            )
        verifier.assert_not_called()

    def test_malformed_or_extra_proof_fields_refuse(self):
        result = self.proof()
        result["proof"]["agentApproved"] = True
        verifier = Mock(return_value="codex:user:tommy")
        with self.assertRaisesRegex(CodexApprovalBridgeError, "exact contract"):
            self.complete(self.prepare(), result, verifier)
        verifier.assert_not_called()

    def test_invalid_credential_or_signature_refuses(self):
        for field, value, message in (
            ("credentialId", "model", "credential ID"),
            ("signature", "not+base64", "device signature"),
        ):
            with self.subTest(field=field):
                result = self.proof()
                result["proof"][field] = value
                with self.assertRaisesRegex(CodexApprovalBridgeError, message):
                    self.complete(self.prepare(), result, Mock())

    def test_challenge_lifetime_is_bounded(self):
        for value in (0, 301, True, "60"):
            with self.subTest(value=value), self.assertRaisesRegex(
                CodexApprovalBridgeError, "1..300"
            ):
                self.prepare(lifetime_seconds=value)  # type: ignore[arg-type]

    def test_bad_host_randomness_refuses(self):
        with patch(
            "knowledge_forge.codex_approval_bridge.secrets.token_hex",
            return_value="predictable",
        ), self.assertRaisesRegex(CodexApprovalBridgeError, "randomness"):
            prepare_codex_native_approval(
                self.flow.from_dir,
                self.flow.bundle,
                observed_at=APPROVE_AT,
            )


if __name__ == "__main__":
    unittest.main()
