from __future__ import annotations

import tempfile
from pathlib import Path
import os
import stat
import subprocess
import unittest
from unittest.mock import Mock, patch

from knowledge_forge.codex_credential_registry import (
    CodexCredentialError,
    build_credential_registry,
    save_credential_registry,
    verify_registered_proof,
)
from knowledge_forge.codex_p256 import CodexP256Error, verify_p256_sha256
from knowledge_forge.codex_enrollment import CodexAppServerClient, enroll


CHALLENGE = "a25vd2xlZGdlLWZvcmdlLWNvZGV4LXByb29mLXRlc3Q"
PUBLIC_KEY = (
    "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEbz9p_TljUmINI99acuWui7TN-l80l"
    "lnUfM0n6T63PdsmwoY2pAQ49q8OskrvoZQX5jmRJ4H6KaKg-arpDGGltA"
)
SIGNATURE = (
    "MEQCIHtpoU0ZhdwpP1zdHooO2DwDfU8Pza2SVcXRizw1OZR6AiBuscFtDKBVp9OD"
    "vXUx8iEiaIhM7fW3xmqM5lhAPEitKw"
)


class CodexP256Tests(unittest.TestCase):
    def test_app_server_client_opts_into_experimental_user_verification(self) -> None:
        process = Mock()
        process.stdin = Mock()
        process.stdout = iter(
            [
                '{"jsonrpc":"2.0","id":1,"result":{}}\n',
                '{"jsonrpc":"2.0","id":2,"result":{"credentialId":"synthetic-device"}}\n',
            ]
        )
        process.stderr = iter(())
        process.poll.return_value = 0
        process.wait.return_value = 0
        with patch.object(subprocess, "Popen", return_value=process):
            client = CodexAppServerClient()
            client.request("userVerification/status", 2)

        initialize = process.stdin.write.call_args_list[0].args[0]
        self.assertIn('"capabilities":{"experimentalApi":true}', initialize)

    def test_known_p256_sha256_proof_verifies(self) -> None:
        verify_p256_sha256(CHALLENGE, SIGNATURE, PUBLIC_KEY)

    def test_changed_challenge_and_noncanonical_data_refuse(self) -> None:
        with self.assertRaisesRegex(CodexP256Error, "did not verify"):
            verify_p256_sha256(CHALLENGE + "A", SIGNATURE, PUBLIC_KEY)
        with self.assertRaisesRegex(CodexP256Error, "unpadded"):
            verify_p256_sha256(CHALLENGE + "=", SIGNATURE, PUBLIC_KEY)

    def test_enrolled_credential_maps_verified_proof_to_principal(self) -> None:
        with tempfile.TemporaryDirectory() as sandbox:
            path = Path(sandbox) / "credentials.json"
            value = build_credential_registry(
                credential_id="codex-test-device",
                public_key=PUBLIC_KEY,
                principal="codex:user:tommy",
                enrolled_at="2026-10-16T13:00:00Z",
            )
            save_credential_registry(value, path)
            self.assertEqual(
                verify_registered_proof(
                    path, CHALLENGE, "codex-test-device", SIGNATURE
                ),
                "codex:user:tommy",
            )
            with self.assertRaisesRegex(CodexCredentialError, "not enrolled"):
                verify_registered_proof(path, CHALLENGE, "another-device", SIGNATURE)
            with self.assertRaisesRegex(CodexCredentialError, "refusing to overwrite"):
                save_credential_registry(value, path)

    def test_app_server_enrollment_writes_create_new_private_registry(self) -> None:
        class FakeClient:
            def __init__(self, _command):
                pass
            def request(self, method, request_id):
                self.assertions = (method, request_id)
                return {
                    "credentialId": "codex-test-device",
                    "publicKey": PUBLIC_KEY,
                    "algorithm": "ES256",
                }
            def close(self):
                pass

        with tempfile.TemporaryDirectory() as sandbox:
            path = Path(sandbox) / "codex-credentials.json"
            with patch(
                "knowledge_forge.codex_enrollment.CodexAppServerClient",
                FakeClient,
            ):
                result = enroll(
                    path,
                    principal="codex:user:tommy",
                    enrolled_at="2026-10-16T13:00:00Z",
                )
            self.assertTrue(result["enrolled"])
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(
                verify_registered_proof(
                    path, CHALLENGE, "codex-test-device", SIGNATURE
                ),
                "codex:user:tommy",
            )


if __name__ == "__main__":
    raise SystemExit(unittest.main())
