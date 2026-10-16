from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge.approval import ApprovalContractError
from knowledge_forge.inline_approval import (
    InlineApprovalError,
    _immutable_output_paths,
    complete_inline_promotion,
    prepare_inline_approval,
)
from knowledge_forge.registry import load_skill_registry
from knowledge_forge.secure_io import (
    SecureIOError,
    read_bounded_bytes,
    snapshot_regular_tree,
)
from tests.test_approval_host import APPROVE_AT, _Flow


class InlineApprovalTests(unittest.TestCase):
    def setUp(self):
        self.flow = _Flow()
        self.addCleanup(self.flow.teardown)

    def test_agent_prepares_inline_card_without_authority(self):
        card = prepare_inline_approval(
            self.flow.from_dir, self.flow.bundle, observed_at=APPROVE_AT
        )
        self.assertEqual(card["status"], "AWAITING_NATIVE_HOST_APPROVAL")
        self.assertEqual(card["actions"], [
            "APPROVE_AND_PROMOTE", "REJECT", "VIEW_DETAILS"
        ])
        self.assertFalse(card["chat_text_can_authorize"])
        self.assertFalse(card["agent_can_authorize"])
        self.assertFalse(card["agent_can_promote"])
        self.assertFalse(card["promotion_performed"])

    def test_signed_native_host_event_promotes_with_unique_outputs(self):
        result = complete_inline_promotion(
            self.flow.from_dir,
            self.flow.bundle,
            signed_host_decision=self.flow.decision(nonce="71" * 16),
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            now=APPROVE_AT,
        )
        self.assertTrue(result["promoted"])
        self.assertFalse(result["model_authorized"])
        self.assertFalse(result["user_command_required"])
        self.assertEqual(result["interaction"], "INLINE_NATIVE_HOST_APPROVAL")
        receipt = Path(result["paths"]["receipt"])
        lifecycle = Path(result["paths"]["promoted_lifecycle"])
        self.assertEqual(receipt.parent.name, "receipts")
        self.assertEqual(lifecycle.parent.name, "lifecycles")
        self.assertNotEqual(receipt.name, "receipt.json")
        self.assertNotEqual(lifecycle.name, "lifecycle.promoted.json")
        self.assertFalse(
            (self.flow.from_dir / "approval-host-decision.json").exists()
        )
        registry = load_skill_registry(self.flow.registry_path)
        self.assertIn(self.flow.manifest["skill_id"], registry["skills"])

    def test_two_distinct_skills_promote_sequentially_without_output_collision(self):
        first = complete_inline_promotion(
            self.flow.from_dir,
            self.flow.bundle,
            signed_host_decision=self.flow.decision(nonce="72" * 16),
            registry_path=self.flow.registry_path,
            host_key_path=self.flow.host_key_path,
            now=APPROVE_AT,
        )
        second_flow = _Flow(variant="operations")
        self.addCleanup(second_flow.teardown)
        second_flow.state = self.flow.state
        second_flow.registry_path = self.flow.registry_path
        second_flow.host_key = self.flow.host_key
        second_flow.host_key_path = self.flow.host_key_path
        second = complete_inline_promotion(
            second_flow.from_dir,
            second_flow.bundle,
            signed_host_decision=second_flow.decision(nonce="73" * 16),
            registry_path=second_flow.registry_path,
            host_key_path=second_flow.host_key_path,
            now=APPROVE_AT,
        )
        self.assertNotEqual(first["paths"]["receipt"], second["paths"]["receipt"])
        self.assertNotEqual(
            first["paths"]["promoted_lifecycle"],
            second["paths"]["promoted_lifecycle"],
        )
        self.assertTrue(Path(first["paths"]["receipt"]).is_file())
        self.assertTrue(Path(second["paths"]["receipt"]).is_file())
        registry = load_skill_registry(self.flow.registry_path)
        self.assertEqual(
            set(registry["skills"]),
            {self.flow.manifest["skill_id"], second_flow.manifest["skill_id"]},
        )

    def test_chat_approve_is_not_an_authorization_input(self):
        with self.assertRaisesRegex(InlineApprovalError, "signed decision"):
            complete_inline_promotion(
                self.flow.from_dir,
                self.flow.bundle,
                signed_host_decision="APPROVE",  # type: ignore[arg-type]
                registry_path=self.flow.registry_path,
                host_key_path=self.flow.host_key_path,
                now=APPROVE_AT,
            )

    def test_output_names_are_request_and_skill_bound(self):
        first = _immutable_output_paths(self.flow.registry_path, self.flow.request)
        other = deepcopy(self.flow.request)
        other["request_id"] = "approval-host-p1-0002"
        second = _immutable_output_paths(self.flow.registry_path, other)
        self.assertNotEqual(first, second)

    def test_agent_staged_json_symlink_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "outside.json"
            target.write_text("{}", encoding="utf-8")
            request_path = self.flow.from_dir / "approval-request.json"
            request_path.unlink()
            try:
                request_path.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaises((ApprovalContractError, ValueError)):
                prepare_inline_approval(
                    self.flow.from_dir, self.flow.bundle, observed_at=APPROVE_AT
                )

    def test_bounded_reader_refuses_file_growth_during_read(self):
        target = self.flow.root / "growing.bin"
        target.write_bytes(b"1234")
        original_read = os.read
        grew = False

        def grow_then_read(descriptor, amount):
            nonlocal grew
            chunk = original_read(descriptor, amount)
            if not grew:
                grew = True
                with target.open("ab") as handle:
                    handle.write(b"56789")
            return chunk

        with patch("knowledge_forge.secure_io.os.read", side_effect=grow_then_read):
            with self.assertRaisesRegex(SecureIOError, "exceeds|changed"):
                read_bounded_bytes(target, 8, "growing test file")

    def test_package_tree_entry_fanout_is_bounded(self):
        root = self.flow.root / "wide-package"
        root.mkdir()
        (root / "one").write_bytes(b"1")
        (root / "two").write_bytes(b"2")
        with self.assertRaisesRegex(SecureIOError, "entry limit"):
            snapshot_regular_tree(
                root,
                maximum_files=10,
                maximum_bytes=10,
                maximum_entries=1,
                label="test package",
            )

    def test_approval_json_size_is_bounded(self):
        request_path = self.flow.from_dir / "approval-request.json"
        request_path.write_text(
            json.dumps({"padding": "x" * (1024 * 1024)}), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "not readable JSON"):
            prepare_inline_approval(
                self.flow.from_dir, self.flow.bundle, observed_at=APPROVE_AT
            )


if __name__ == "__main__":
    unittest.main()
