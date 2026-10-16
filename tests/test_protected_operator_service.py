from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import stat
import sqlite3
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows("protected_operator_service")

from knowledge_forge.protected_operator_client import snapshot_bundle
from knowledge_forge.protected_operator_service import (
    OperatorPolicy,
    PeerIdentity,
    ProtectedLedger,
    ProtectedOperatorError,
    ConfirmationMismatch,
    _promotion_lock,
    confirm_promotion,
    decode_bundle,
    prepare_promotion,
    peer_identity,
)
from knowledge_forge.registry import create_skill_registry, load_skill_registry, save_skill_registry
from tests.test_candidate_preview import fixture


CLOCK = datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)


class ProtectedOperatorServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        (self.state / "lifecycles").mkdir(mode=0o700)
        (self.state / "receipts").mkdir(mode=0o700)
        save_skill_registry(create_skill_registry(created_at="2026-10-16T04:00:00Z"), self.state / "registry.json")
        ProtectedLedger(self.state / "authorizations.sqlite3")
        self.policy = OperatorPolicy("kf-operator", "kf-agent", self.state, self.root)
        self.peer = PeerIdentity(pid=1234, uid=1000, gid=1000, principal="human-reviewer")
        sandbox, skill_root, lifecycle_path, files, lifecycle, manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        self.files = files
        self.lifecycle = lifecycle
        self.manifest = manifest
        self.bundle = {
            "schema_version": "0.1",
            "files": {name: base64.b64encode(payload).decode("ascii") for name, payload in files.items()},
            "lifecycle": base64.b64encode(json.dumps(lifecycle).encode("utf-8")).decode("ascii"),
        }

    def exchange(self, confirmations):
        errors = []
        session, challenge = prepare_promotion(
            {"operation": "PROMOTE", "bundle": self.bundle},
            policy=self.policy, peer=self.peer, now=CLOCK,
        )
        responses = []
        for confirmation in confirmations(challenge):
            try:
                responses.append(confirm_promotion(
                    session,
                    {"challenge_id": challenge["challenge_id"], "confirmation": confirmation},
                    now=CLOCK,
                ))
                break
            except ConfirmationMismatch as exc:
                status = "REFUSED" if exc.remaining == 0 else "MISMATCH"
                responses.append({"status": status, "error": str(exc), "remaining": exc.remaining})
                if exc.remaining == 0:
                    errors.append(exc)
                    break
            except Exception as exc:
                errors.append(exc)
                break
        return challenge, responses, errors

    def test_one_command_ceremony_promotes_exact_validated_bundle(self):
        session, challenge = prepare_promotion(
            {"operation": "PROMOTE", "bundle": self.bundle},
            policy=self.policy, peer=self.peer, now=CLOCK,
        )
        promoted = confirm_promotion(
            session,
            {"challenge_id": challenge["challenge_id"], "confirmation": challenge["confirmation"]},
            now=CLOCK,
        )
        self.assertEqual(challenge["status"], "AWAITING_HUMAN_CONFIRMATION")
        self.assertEqual(challenge["principal"], "human-reviewer")
        self.assertEqual(promoted["status"], "PROMOTED")
        registry = load_skill_registry(self.state / "registry.json")
        self.assertIn(self.manifest["skill_id"], registry["skills"])
        receipts = list((self.state / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        with sqlite3.connect(self.state / "authorizations.sqlite3") as db:
            self.assertEqual(db.execute("SELECT state FROM promotion_challenges").fetchone()[0], "COMPLETE")
        with self.assertRaisesRegex(ProtectedOperatorError, "unknown, spent"):
            confirm_promotion(
                session,
                {"challenge_id": challenge["challenge_id"], "confirmation": challenge["confirmation"]},
                now=CLOCK,
            )

    def test_expired_challenge_is_one_use_refusal(self):
        session, challenge = prepare_promotion(
            {"operation": "PROMOTE", "bundle": self.bundle},
            policy=self.policy, peer=self.peer, now=CLOCK,
        )
        response = {"challenge_id": challenge["challenge_id"], "confirmation": challenge["confirmation"]}
        with self.assertRaisesRegex(ProtectedOperatorError, "expired"):
            confirm_promotion(session, response, now=CLOCK + timedelta(seconds=301))
        with self.assertRaisesRegex(ProtectedOperatorError, "unknown, spent"):
            confirm_promotion(session, response, now=CLOCK + timedelta(seconds=301))

    def test_token_without_approve_prefix_is_refused_and_bounded(self):
        challenge, responses, errors = self.exchange(
            lambda c: [c["confirmation"].removeprefix("APPROVE "), "no", "still-no"]
        )
        self.assertEqual([item["status"] for item in responses], ["MISMATCH", "MISMATCH", "REFUSED"])
        self.assertIsInstance(errors[-1], ProtectedOperatorError)
        registry = load_skill_registry(self.state / "registry.json")
        self.assertEqual(registry["skills"], {})
        with sqlite3.connect(self.state / "authorizations.sqlite3") as db:
            self.assertEqual(db.execute("SELECT state FROM promotion_challenges").fetchone()[0], "DENIED")

    def test_changed_registry_after_challenge_refuses_without_promotion(self):
        session, challenge = prepare_promotion(
            {"operation": "PROMOTE", "bundle": self.bundle},
            policy=self.policy, peer=self.peer, now=CLOCK,
        )
        changed = create_skill_registry(created_at="2026-10-16T04:00:01Z")
        save_skill_registry(changed, self.state / "registry.json")
        with self.assertRaisesRegex(ProtectedOperatorError, "registry changed"):
            confirm_promotion(
                session,
                {"challenge_id": challenge["challenge_id"], "confirmation": challenge["confirmation"]},
                now=CLOCK,
            )

    def test_promotion_lock_is_operator_only(self):
        with _promotion_lock(self.state):
            info = (self.state / "promotion.lock").lstat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)

    def test_bundle_decoder_refuses_traversal_and_tampering(self):
        broken = dict(self.bundle)
        broken["files"] = {"../manifest.json": base64.b64encode(b"{}").decode("ascii")}
        with self.assertRaisesRegex(ProtectedOperatorError, "unsafe"):
            decode_bundle(broken)
        broken = dict(self.bundle)
        broken["lifecycle"] = "not-base64!"
        with self.assertRaisesRegex(ProtectedOperatorError, "lifecycle"):
            decode_bundle(broken)

    def test_human_client_bundle_is_exact_and_symlink_safe(self):
        bundle_root = self.root / "bundle"
        package = bundle_root / "package"
        package.mkdir(parents=True)
        for name, payload in self.files.items():
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        (bundle_root / "lifecycle.validated.json").write_text(json.dumps(self.lifecycle), encoding="utf-8")
        encoded = snapshot_bundle(str(bundle_root))
        decoded, lifecycle, _digest = decode_bundle(encoded)
        self.assertEqual(decoded, self.files)
        self.assertEqual(lifecycle, self.lifecycle)
        linked = package / "linked"
        try:
            linked.symlink_to(package / "manifest.json")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ProtectedOperatorError, "symlink"):
            snapshot_bundle(str(bundle_root))

    def test_kernel_peer_credentials_refuse_agent_operator_and_root(self):
        class FakeConnection:
            def __init__(self, uid):
                self.uid = uid
            def getsockopt(self, *_args):
                return struct.pack("3i", 4321, self.uid, self.uid)

        for uid in (0, 2001, 2002):
            with self.assertRaisesRegex(ProtectedOperatorError, "distinct non-root human"):
                peer_identity(FakeConnection(uid), operator_uid=2002, agent_uid=2001)
        with patch("knowledge_forge.protected_operator_service.pwd.getpwuid",
                   return_value=SimpleNamespace(pw_name="alice")):
            peer = peer_identity(FakeConnection(1000), operator_uid=2002, agent_uid=2001)
        self.assertEqual((peer.pid, peer.uid, peer.principal), (4321, 1000, "alice"))

    def test_protected_surface_is_not_added_to_ordinary_agent_installer(self):
        repo = Path(__file__).resolve().parents[1]
        installer = (repo / "bin" / "knowledge-forge-install-agent").read_text(encoding="utf-8")
        self.assertNotIn("knowledge-forge-promote", installer)
        self.assertNotIn("knowledge-forge-protected-operator", installer)
        protected = (repo / "bin" / "knowledge-forge-protected-operator").read_text(encoding="utf-8")
        client = (repo / "bin" / "knowledge-forge-promote").read_text(encoding="utf-8")
        for source in (protected, client):
            self.assertIn("/opt/toadaid-kf-operator", source)
            self.assertIn("python3 -I -B", source)
            self.assertNotIn("PYTHONPATH=", source)

    def test_systemd_socket_is_human_group_only_and_service_is_hardened(self):
        repo = Path(__file__).resolve().parents[1]
        socket_unit = (repo / "deploy/linux-protected-operator/knowledge-forge-operator.socket").read_text()
        service_unit = (repo / "deploy/linux-protected-operator/knowledge-forge-operator@.service").read_text()
        self.assertIn("SocketGroup=kf-reviewers", socket_unit)
        self.assertIn("SocketMode=0660", socket_unit)
        self.assertIn("Accept=yes", socket_unit)
        self.assertIn("User=kf-operator", service_unit)
        self.assertIn("NoNewPrivileges=yes", service_unit)
        self.assertIn("ProtectSystem=strict", service_unit)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", service_unit)
        self.assertIn("RuntimeMaxSec=10min", service_unit)
        self.assertNotIn("kf-agent", socket_unit)


if __name__ == "__main__":
    unittest.main()
