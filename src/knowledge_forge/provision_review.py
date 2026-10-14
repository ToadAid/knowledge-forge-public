"""P3B-P2B2A: TNG provisioning review, never a provisioning authority.

Read-only observations are agent-modifiable and NOT independent host
attestation. No privileged operations, mutation, approval, or promotion live
in this module. An independently authenticated human must own provisioning.
"""
from __future__ import annotations

from hashlib import sha256
import grp
import json
import os
from pathlib import Path
import pwd
import socket
import stat
from typing import Any


PLAN_KIND = "TNG_PROVISIONING_PLAN_FOR_HUMAN_REVIEW_NOT_AUTHORIZATION"
EXPECTED_HOST = "TNG"
USERS = (
    ("kf-agent", "/var/lib/toadaid-kf-agent"),
    ("kf-operator", "/var/lib/toadaid-kf-operator"),
)
PATHS = (
    ("agent_home", "/var/lib/toadaid-kf-agent", "kf-agent", 0o700),
    ("operator_code", "/opt/toadaid-kf-operator", "root", 0o755),
    ("operator_state", "/var/lib/toadaid-kf-operator", "kf-operator", 0o700),
)
UNSAFE_GROUPS = frozenset({
    "sudo", "wheel", "docker", "podman", "lxd", "libvirt", "libvirt-qemu",
    "disk", "shadow", "adm", "systemd-journal",
})
EXPECTED_SHELLS = frozenset({"/usr/sbin/nologin", "/sbin/nologin"})


class ProvisionReviewError(ValueError):
    """Read-only review observations were incomplete or on the wrong host."""


def _account(name: str, expected_home: str) -> dict[str, Any]:
    try:
        record = pwd.getpwnam(name)
    except KeyError:
        return {"user": name, "status": "ABSENT", "uid": None,
                "expected_home": expected_home, "groups": [], "findings": []}
    if record.pw_uid == 0:
        raise ProvisionReviewError(f"unsafe root UID assigned to {name}")
    try:
        gids = sorted(set(os.getgrouplist(name, record.pw_gid)))
        groups = sorted(set(grp.getgrgid(gid).gr_name for gid in gids))
    except (OSError, KeyError) as exc:
        raise ProvisionReviewError(f"cannot determine complete groups for {name}") from exc
    findings: list[str] = []
    if record.pw_dir != expected_home:
        findings.append("UNEXPECTED_HOME")
    if record.pw_shell not in EXPECTED_SHELLS:
        findings.append("UNEXPECTED_LOGIN_SHELL")
    if sorted(set(groups) & UNSAFE_GROUPS):
        findings.append("PRIVILEGED_GROUPS")
    return {"user": name, "status": "EXISTS_REQUIRES_INDEPENDENT_ATTESTATION",
            "uid": record.pw_uid, "expected_home": expected_home,
            "groups": groups, "findings": findings}


def _path(label: str, target: str, expected_owner: str, expected_mode: int,
          accounts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    path = Path(target)
    observation: dict[str, Any] = {"label": label, "path": target,
                                   "expected_owner": expected_owner,
                                   "expected_mode": f"{expected_mode:04o}"}
    ancestry_findings = []
    for parent in path.parents:
        try:
            ancestor = parent.lstat()
        except OSError as exc:
            raise ProvisionReviewError(f"cannot inspect {label} ancestry") from exc
        if not stat.S_ISDIR(ancestor.st_mode) or ancestor.st_mode & 0o022:
            ancestry_findings.append("UNSAFE_ANCESTOR")
            break
        if ancestor.st_uid != 0:
            ancestry_findings.append("NON_ROOT_ANCESTOR_REQUIRES_REVIEW")
            break
    try:
        record = path.lstat()
    except FileNotFoundError:
        return {**observation, "status": "ABSENT", "findings": ancestry_findings}
    except OSError as exc:
        raise ProvisionReviewError(f"unable to inspect {label}") from exc
    findings = list(ancestry_findings)
    if not stat.S_ISDIR(record.st_mode):
        findings.append("NOT_REAL_DIRECTORY_OR_SYMLINK")
    if record.st_mode & 0o022:
        findings.append("GROUP_OR_WORLD_WRITABLE")
    uid = 0 if expected_owner == "root" else accounts[expected_owner]["uid"]
    if uid is None or record.st_uid != uid:
        findings.append("UNEXPECTED_OWNER")
    if stat.S_IMODE(record.st_mode) != expected_mode:
        findings.append("UNEXPECTED_MODE")
    return {**observation, "status": "PRESENT_REQUIRES_INDEPENDENT_ATTESTATION",
            "observed_uid": record.st_uid,
            "observed_mode": f"{stat.S_IMODE(record.st_mode):04o}",
            "findings": sorted(set(findings))}


def inspect_tng_provisioning_plan() -> dict[str, Any]:
    """Observe current TNG account/path layout; NEVER execute a plan.

    The source and report are under an ordinary agent UID and can be forged.
    Protected OS state must be checked anew by a separate human-owned process.
    """
    if socket.gethostname().split(".")[0] != EXPECTED_HOST:
        raise ProvisionReviewError("TNG only: host identity mismatch")
    accounts = {name: _account(name, home) for name, home in USERS}
    ids = [v["uid"] for v in accounts.values() if v["uid"] is not None]
    if len(ids) != len(set(ids)):
        raise ProvisionReviewError("account UID collision")
    paths = [_path(label, path, owner, mode, accounts)
             for label, path, owner, mode in PATHS]
    policy = {
        "target_host": EXPECTED_HOST,
        "accounts": [{"name": name, "home": home, "shell": "/usr/sbin/nologin",
                      "privileged_groups": "NONE"} for name, home in USERS],
        "paths": [{"label": label, "path": path, "owner": owner,
                   "mode": f"{mode:04o}"} for label, path, owner, mode in PATHS],
        "human_session": "INDEPENDENT_NON_AGENT_CONTROLLED_ADMIN_SESSION_ONLY",
        "operator_implementation": "REVIEWED_PINNED_ROOT_OWNED_CODE_ONLY",
        "operator_activation": "PROHIBITED_UNTIL_P2C_AND_P3_VERIFIED",
    }
    digest = sha256((json.dumps(policy, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()
    incomplete = (any(v["status"] == "ABSENT" for v in accounts.values()) or
                  any(v["status"] == "ABSENT" for v in paths))
    deviations = (any(v["findings"] for v in accounts.values()) or
                  any(v["findings"] for v in paths))
    return {
        "schema_version": "0.1", "plan_kind": PLAN_KIND,
        "status": "HUMAN_REVIEW_REQUIRED_NO_PROVISIONING_PERFORMED",
        "policy_sha256": "sha256:" + digest,
        "target_host": EXPECTED_HOST,
        "policy": policy, "observed_accounts": list(accounts.values()),
        "observed_paths": paths,
        "missing_resources_observed": incomplete,
        "unexpected_layout_observed": deviations,
        "independent_host_attestation_verified": False,
        "human_authentication_verified": False,
        "agent_sandbox_integrity_verified": False,
        "operator_provisioned_by_this_cut": False,
        "account_creation_authorized": False,
        "operator_activation_authorized": False,
        "approval_authorized": False, "promotion_authorized": False,
        "operator_cli_invoked": False, "registry_modified": False,
        "commands_executed": 0, "tool_grants": [], "execution_authority": "NONE",
        "warning": "Agent-readable review evidence only. Not authentication, independent attestation, or approval. No administrative action is permitted by this plan.",
    }
