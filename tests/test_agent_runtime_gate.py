"""P3B-P2B: adversarial, synthetic ordinary-agent privilege checks."""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import os
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from _platform_support import skip_linux_account_module_on_windows
skip_linux_account_module_on_windows('agent_runtime_gate')

from knowledge_forge.agent_runtime_gate import (
    AgentRuntimeGateError, AgentRuntimePolicy, inspect_ordinary_agent_runtime,
)


class AgentRuntimeGateTests(unittest.TestCase):
    def setUp(self):
        self.layout = AgentRuntimePolicy("kf-agent", "kf-operator", "/var/lib/toadaid-kf-operator")

    def model(self, *, uid=2001, euid=2001, gids=(2001,), caps=0,
              env=None, socket=False, state_uid=2002, state_mode=0o700,
              accessible_state=False, ancestor_mode=0o755, ancestor_link=False):
        stack = ExitStack()
        self.addCleanup(stack.close)
        names = {"kf-agent": 2001, "kf-operator": 2002, "root": 0}
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.pwd.getpwnam",
                                  side_effect=lambda name: SimpleNamespace(pw_uid=names[name])))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.getuid", return_value=uid))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.geteuid", return_value=euid))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.getgid", return_value=2001))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.getegid", return_value=2001))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.getgroups", return_value=list(gids)))
        group_names = {2001: "kf-agent", 2002: "kf-operator", 27: "sudo", 999: "docker", 6: "disk"}
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.grp.getgrgid",
                                  side_effect=lambda gid: SimpleNamespace(gr_name=group_names[gid])))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate._effective_caps", return_value=caps))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate._accessible_container_socket", return_value=socket))
        stack.enter_context(patch.dict("os.environ", env or {}, clear=True))
        target = Path(self.layout.operator_state_root)
        def faked_lstat(path):
            p = Path(path)
            mode = state_mode if p == target else ancestor_mode
            owner = state_uid if p == target else 0
            typ = stat.S_IFLNK if (ancestor_link and p == target.parent) else stat.S_IFDIR
            return SimpleNamespace(st_mode=typ | mode, st_uid=owner)
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.Path.lstat", new=faked_lstat))
        stack.enter_context(patch("knowledge_forge.agent_runtime_gate.os.access", return_value=accessible_state))

    def test_clean_synthetic_observation_is_never_authentication(self):
        self.model()
        outcome = inspect_ordinary_agent_runtime(self.layout)
        self.assertEqual(outcome["status"], "AGENT_RUNTIME_OBSERVATION_ONLY_NOT_SANDBOX_ATTESTATION")
        for key in ("host_authentication_verified", "sandbox_integrity_verified",
                    "authorization_granted", "promotion_authorized", "operator_cli_invoked",
                    "production_registry_mutated"):
            self.assertIs(outcome[key], False)
        self.assertEqual(outcome["tool_grants"], [])

    def test_caller_dict_is_not_a_host_policy(self):
        with self.assertRaisesRegex(AgentRuntimeGateError, "AgentRuntimePolicy"):
            inspect_ordinary_agent_runtime({"verified_by_host": True})

    def test_same_uid_and_root_operator_refused(self):
        self.model()
        with self.assertRaisesRegex(AgentRuntimeGateError, "non-root"):
            inspect_ordinary_agent_runtime(replace(self.layout, operator_user="kf-agent"))
        with self.assertRaisesRegex(AgentRuntimeGateError, "non-root"):
            inspect_ordinary_agent_runtime(replace(self.layout, operator_user="root"))

    def test_process_wrong_real_or_effective_uid_refused(self):
        self.model(uid=1000)
        with self.assertRaisesRegex(AgentRuntimeGateError, "process does not run"):
            inspect_ordinary_agent_runtime(self.layout)
        self.model(euid=0)
        with self.assertRaisesRegex(AgentRuntimeGateError, "process does not run"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_sudo_group_refused(self):
        self.model(gids=(2001, 27))
        with self.assertRaisesRegex(AgentRuntimeGateError, "sudo"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_docker_group_refused(self):
        self.model(gids=(2001, 999))
        with self.assertRaisesRegex(AgentRuntimeGateError, "docker"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_disk_group_refused(self):
        self.model(gids=(2001, 6))
        with self.assertRaisesRegex(AgentRuntimeGateError, "disk"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_effective_capabilities_refused(self):
        self.model(caps=0x200000)
        with self.assertRaisesRegex(AgentRuntimeGateError, "capabilities"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_inherited_host_credential_names_refused_without_values(self):
        marker = "SECRET_MUST_NOT_APPEAR"
        self.model(env={"GH_TOKEN": marker, "DOCKER_HOST": marker})
        with self.assertRaises(AgentRuntimeGateError) as cm:
            inspect_ordinary_agent_runtime(self.layout)
        self.assertIn("GH_TOKEN", str(cm.exception))
        self.assertNotIn(marker, str(cm.exception))

    def test_container_socket_access_refused(self):
        self.model(socket=True)
        with self.assertRaisesRegex(AgentRuntimeGateError, "Docker daemon socket"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_readable_private_operator_state_refused(self):
        self.model(accessible_state=True)
        with self.assertRaisesRegex(AgentRuntimeGateError, "can access operator state"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_even_partial_operator_state_access_is_refused(self):
        self.model()
        with patch("knowledge_forge.agent_runtime_gate.os.access",
                   side_effect=lambda _path, mode: mode == os.X_OK):
            with self.assertRaisesRegex(AgentRuntimeGateError, "can access operator state"):
                inspect_ordinary_agent_runtime(self.layout)

    def test_operator_state_wrong_owner_or_mode_refused(self):
        self.model(state_uid=0)
        with self.assertRaisesRegex(AgentRuntimeGateError, "operator-owned"):
            inspect_ordinary_agent_runtime(self.layout)
        self.model(state_mode=0o750)
        with self.assertRaisesRegex(AgentRuntimeGateError, "operator-owned"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_writable_or_symlink_ancestor_refused(self):
        self.model(ancestor_mode=0o777)
        with self.assertRaisesRegex(AgentRuntimeGateError, "ancestry"):
            inspect_ordinary_agent_runtime(self.layout)
        self.model(ancestor_link=True)
        with self.assertRaisesRegex(AgentRuntimeGateError, "symlink"):
            inspect_ordinary_agent_runtime(self.layout)

    def test_missing_operator_account_refused(self):
        self.model()
        with self.assertRaisesRegex(AgentRuntimeGateError, "not provisioned"):
            inspect_ordinary_agent_runtime(replace(self.layout, operator_user="missing"))

    def test_relative_or_traversal_operator_state_refused(self):
        self.model()
        for value in ("relative", "/var/lib/../tmp", "/"):
            with self.assertRaises(AgentRuntimeGateError):
                inspect_ordinary_agent_runtime(replace(self.layout, operator_state_root=value))


if __name__ == "__main__":
    unittest.main()
