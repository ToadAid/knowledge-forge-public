"""P3B-P2B2A: synthetic pre-provisioning review remains zero authority."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from knowledge_forge.provision_review import (
    EXPECTED_HOST, PATHS, PLAN_KIND, ProvisionReviewError,
    inspect_tng_provisioning_plan,
)


class ProvisionReviewTests(unittest.TestCase):
    def model(self, *, hostname="TNG", accounts=(), account_override=None,
              present_paths=(), path_override=None, ancestor_override=None):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch("knowledge_forge.provision_review.socket.gethostname", return_value=hostname))
        uidmap = {"kf-agent": 2001, "kf-operator": 2002}
        def fake_account(name):
            if name not in accounts:
                raise KeyError(name)
            settings = (account_override or {}).get(name, {})
            return SimpleNamespace(pw_uid=settings.get("uid", uidmap[name]),
                                   pw_gid=2001 if name == "kf-agent" else 2002,
                                   pw_dir=settings.get("home", "/var/lib/toadaid-" + name),
                                   pw_shell=settings.get("shell", "/usr/sbin/nologin"))
        stack.enter_context(patch("knowledge_forge.provision_review.pwd.getpwnam", side_effect=fake_account))
        def groups(name, primary):
            if name == "kf-agent" and (account_override or {}).get(name, {}).get("sudo"):
                return [primary, 27]
            return [primary]
        stack.enter_context(patch("knowledge_forge.provision_review.os.getgrouplist", side_effect=groups))
        stack.enter_context(patch("knowledge_forge.provision_review.grp.getgrgid", side_effect=lambda gid: SimpleNamespace(gr_name={2001:"kf-agent",2002:"kf-operator",27:"sudo"}[gid])))
        expected_paths = {label: (p, owner, mode) for label, p, owner, mode in PATHS}
        def lstat(p):
            path = Path(p)
            found = next(((label, *attrs) for label, attrs in expected_paths.items() if Path(attrs[0]) == path), None)
            if found:
                label, _str, owner, mode = found
                if label not in present_paths:
                    raise FileNotFoundError(str(p))
                cfg = (path_override or {}).get(label, {})
                default_uid = 0 if owner == "root" else uidmap[owner]
                filetype = stat.S_IFLNK if cfg.get("symlink") else stat.S_IFDIR
                return SimpleNamespace(st_mode=filetype | cfg.get("mode", mode), st_uid=cfg.get("uid", default_uid))
            cfg = (ancestor_override or {}).get(str(path), {})
            filetype = stat.S_IFLNK if cfg.get("symlink") else stat.S_IFDIR
            return SimpleNamespace(st_mode=filetype | cfg.get("mode", 0o755), st_uid=cfg.get("uid", 0))
        stack.enter_context(patch("knowledge_forge.provision_review.Path.lstat", new=lstat))

    def test_tng_only(self):
        self.model(hostname="Dell7920")
        with self.assertRaisesRegex(ProvisionReviewError, "TNG only"):
            inspect_tng_provisioning_plan()

    def test_empty_host_shows_missing_resources_no_authority(self):
        self.model()
        out = inspect_tng_provisioning_plan()
        self.assertEqual(out["plan_kind"], PLAN_KIND)
        self.assertTrue(out["missing_resources_observed"])
        self.assertFalse(out["unexpected_layout_observed"])
        self.assertTrue(all(x["status"] == "ABSENT" for x in out["observed_accounts"]))
        self.assertTrue(all(x["status"] == "ABSENT" for x in out["observed_paths"]))

    def test_stable_plan_digest(self):
        self.model()
        a = inspect_tng_provisioning_plan()
        b = inspect_tng_provisioning_plan()
        self.assertEqual(a["policy_sha256"], b["policy_sha256"])
        self.assertRegex(a["policy_sha256"], r"^sha256:[0-9a-f]{64}$")

    def test_no_authority_flags_always_false(self):
        self.model()
        out = inspect_tng_provisioning_plan()
        for key in ("independent_host_attestation_verified", "human_authentication_verified",
                    "agent_sandbox_integrity_verified", "operator_provisioned_by_this_cut",
                    "account_creation_authorized", "operator_activation_authorized",
                    "approval_authorized", "promotion_authorized", "operator_cli_invoked",
                    "registry_modified"):
            self.assertIs(out[key], False)
        self.assertEqual(out["execution_authority"], "NONE")
        self.assertEqual(out["commands_executed"], 0)
        self.assertEqual(out["tool_grants"], [])

    def test_fixed_tng_accounts_and_paths(self):
        self.model()
        o = inspect_tng_provisioning_plan()
        self.assertEqual([x["name"] for x in o["policy"]["accounts"]], ["kf-agent", "kf-operator"])
        self.assertEqual([x["path"] for x in o["policy"]["paths"]], [
            "/var/lib/toadaid-kf-agent", "/opt/toadaid-kf-operator", "/var/lib/toadaid-kf-operator"])

    def test_partial_account_presence_is_not_approved(self):
        self.model(accounts=("kf-agent",))
        o = inspect_tng_provisioning_plan()
        self.assertEqual(o["observed_accounts"][0]["status"], "EXISTS_REQUIRES_INDEPENDENT_ATTESTATION")
        self.assertTrue(o["missing_resources_observed"])
        self.assertFalse(o["account_creation_authorized"])

    def test_root_account_refused(self):
        self.model(accounts=("kf-agent",), account_override={"kf-agent":{"uid":0}})
        with self.assertRaisesRegex(ProvisionReviewError, "root UID"):
            inspect_tng_provisioning_plan()

    def test_duplicate_uid_refused(self):
        self.model(accounts=("kf-agent","kf-operator"),account_override={"kf-operator":{"uid":2001}})
        with self.assertRaisesRegex(ProvisionReviewError, "UID collision"):
            inspect_tng_provisioning_plan()

    def test_wrong_home_and_shell_flagged(self):
        self.model(accounts=("kf-agent",),account_override={"kf-agent":{"home":"/tmp/bad","shell":"/bin/bash"}})
        o = inspect_tng_provisioning_plan()
        self.assertEqual(o["observed_accounts"][0]["findings"], ["UNEXPECTED_HOME","UNEXPECTED_LOGIN_SHELL"])

    def test_privileged_group_flagged(self):
        self.model(accounts=("kf-agent",), account_override={"kf-agent":{"sudo":True}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("PRIVILEGED_GROUPS",o["observed_accounts"][0]["findings"])

    def test_safe_present_layout_still_not_authenticated(self):
        self.model(accounts=("kf-agent","kf-operator"),present_paths=("agent_home","operator_code","operator_state"))
        o = inspect_tng_provisioning_plan()
        self.assertFalse(o["missing_resources_observed"])
        self.assertFalse(o["unexpected_layout_observed"])
        self.assertFalse(o["human_authentication_verified"])

    def test_symlink_operator_code_flagged(self):
        self.model(accounts=("kf-agent","kf-operator"),present_paths=("operator_code",),path_override={"operator_code":{"symlink":True}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("NOT_REAL_DIRECTORY_OR_SYMLINK",o["observed_paths"][1]["findings"])

    def test_agent_owned_code_flagged(self):
        self.model(accounts=("kf-agent",),present_paths=("operator_code",),path_override={"operator_code":{"uid":2001}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("UNEXPECTED_OWNER",o["observed_paths"][1]["findings"])

    def test_world_writable_state_flagged(self):
        self.model(accounts=("kf-operator",),present_paths=("operator_state",),path_override={"operator_state":{"mode":0o777}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("GROUP_OR_WORLD_WRITABLE",o["observed_paths"][2]["findings"])

    def test_wrong_mode_flagged(self):
        self.model(accounts=("kf-operator",),present_paths=("operator_state",),path_override={"operator_state":{"mode":0o750}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("UNEXPECTED_MODE",o["observed_paths"][2]["findings"])

    def test_unsafe_parent_flagged(self):
        self.model(accounts=("kf-operator",),present_paths=("operator_state",),ancestor_override={"/var/lib":{"mode":0o777}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("UNSAFE_ANCESTOR",o["observed_paths"][2]["findings"])

    def test_missing_target_unsafe_ancestor_still_flagged(self):
        self.model(ancestor_override={"/var/lib":{"symlink":True}})
        o = inspect_tng_provisioning_plan()
        self.assertTrue(o["missing_resources_observed"])
        self.assertTrue(o["unexpected_layout_observed"])
        self.assertIn("UNSAFE_ANCESTOR", o["observed_paths"][2]["findings"])

    def test_ancestor_symlink_flagged(self):
        self.model(accounts=("kf-operator",),present_paths=("operator_state",),ancestor_override={"/var/lib":{"symlink":True}})
        o = inspect_tng_provisioning_plan()
        self.assertIn("UNSAFE_ANCESTOR",o["observed_paths"][2]["findings"])


if __name__ == '__main__':
    unittest.main()
