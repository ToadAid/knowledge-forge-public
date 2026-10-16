"""Linux protected promotion service core.

This module is authority-bearing only when deployed as root-owned code, run as
the dedicated ``kf-operator`` account, and reached through a systemd-owned Unix
socket that excludes ``kf-agent``.  The generic agent installer never exposes
it.  Development imports and unit tests do not establish that boundary.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import pwd
import secrets
import socket
import sqlite3
import stat
import struct
from typing import Any, Mapping

from .candidate_preview import preview_validated_candidate
from .operator_promotion import (
    OperatorAuthorization,
    persist_operator_promotion,
    promote_validated_skill,
)
from .registry import load_skill_registry, registry_bytes


CONFIG_PATH = Path("/etc/toadaid-kf/operator.json")
SOCKET_PATH = Path("/run/toadaid-knowledge-forge/operator.sock")
MAX_FRAME_BYTES = 96 * 1024 * 1024
MAX_PACKAGE_FILES = 512
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
MAX_LIFECYCLE_BYTES = 2 * 1024 * 1024
CHALLENGE_SECONDS = 300
INITIAL_REQUEST_SECONDS = 30


class ProtectedOperatorError(ValueError):
    """A protected-host invariant or promotion ceremony failed closed."""


class ConfirmationMismatch(ProtectedOperatorError):
    def __init__(self, remaining: int):
        super().__init__("approval confirmation mismatch")
        self.remaining = remaining


@dataclass(frozen=True)
class OperatorPolicy:
    operator_user: str
    agent_user: str
    state_root: Path
    code_root: Path


@dataclass(frozen=True)
class PeerIdentity:
    pid: int
    uid: int
    gid: int
    principal: str


@dataclass(frozen=True)
class PromotionSession:
    challenge_id: str
    token: str
    peer: PeerIdentity
    files: Mapping[str, bytes]
    lifecycle: Mapping[str, Any]
    preview: Mapping[str, Any]
    registry_sha256: str
    policy: OperatorPolicy


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _sha(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_tree(path: Path, *, leaf_uid: int, leaf_mode: int, root_owned_leaf: bool = False) -> None:
    if not path.is_absolute() or ".." in path.parts or path == Path("/"):
        raise ProtectedOperatorError("protected path must be absolute and bounded")
    nodes = (path, *path.parents)
    for node in nodes:
        try:
            info = node.lstat()
        except OSError as exc:
            raise ProtectedOperatorError(f"protected path missing or unreadable: {node}") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise ProtectedOperatorError(f"protected path contains a symlink or non-directory: {node}")
        if info.st_mode & 0o022:
            raise ProtectedOperatorError(f"protected path is group/other writable: {node}")
        if node == path:
            expected_uid = 0 if root_owned_leaf else leaf_uid
            if info.st_uid != expected_uid or stat.S_IMODE(info.st_mode) != leaf_mode:
                raise ProtectedOperatorError(f"protected path owner/mode mismatch: {node}")
        elif info.st_uid not in (0, leaf_uid):
            raise ProtectedOperatorError(f"protected path ancestry owner mismatch: {node}")


def _load_policy() -> OperatorPolicy:
    path = CONFIG_PATH
    try:
        info = path.lstat()
    except OSError as exc:
        raise ProtectedOperatorError("protected operator configuration is unavailable") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ProtectedOperatorError("operator configuration must be root-owned and non-writable")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtectedOperatorError("operator configuration must be UTF-8 JSON") from exc
    expected = {"schema_version", "operator_user", "agent_user", "state_root", "code_root"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("schema_version") != "0.1":
        raise ProtectedOperatorError("operator configuration fields mismatch")
    return OperatorPolicy(
        operator_user=str(raw["operator_user"]),
        agent_user=str(raw["agent_user"]),
        state_root=Path(str(raw["state_root"])),
        code_root=Path(str(raw["code_root"])),
    )


def verify_runtime(policy: OperatorPolicy) -> tuple[int, int]:
    if os.name != "posix" or not hasattr(socket, "SO_PEERCRED"):
        raise ProtectedOperatorError("protected operator requires Linux SO_PEERCRED")
    try:
        operator_uid = pwd.getpwnam(policy.operator_user).pw_uid
        agent_uid = pwd.getpwnam(policy.agent_user).pw_uid
    except KeyError as exc:
        raise ProtectedOperatorError("operator or agent account is not provisioned") from exc
    if operator_uid == 0 or agent_uid == 0 or operator_uid == agent_uid:
        raise ProtectedOperatorError("operator and agent require distinct non-root UIDs")
    if os.getuid() != operator_uid or os.geteuid() != operator_uid:
        raise ProtectedOperatorError("service is not running as the dedicated operator UID")
    _safe_tree(policy.state_root, leaf_uid=operator_uid, leaf_mode=0o700)
    _safe_tree(policy.code_root, leaf_uid=operator_uid, leaf_mode=0o755, root_owned_leaf=True)
    try:
        Path(__file__).resolve().relative_to(policy.code_root.resolve())
    except ValueError as exc:
        raise ProtectedOperatorError("service code is not loaded from the protected code root") from exc
    return operator_uid, agent_uid


def peer_identity(connection: socket.socket, *, operator_uid: int, agent_uid: int) -> PeerIdentity:
    try:
        pid, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    except (OSError, struct.error) as exc:
        raise ProtectedOperatorError("cannot authenticate Unix peer credentials") from exc
    if uid in (0, operator_uid, agent_uid):
        raise ProtectedOperatorError("peer must be a distinct non-root human account")
    try:
        principal = pwd.getpwuid(uid).pw_name
    except KeyError as exc:
        raise ProtectedOperatorError("peer UID has no local account identity") from exc
    return PeerIdentity(pid=pid, uid=uid, gid=gid, principal=principal)


def send_frame(connection: socket.socket, value: Mapping[str, Any]) -> None:
    payload = _json_bytes(dict(value))
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtectedOperatorError("protocol frame exceeds size limit")
    connection.sendall(struct.pack("!Q", len(payload)) + payload)


def _recv_exact(connection: socket.socket, length: int) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        chunk = connection.recv(min(1024 * 1024, remaining))
        if not chunk:
            raise ProtectedOperatorError("protocol connection closed early")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def recv_frame(connection: socket.socket) -> dict[str, Any]:
    header = _recv_exact(connection, 8)
    length = struct.unpack("!Q", header)[0]
    if length < 2 or length > MAX_FRAME_BYTES:
        raise ProtectedOperatorError("protocol frame length refused")
    try:
        value = json.loads(_recv_exact(connection, length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtectedOperatorError("protocol frame must be UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ProtectedOperatorError("protocol frame must be a JSON object")
    return value


def decode_bundle(value: Any) -> tuple[dict[str, bytes], dict[str, Any], str]:
    if not isinstance(value, Mapping) or set(value) != {"schema_version", "files", "lifecycle"}:
        raise ProtectedOperatorError("promotion bundle fields mismatch")
    if value.get("schema_version") != "0.1" or not isinstance(value.get("files"), Mapping):
        raise ProtectedOperatorError("promotion bundle schema mismatch")
    files: dict[str, bytes] = {}
    total = 0
    for name, encoded in value["files"].items():
        if (
            not isinstance(name, str) or not name or name.startswith("/")
            or ".." in Path(name).parts or "\\" in name or not isinstance(encoded, str)
        ):
            raise ProtectedOperatorError("promotion bundle contains an unsafe file entry")
        try:
            payload = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise ProtectedOperatorError("promotion bundle contains invalid base64") from exc
        files[name] = payload
        total += len(payload)
        if len(files) > MAX_PACKAGE_FILES or total > MAX_PACKAGE_BYTES:
            raise ProtectedOperatorError("promotion bundle exceeds package limits")
    try:
        lifecycle_bytes = base64.b64decode(value["lifecycle"], validate=True)
        if len(lifecycle_bytes) > MAX_LIFECYCLE_BYTES:
            raise ProtectedOperatorError("promotion lifecycle exceeds size limit")
        lifecycle = json.loads(lifecycle_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise ProtectedOperatorError("promotion lifecycle is invalid") from exc
    if not isinstance(lifecycle, dict):
        raise ProtectedOperatorError("promotion lifecycle must be a JSON object")
    bundle_digest = _sha(_json_bytes(value))
    return files, lifecycle, bundle_digest


class ProtectedLedger:
    def __init__(self, path: Path):
        self.path = path
        existed = os.path.lexists(path)
        if existed and path.is_symlink():
            raise ProtectedOperatorError("authorization ledger may not be a symlink")
        self._initialize()
        if not existed:
            os.chmod(path, 0o600)
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ProtectedOperatorError("authorization ledger owner/mode mismatch")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS promotion_challenges (
                challenge_id TEXT PRIMARY KEY, bundle_sha256 TEXT NOT NULL,
                registry_sha256 TEXT NOT NULL, peer_uid INTEGER NOT NULL,
                token_sha256 TEXT NOT NULL, issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, attempts INTEGER NOT NULL,
                state TEXT NOT NULL, receipt_sha256 TEXT)"""
            )
        finally:
            connection.close()

    def issue(self, *, bundle_sha256: str, registry_sha256: str, peer_uid: int,
              token: str, now: datetime) -> tuple[str, str]:
        challenge_id = secrets.token_hex(32)
        expires = now + timedelta(seconds=CHALLENGE_SECONDS)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            uncertain = connection.execute(
                "SELECT challenge_id FROM promotion_challenges WHERE bundle_sha256=? AND state IN ('CONSUMING','RECONCILIATION_REQUIRED')",
                (bundle_sha256,),
            ).fetchone()
            if uncertain is not None:
                raise ProtectedOperatorError("candidate has an uncertain prior promotion; reconciliation required")
            connection.execute(
                "INSERT INTO promotion_challenges VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                (challenge_id, bundle_sha256, registry_sha256, peer_uid,
                 _sha(token.encode("ascii")), _stamp(now), _stamp(expires), 0, "ISSUED"),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return challenge_id, _stamp(expires)

    def consume(self, *, challenge_id: str, peer_uid: int, token: str,
                registry_sha256: str, now: datetime) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM promotion_challenges WHERE challenge_id=?", (challenge_id,)
            ).fetchone()
            if row is None or row["state"] != "ISSUED" or row["peer_uid"] != peer_uid:
                raise ProtectedOperatorError("challenge is unknown, spent, or belongs to another peer")
            expiry = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if not now < expiry:
                connection.execute("UPDATE promotion_challenges SET state='EXPIRED' WHERE challenge_id=?", (challenge_id,))
                connection.commit()
                raise ProtectedOperatorError("promotion challenge expired")
            attempts = row["attempts"] + 1
            if row["token_sha256"] != _sha(token.encode("ascii")):
                state = "DENIED" if attempts >= 3 else "ISSUED"
                connection.execute(
                    "UPDATE promotion_challenges SET attempts=?,state=? WHERE challenge_id=?",
                    (attempts, state, challenge_id),
                )
                connection.commit()
                raise ConfirmationMismatch(max(0, 3 - attempts))
            if row["registry_sha256"] != registry_sha256:
                raise ProtectedOperatorError("registry changed after challenge issuance")
            connection.execute(
                "UPDATE promotion_challenges SET attempts=?,state='CONSUMING' WHERE challenge_id=?",
                (attempts, challenge_id),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def finish(self, challenge_id: str, *, receipt_sha256: str | None) -> None:
        state = "COMPLETE" if receipt_sha256 else "RECONCILIATION_REQUIRED"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(
                "UPDATE promotion_challenges SET state=?,receipt_sha256=? WHERE challenge_id=? AND state='CONSUMING'",
                (state, receipt_sha256, challenge_id),
            ).rowcount
            if changed != 1:
                raise ProtectedOperatorError("challenge completion state mismatch")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _package_json(files: Mapping[str, bytes], name: str) -> dict[str, Any]:
    try:
        value = json.loads(files[name].decode("utf-8"))
    except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtectedOperatorError(f"package {name} is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ProtectedOperatorError(f"package {name} must be a JSON object")
    return value


@contextmanager
def _promotion_lock(state_root: Path):
    """Serialize registry verification, authorization consume, and persistence."""

    path = state_root / "promotion.lock"
    flags = os.O_CREAT | os.O_RDWR | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise ProtectedOperatorError("promotion lock is unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise ProtectedOperatorError("promotion lock owner/mode mismatch")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        os.close(descriptor)


def initialize_state(policy: OperatorPolicy, *, now: datetime | None = None) -> dict[str, Any]:
    from .registry import create_skill_registry, save_skill_registry

    policy.state_root.mkdir(parents=False, exist_ok=True, mode=0o700)
    registry_path = policy.state_root / "registry.json"
    if not registry_path.exists():
        save_skill_registry(create_skill_registry(created_at=_stamp(now or _now())), registry_path)
    (policy.state_root / "lifecycles").mkdir(mode=0o700, exist_ok=True)
    (policy.state_root / "receipts").mkdir(mode=0o700, exist_ok=True)
    ProtectedLedger(policy.state_root / "authorizations.sqlite3")
    return {"state_root": str(policy.state_root), "registry": str(registry_path), "initialized": True}


def prepare_promotion(request: Mapping[str, Any], *, policy: OperatorPolicy,
                      peer: PeerIdentity, now: datetime) -> tuple[PromotionSession, dict[str, Any]]:
    if set(request) != {"operation", "bundle"} or request.get("operation") != "PROMOTE":
        raise ProtectedOperatorError("unsupported protected operator request")
    files, lifecycle, bundle_sha = decode_bundle(request["bundle"])
    preview = preview_validated_candidate(files, lifecycle, observed_at=_stamp(now))
    registry_path = policy.state_root / "registry.json"
    registry = load_skill_registry(registry_path)
    registry_sha = _sha(registry_bytes(registry))
    token = secrets.token_hex(6)
    ledger = ProtectedLedger(policy.state_root / "authorizations.sqlite3")
    challenge_id, expires_at = ledger.issue(
        bundle_sha256=bundle_sha, registry_sha256=registry_sha,
        peer_uid=peer.uid, token=token, now=now,
    )
    session = PromotionSession(
        challenge_id=challenge_id, token=token, peer=peer, files=files,
        lifecycle=lifecycle, preview=preview, registry_sha256=registry_sha,
        policy=policy,
    )
    challenge = {
        "status": "AWAITING_HUMAN_CONFIRMATION",
        "challenge_id": challenge_id,
        "confirmation": f"APPROVE {token}",
        "expires_at": expires_at,
        "principal": peer.principal,
        "skill_id": preview["skill_id"],
        "skill_version": preview["skill_version"],
        "unit_count": preview["unit_count"],
        "claim_count": preview["claim_count"],
        "package_receipt_sha256": preview["package_receipt_sha256"],
        "registry_before_sha256": registry_sha,
    }
    return session, challenge


def confirm_promotion(session: PromotionSession, response: Mapping[str, Any], *,
                      now: datetime) -> dict[str, Any]:
    if set(response) != {"challenge_id", "confirmation"} or response.get("challenge_id") != session.challenge_id:
        raise ProtectedOperatorError("confirmation response binding mismatch")
    confirmation = response.get("confirmation")
    if not isinstance(confirmation, str):
        raise ProtectedOperatorError("confirmation must be text")
    supplied_token = confirmation[len("APPROVE "):] if confirmation.startswith("APPROVE ") else ""
    with _promotion_lock(session.policy.state_root):
        registry_path = session.policy.state_root / "registry.json"
        current_registry = load_skill_registry(registry_path)
        current_registry_sha = _sha(registry_bytes(current_registry))
        ledger = ProtectedLedger(session.policy.state_root / "authorizations.sqlite3")
        ledger.consume(
            challenge_id=session.challenge_id, peer_uid=session.peer.uid,
            token=supplied_token, registry_sha256=current_registry_sha, now=now,
        )
        try:
            result = promote_validated_skill(
                current_registry,
                _package_json(session.files, "manifest.json"), session.lifecycle,
                _package_json(session.files, "claim-index.json"),
                _package_json(session.files, "SOURCE_RECEIPT.json"),
                authorization=OperatorAuthorization(
                    principal_ref=f"linux-uid:{session.peer.uid}:{session.peer.principal}",
                    authorization_ref=f"protected-challenge:{session.challenge_id}",
                    verified_by_host=True,
                ),
                decision_ref=f"typed-confirmation:{session.challenge_id}",
                occurred_at=_stamp(now),
            )
            name = sha256(session.preview["skill_id"].encode("utf-8")).hexdigest()
            paths = persist_operator_promotion(
                result,
                registry_path=registry_path,
                promoted_lifecycle_path=session.policy.state_root / "lifecycles" / f"{name}.v{session.preview['skill_version']}.json",
                receipt_path=session.policy.state_root / "receipts" / f"{session.challenge_id}.json",
            )
            receipt_sha = _sha(Path(paths["receipt"]).read_bytes())
        except Exception:
            ledger.finish(session.challenge_id, receipt_sha256=None)
            raise
        try:
            ledger.finish(session.challenge_id, receipt_sha256=receipt_sha)
        except Exception as exc:
            raise ProtectedOperatorError(
                "promotion persisted but authorization ledger completion is uncertain; reconcile registry truth"
            ) from exc
    return {
        "status": "PROMOTED",
        "skill_id": session.preview["skill_id"],
        "skill_version": session.preview["skill_version"],
        "principal": session.peer.principal,
        "receipt_sha256": receipt_sha,
        "paths": paths,
    }


def handle_connection(connection: socket.socket, *, policy: OperatorPolicy,
                      peer: PeerIdentity, now_fn=_now) -> None:
    session, challenge = prepare_promotion(
        recv_frame(connection), policy=policy, peer=peer, now=now_fn()
    )
    send_frame(connection, challenge)
    connection.settimeout(CHALLENGE_SECONDS + 5)
    while True:
        try:
            result = confirm_promotion(session, recv_frame(connection), now=now_fn())
            send_frame(connection, result)
            return
        except ConfirmationMismatch as exc:
            status = "REFUSED" if exc.remaining == 0 else "MISMATCH"
            send_frame(connection, {"status": status, "error": str(exc), "remaining": exc.remaining})
            if exc.remaining == 0:
                raise


def serve_connected_socket(connection: socket.socket) -> None:
    policy = _load_policy()
    operator_uid, agent_uid = verify_runtime(policy)
    peer = peer_identity(connection, operator_uid=operator_uid, agent_uid=agent_uid)
    os.umask(0o077)
    try:
        connection.settimeout(INITIAL_REQUEST_SECONDS)
        handle_connection(connection, policy=policy, peer=peer)
    except Exception as exc:
        try:
            send_frame(connection, {"status": "REFUSED", "error": str(exc)})
        except Exception:
            pass
        raise
