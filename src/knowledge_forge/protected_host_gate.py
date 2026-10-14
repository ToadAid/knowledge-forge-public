"""P3B-P2A: inspect a prospective operator OS boundary, never authenticate.

This is a *diagnostic* security gate, not a trusted host, human-authentication
ceremony, authorization issuer, one-time token store, or promotion entry point.
A privileged host must independently own its policy and isolated runtime.
The agent may execute this diagnostic, but its output grants no authority.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import pwd
import re
import stat
from typing import Any


_USER = re.compile(r"^[a-z_][a-z0-9_-]{0,31}\$?$")


class ProtectedHostGateError(ValueError):
    """A prospective protected operator boundary is not acceptable."""


@dataclass(frozen=True)
class ProtectedHostLayout:
    """Host-selected expectations; fields themselves are NOT credentials."""

    agent_user: str
    operator_user: str
    operator_code_root: str
    operator_state_root: str


def _uid(username: str) -> int:
    if not isinstance(username, str) or not _USER.fullmatch(username):
        raise ProtectedHostGateError("username must be a bounded local POSIX name")
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError as exc:
        raise ProtectedHostGateError(f"local OS account not provisioned: {username}") from exc


def _absolute_directory_path(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ProtectedHostGateError(f"{label} must be an absolute bounded path")
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts or '\x00' in value:
        raise ProtectedHostGateError(f"{label} must be an absolute non-traversal path")
    normalized = Path(os.path.normpath(value))
    if normalized == Path('/'):
        raise ProtectedHostGateError(f"{label} must not be the filesystem root")
    return normalized


def _inspect_ancestry(path: Path, label: str, operator_uid: int) -> None:
    """Fail on symlinks, other owners, or group/world-writable ancestors.

    Read-only observations are still TOCTOU-prone: this MUST NOT be treated as
    proof that a later operation is safe. A real host must use anchored fds,
    protected mounting, and separately controlled permissions at execution.
    """
    for node in reversed((path, *path.parents)):
        try:
            record = node.lstat()
        except OSError as exc:
            raise ProtectedHostGateError(f"{label} directory missing/inaccessible: {node}") from exc
        if not stat.S_ISDIR(record.st_mode):
            raise ProtectedHostGateError(f"{label} path contains a non-directory or symlink: {node}")
        if record.st_uid not in (0, operator_uid):
            raise ProtectedHostGateError(f"{label} path is not root/operator-owned: {node}")
        if record.st_mode & 0o022:
            raise ProtectedHostGateError(f"{label} path is group/world writable: {node}")


def inspect_prospective_operator_boundary(layout: ProtectedHostLayout) -> dict[str, Any]:
    """Read-only inspection; successful output is NEVER human authentication.

    There are deliberately no injectable UID/mode parameters: inspect the
    *actual calling process and filesystem*. A user-controlled invocation or
    mutable Python import path cannot turn this diagnostic into authorization.
    """
    if not isinstance(layout, ProtectedHostLayout):
        raise ProtectedHostGateError("host layout must be a ProtectedHostLayout")
    agent_uid = _uid(layout.agent_user)
    operator_uid = _uid(layout.operator_user)
    if agent_uid == 0 or operator_uid == 0:
        raise ProtectedHostGateError("root must not be an agent or operator identity")
    if agent_uid == operator_uid:
        raise ProtectedHostGateError("agent and operator share the same UID")
    if os.geteuid() != operator_uid:
        raise ProtectedHostGateError("diagnostic is not running as the distinct operator UID")

    code = _absolute_directory_path(layout.operator_code_root, 'operator code')
    state = _absolute_directory_path(layout.operator_state_root, 'operator state')
    if code == state or code in state.parents or state in code.parents:
        raise ProtectedHostGateError("operator code and mutable state roots must be separate")
    _inspect_ancestry(code, 'operator code', operator_uid)
    _inspect_ancestry(state, 'operator state', operator_uid)
    code_stat = code.lstat()
    state_stat = state.lstat()
    if code_stat.st_uid != 0:
        raise ProtectedHostGateError("operator code root must be root-owned, not agent/operator writable")
    if state_stat.st_uid != operator_uid or state_stat.st_mode & 0o077:
        raise ProtectedHostGateError("operator state root must be operator-owned and mode 0700 or stricter")

    return {
        'schema_version': '0.1',
        'status': 'OS_LAYOUT_PRECHECK_ONLY_NOT_AUTHENTICATION',
        'agent_user': layout.agent_user,
        'agent_uid': agent_uid,
        'operator_user': layout.operator_user,
        'operator_uid': operator_uid,
        'operator_code_root': str(code),
        'operator_state_root': str(state),
        'distinct_uid_observed': True,
        'directory_ownership_and_modes_observed': True,
        'trusted_code_integrity_verified': False,
        'independent_human_authentication_verified': False,
        'host_authentication_verified': False,
        'authorization_granted': False,
        'promotion_authorized': False,
        'one_time_decision_consumed': False,
        'operator_cli_invoked': False,
        'production_registry_mutated': False,
        'tool_grants': [],
        'execution_authority': 'NONE',
        'warning': (
            'Read-only, race-prone OS layout inspection only. No human identity, '
            'operator authentication, one-time authorization, protected audit '
            'or promotion has been established.'
        ),
    }
