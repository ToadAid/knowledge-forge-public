"""P3B-P2C-P3-P1: SQLite transaction rehearsal; never operator authority.

A SQLite commit protects ONE database transition against concurrent clients.
The caller owns the database, parameters and clock: none attest host identity,
human authentication, protected storage, or permission to promote knowledge.
No operator, server, launcher, installer, token or external side effects exist.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

KIND = "UNTRUSTED_SQLITE_LEDGER_PILOT_NOT_AUTHORIZATION"
SCHEMA_VERSION = "0.1"
_SHA = re.compile(r"^sha256:[a-f0-9]{64}$")
_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{7,127}$")
_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MAX_AGE = timedelta(minutes=15)
_STATES = frozenset({"PREPARED_SIMULATION", "CONSUMED_SIMULATION", "CANCELLED_SIMULATION", "RECONCILIATION_REQUIRED"})
_SCHEMA = """
CREATE TABLE IF NOT EXISTS rehearsal_decisions (
 id TEXT PRIMARY KEY, binding_sha256 TEXT NOT NULL,
 created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
 state TEXT NOT NULL, revision INTEGER NOT NULL,
 last_event_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rehearsal_events (
 id TEXT NOT NULL, revision INTEGER NOT NULL, action TEXT NOT NULL,
 occurred_at TEXT NOT NULL, prev_sha256 TEXT NOT NULL,
 event_sha256 TEXT NOT NULL, PRIMARY KEY (id, revision));
"""


class SQLitePilotError(ValueError):
    """A refusal or uncertain outcome in a NON-AUTHORIZING rehearsal."""


def _time(value: Any) -> datetime:
    if not isinstance(value, str) or not _UTC.fullmatch(value):
        raise SQLitePilotError("timestamp must be canonical UTC seconds")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise SQLitePilotError("invalid timestamp") from exc


def _sha(value: Any) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise SQLitePilotError("binding must be lowercase sha256:hex")
    return value


def _id(value: Any) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise SQLitePilotError("invalid synthetic simulation ID")
    return value


def _hash(parts: list[Any]) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return "sha256:" + sha256(raw).hexdigest()


def _event(id: str, revision: int, action: str, at: str, previous: str) -> str:
    return _hash([KIND, id, revision, action, at, previous])


def _safe_path(value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.name.endswith(".sqlite3"):
        raise SQLitePilotError("pilot requires explicit absolute .sqlite3 path")
    if any(p == ".." for p in path.parts):
        raise SQLitePilotError("parent traversal refused")
    # Reject protected locations before any metadata probe. Their contents can
    # be inaccessible once a real operator state directory has been deployed.
    if str(path).startswith(("/var/lib/", "/opt/", "/etc/", "/root/", "/run/")):
        raise SQLitePilotError("system/protected paths excluded from untrusted pilot")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise SQLitePilotError("symlink ancestor in pilot DB path")
    if not path.parent.is_dir():
        raise SQLitePilotError("pilot DB parent must already exist")
    if path.exists() and not path.is_file():
        raise SQLitePilotError("existing DB must be a regular file")
    return path


class SQLiteLedgerPilot:
    """One-DB transactional rehearsal; the human/operator boundary is ABSENT."""

    def __init__(self, db_path: str | Path) -> None:
        self.path = _safe_path(db_path)

    def _connect(self) -> sqlite3.Connection:
        # BEGIN IMMEDIATE serializes writers on one local SQLite DB, not
        # authorization or a guarantee for remote/NFS/cloud filesystems.
        try:
            conn = sqlite3.connect(str(self.path), timeout=3.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=3000")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA journal_mode=DELETE")
            conn.executescript(_SCHEMA)
            return conn
        except sqlite3.Error as exc:
            raise SQLitePilotError("cannot open pilot database; reconcile manually") from exc

    def _transaction(self, op):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = op(conn)
            conn.commit()
            return result
        except sqlite3.Error as exc:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
            raise SQLitePilotError("SQLite transaction failed; reconcile outcome before retry") from exc
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def prepare(self, *, simulation_id: str, binding_sha256: str,
                created_at: str, expires_at: str) -> dict[str, Any]:
        id, binding = _id(simulation_id), _sha(binding_sha256)
        start, expires = _time(created_at), _time(expires_at)
        if not timedelta(0) < expires - start <= MAX_AGE:
            raise SQLitePilotError("simulation lifetime must be >0 and <=15 minutes")
        def op(conn):
            if conn.execute("SELECT 1 FROM rehearsal_decisions WHERE id=?", (id,)).fetchone():
                raise SQLitePilotError("duplicate simulation ID; no overwrite")
            genesis = _hash([KIND, id, 1, "PREPARE_SIMULATION", created_at, "GENESIS", binding, expires_at])
            conn.execute("INSERT INTO rehearsal_decisions VALUES (?,?,?,?,?,?,?)",
                         (id, binding, created_at, expires_at, "PREPARED_SIMULATION", 1, genesis))
            conn.execute("INSERT INTO rehearsal_events VALUES (?,?,?,?,?,?)",
                         (id, 1, "PREPARE_SIMULATION", created_at, "GENESIS", genesis))
            return self._public(id, "PREPARED_SIMULATION", 1, genesis)
        return self._transaction(op)

    @staticmethod
    def _public(id: str, state: str, revision: int, digest: str) -> dict[str, Any]:
        return {"schema_version": SCHEMA_VERSION, "kind": KIND,
                "simulation_id": id, "state": state, "revision": revision,
                "event_sha256": digest, "execution_authority": "NONE",
                "human_authenticated": False, "authorization_issued": False,
                "operator_invoked": False, "promotion_authorized": False,
                "registry_mutated": False, "tool_grants": [],
                "protected_host_verified": False}

    def _load_verify(self, conn, id: str):
        row = conn.execute("SELECT * FROM rehearsal_decisions WHERE id=?", (id,)).fetchone()
        if row is None:
            raise SQLitePilotError("unknown synthetic ID")
        _sha(row["binding_sha256"])
        start, end = _time(row["created_at"]), _time(row["expires_at"])
        if not timedelta(0) < end - start <= MAX_AGE:
            raise SQLitePilotError("invalid stored lifetime")
        events = conn.execute("SELECT * FROM rehearsal_events WHERE id=? ORDER BY revision", (id,)).fetchall()
        if len(events) != row["revision"] or not 1 <= len(events) <= 4:
            raise SQLitePilotError("noncanonical event count")
        state, prev, last_at = None, "GENESIS", start
        for idx, event in enumerate(events, 1):
            action, at = event["action"], event["occurred_at"]
            when = _time(at)
            if event["revision"] != idx or event["prev_sha256"] != prev or when < last_at:
                raise SQLitePilotError("event sequence mismatch")
            expected = (_hash([KIND, id, idx, action, at, prev, row["binding_sha256"], row["expires_at"]])
                        if idx == 1 else _event(id, idx, action, at, prev))
            if event["event_sha256"] != expected:
                raise SQLitePilotError("event digest mismatch")
            if idx == 1 and action == "PREPARE_SIMULATION" and at == row["created_at"]:
                state = "PREPARED_SIMULATION"
            elif state == "PREPARED_SIMULATION" and action in ("CONSUME_SIMULATION", "CANCEL_SIMULATION", "RECOVER_UNCERTAIN"):
                if action != "RECOVER_UNCERTAIN" and when >= end:
                    raise SQLitePilotError("stored transition after expiry")
                state = {"CONSUME_SIMULATION": "CONSUMED_SIMULATION",
                         "CANCEL_SIMULATION": "CANCELLED_SIMULATION",
                         "RECOVER_UNCERTAIN": "RECONCILIATION_REQUIRED"}[action]
            else:
                raise SQLitePilotError("invalid state history")
            last_at, prev = when, event["event_sha256"]
        if state != row["state"] or prev != row["last_event_sha256"] or state not in _STATES:
            raise SQLitePilotError("stored state digest mismatch")
        return row

    def inspect(self, simulation_id: str) -> dict[str, Any]:
        id = _id(simulation_id)
        with closing(self._connect()) as conn:
            row = self._load_verify(conn, id)
            return self._public(id, row["state"], row["revision"], row["last_event_sha256"])

    def transition(self, *, simulation_id: str, binding_sha256: str,
                   expected_revision: int, action: str, occurred_at: str) -> dict[str, Any]:
        id, binding = _id(simulation_id), _sha(binding_sha256)
        when = _time(occurred_at)
        if type(expected_revision) is not int or expected_revision < 1:
            raise SQLitePilotError("expected revision must be positive integer")
        if action not in ("CONSUME_SIMULATION", "CANCEL_SIMULATION", "RECOVER_UNCERTAIN"):
            raise SQLitePilotError("unsupported transition")
        def op(conn):
            row = self._load_verify(conn, id)
            if row["binding_sha256"] != binding:
                raise SQLitePilotError("synthetic binding mismatch")
            if row["revision"] != expected_revision or row["state"] != "PREPARED_SIMULATION":
                raise SQLitePilotError("stale, duplicate, or terminal transition refused")
            if when < _time(row["created_at"]):
                raise SQLitePilotError("caller clock moved backwards")
            if action != "RECOVER_UNCERTAIN" and when >= _time(row["expires_at"]):
                raise SQLitePilotError("expired simulation")
            state = {"CONSUME_SIMULATION": "CONSUMED_SIMULATION",
                     "CANCEL_SIMULATION": "CANCELLED_SIMULATION",
                     "RECOVER_UNCERTAIN": "RECONCILIATION_REQUIRED"}[action]
            revision = expected_revision + 1
            digest = _event(id, revision, action, occurred_at, row["last_event_sha256"])
            conn.execute("UPDATE rehearsal_decisions SET state=?,revision=?,last_event_sha256=? WHERE id=? AND revision=? AND state=?",
                         (state, revision, digest, id, expected_revision, "PREPARED_SIMULATION"))
            if conn.execute("SELECT changes()").fetchone()[0] != 1:
                raise SQLitePilotError("stale writer refused")
            conn.execute("INSERT INTO rehearsal_events VALUES (?,?,?,?,?,?)",
                         (id, revision, action, occurred_at, row["last_event_sha256"], digest))
            return self._public(id, state, revision, digest)
        return self._transaction(op)
