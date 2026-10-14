"""P3B-P2B: ordinary-agent process isolation preflight, never authorization.

These observations are deliberately insufficient as sandbox attestation.
They never authorize a promotion or substitute for an independently protected
human host. A model can modify its own Python code and reporting path.
"""

from __future__ import annotations

from dataclasses import dataclass
import grp
import os
from pathlib import Path
import pwd
import re
import stat
from typing import Any


_NAME = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")
_UNSAFE_GROUPS = frozenset({
    "sudo", "wheel", "docker", "podman", "lxd", "libvirt", "libvirt-qemu",
    "disk", "shadow", "adm", "systemd-journal",
})
_UNSAFE_ENV_KEYS = frozenset({
    "SUDO_USER", "SUDO_UID", "SUDO_COMMAND", "SUDO_ASKPASS",
    "DOCKER_HOST", "DOCKER_CERT_PATH", "CONTAINER_HOST", "PODMAN_HOST",
    "SSH_AUTH_SOCK", "SSH_ASKPASS", "GH_TOKEN", "GITHUB_TOKEN",
    "AWS_ACCESS_KEY_ID", "GOOGLE_APPLICATION_CREDENTIALS", "KUBECONFIG",
})
_CONTAINER_SOCKETS = (Path("/run/docker.sock"), Path("/var/run/docker.sock"))


class AgentRuntimeGateError(ValueError):
    """An ordinary agent process has not met the observed safety baseline."""


@dataclass(frozen=True)
class AgentRuntimePolicy:
    """Expected local layout, not a credential or positive host attestation."""

    agent_user: str
    operator_user: str
    operator_state_root: str


def _uid(name: str) -> int:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise AgentRuntimeGateError("invalid bounded POSIX account label")
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError as exc:
        raise AgentRuntimeGateError("account not provisioned: " + name) from exc


def _effective_caps() -> int:
    """Linux capability effective set; missing data is a refusal, not success."""
    try:
        with open("/proc/self/status", "rt", encoding="ascii") as handle:
            for line in handle:
                if line.startswith("CapEff:"):
                    return int(line.split(":", 1)[1].strip(), 16)
    except (OSError, ValueError) as exc:
        raise AgentRuntimeGateError("cannot examine current Linux capabilities") from exc
    raise AgentRuntimeGateError("CapEff not available; cannot validate Linux process")


def _group_names() -> list[str]:
    gids = set(os.getgroups()) | {os.getgid(), os.getegid()}
    result = []
    for gid in sorted(gids):
        try:
            result.append(grp.getgrgid(gid).gr_name)
        except KeyError as exc:
            raise AgentRuntimeGateError("unresolved supplementary group") from exc
    return sorted(set(result))


def _protected_state(path_value: str, operator_uid: int) -> str:
    if not isinstance(path_value, str) or len(path_value) > 1024 or not path_value:
        raise AgentRuntimeGateError("invalid protected operator state path")
    path = Path(path_value)
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise AgentRuntimeGateError("protected operator state needs an absolute safe path")
    for part in reversed((path, *path.parents)):
        try:
            node = part.lstat()
        except OSError as exc:
            raise AgentRuntimeGateError("protected operator state is missing or inaccessible") from exc
        if not stat.S_ISDIR(node.st_mode):
            raise AgentRuntimeGateError("protected operator state path contains symlink or non-directory")
        if node.st_uid not in (0, operator_uid) or (node.st_mode & 0o022):
            raise AgentRuntimeGateError("protected operator state ancestry is not protected")
        if part == path and (node.st_uid != operator_uid or node.st_mode & 0o077):
            raise AgentRuntimeGateError("operator state must be operator-owned and mode 0700 or tighter")
    if any(os.access(path, permission) for permission in (os.R_OK, os.W_OK, os.X_OK)):
        raise AgentRuntimeGateError("ordinary agent can access operator state")
    return str(path)


def _accessible_container_socket() -> bool:
    for path in _CONTAINER_SOCKETS:
        try:
            node = path.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise AgentRuntimeGateError("cannot examine local container socket") from exc
        if stat.S_ISSOCK(node.st_mode) and os.access(path, os.W_OK):
            return True
    return False


def inspect_ordinary_agent_runtime(policy: AgentRuntimePolicy) -> dict[str, Any]:
    """Observe this process; never attest to an agent sandbox or grant privilege.

    Only a separate trusted supervisor can establish process launch boundaries.
    Running these checks from model-modifiable Python is not independent proof.
    """
    if not isinstance(policy, AgentRuntimePolicy):
        raise AgentRuntimeGateError("host policy must be an AgentRuntimePolicy")
    agent_uid = _uid(policy.agent_user)
    operator_uid = _uid(policy.operator_user)
    if agent_uid == 0 or operator_uid == 0 or agent_uid == operator_uid:
        raise AgentRuntimeGateError("agent and operator must be separate non-root UIDs")
    if os.getuid() != agent_uid or os.geteuid() != agent_uid:
        raise AgentRuntimeGateError("process does not run as selected unprivileged agent UID")
    groups = _group_names()
    dangerous = sorted(set(groups) & _UNSAFE_GROUPS)
    if dangerous:
        raise AgentRuntimeGateError("agent has privileged groups: " + ",".join(dangerous))
    caps = _effective_caps()
    if caps:
        raise AgentRuntimeGateError("agent process has effective Linux capabilities")
    inherited = sorted(key for key in _UNSAFE_ENV_KEYS if os.environ.get(key))
    if inherited:
        # Never print environment values, including passwords or tokens.
        raise AgentRuntimeGateError("agent inherited host-capable environment keys: " + ",".join(inherited))
    if _accessible_container_socket():
        raise AgentRuntimeGateError("agent can write to Docker daemon socket")
    state = _protected_state(policy.operator_state_root, operator_uid)
    return {
        "schema_version": "0.1",
        "status": "AGENT_RUNTIME_OBSERVATION_ONLY_NOT_SANDBOX_ATTESTATION",
        "agent_user": policy.agent_user,
        "agent_uid": agent_uid,
        "operator_user": policy.operator_user,
        "operator_uid": operator_uid,
        "operator_state_root": state,
        "privileged_groups_observed": [],
        "effective_capabilities_observed": 0,
        "host_credential_keys_observed": [],
        "container_socket_access_observed": False,
        "host_authentication_verified": False,
        "sandbox_integrity_verified": False,
        "authorization_granted": False,
        "promotion_authorized": False,
        "operator_cli_invoked": False,
        "production_registry_mutated": False,
        "execution_authority": "NONE",
        "tool_grants": [],
        "warning": "Self-observed diagnostics only; host-owned launch and real human authentication remain unproven.",
    }
