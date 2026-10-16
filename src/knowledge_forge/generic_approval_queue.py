"""Operator-owned request queue; agent submission never grants authority.

Only the kernel-authenticated reviewer connection receives a challenge or may
decide a request. The desktop reviewer runs outside the agent sandbox, while
the protected operator owns snapshots, ledger, registry and receipts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import secrets
from typing import Any, Mapping

from .candidate_preview import preview_validated_candidate
from .protected_operator_service import (
    CHALLENGE_SECONDS, OperatorPolicy, PeerIdentity, ProtectedLedger,
    ProtectedOperatorError, _json_bytes, _promotion_lock, _sha, _stamp,
    confirm_promotion, decode_bundle, prepare_promotion, recv_frame, send_frame,
)

AGENT_OPERATIONS = frozenset({"SUBMIT_GENERIC", "STATUS_GENERIC"})
REVIEW_OPERATIONS = frozenset({"LIST_GENERIC", "REVIEW_GENERIC"})
QUEUE_SECONDS = 3600
MAX_QUEUE_ENTRIES = 128
MAX_PENDING = 16
MAX_BUNDLE_BYTES = 8 * 1024 * 1024
MAX_STORED_BYTES = 128 * 1024 * 1024
_REQUEST_ID = re.compile(r"^[a-f0-9]{64}$")


def _request_id(value: Any) -> str:
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ProtectedOperatorError("invalid queue request identity")
    return value


class ApprovalQueue(ProtectedLedger):
    """Private SQLite snapshots with bounded storage and conditional transitions.

    ProtectedLedger supplies owner/mode/symlink checks and durable connections.
    Entries are not deleted automatically: reaching a cap requires deliberate
    administrator retention, never overwriting an approval or audit record.
    """

    def _initialize(self) -> None:
        db = self._connect()
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS approval_requests (
                request_id TEXT PRIMARY KEY, submitter_uid INTEGER NOT NULL,
                bundle TEXT NOT NULL, bundle_sha256 TEXT NOT NULL,
                preview TEXT NOT NULL, created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL, state TEXT NOT NULL,
                reviewer_uid INTEGER, result TEXT)""")
        finally:
            db.close()

    def _expire(self, db, now: datetime) -> None:
        # PROMOTING is deliberately never retried or expired: a crash during
        # persistence needs reconciliation with registry/authorization truth.
        rows = db.execute("SELECT request_id,expires_at FROM approval_requests WHERE state IN ('PENDING','REVIEWING')").fetchall()
        for row in rows:
            if now >= datetime.fromisoformat(row['expires_at'].replace('Z', '+00:00')):
                db.execute("UPDATE approval_requests SET state='EXPIRED' WHERE request_id=?", (row['request_id'],))

    def submit(self, bundle: Mapping[str, Any], *, peer: PeerIdentity,
               now: datetime) -> dict[str, Any]:
        payload = _json_bytes(bundle)
        if len(payload) > MAX_BUNDLE_BYTES:
            raise ProtectedOperatorError("queued bundle exceeds 8 MiB limit")
        files, lifecycle, bundle_sha = decode_bundle(bundle)
        preview = preview_validated_candidate(files, lifecycle, observed_at=_stamp(now))
        identity = secrets.token_hex(32)
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db, now)
            count, size = db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(bundle AS BLOB))),0) FROM approval_requests').fetchone()
            pending = db.execute("SELECT COUNT(*) FROM approval_requests WHERE state IN ('PENDING','REVIEWING','PROMOTING')").fetchone()[0]
            if count >= MAX_QUEUE_ENTRIES or pending >= MAX_PENDING or size + len(payload) > MAX_STORED_BYTES:
                raise ProtectedOperatorError('approval queue capacity reached; administrator retention required')
            duplicate = db.execute("SELECT request_id FROM approval_requests WHERE bundle_sha256=? AND state IN ('PENDING','REVIEWING','PROMOTING','PROMOTED','RECONCILIATION_REQUIRED')", (bundle_sha,)).fetchone()
            if duplicate:
                raise ProtectedOperatorError('bundle already queued, promoted, or awaiting reconciliation')
            db.execute('INSERT INTO approval_requests VALUES (?,?,?,?,?,?,?,?,NULL,NULL)',
                (identity, peer.uid, payload.decode('utf-8'), bundle_sha,
                 _json_bytes(preview).decode('utf-8'), _stamp(now),
                 _stamp(now + timedelta(seconds=QUEUE_SECONDS)), 'PENDING'))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
        return self.status(identity, peer_uid=peer.uid, now=now)

    def _row(self, db, identity: str, now: datetime):
        self._expire(db, now)
        row = db.execute('SELECT * FROM approval_requests WHERE request_id=?', (_request_id(identity),)).fetchone()
        if row is None:
            raise ProtectedOperatorError('queue request unavailable')
        return row

    @staticmethod
    def _public(row) -> dict[str, Any]:
        preview = json.loads(row['preview'])
        result = json.loads(row['result']) if row['result'] else None
        return {
            'request_id': row['request_id'], 'status': row['state'],
            'bundle_sha256': row['bundle_sha256'], 'expires_at': row['expires_at'],
            'skill_id': preview['skill_id'], 'skill_version': preview['skill_version'],
            'package_receipt_sha256': preview['package_receipt_sha256'],
            'unit_count': preview['unit_count'], 'claim_count': preview['claim_count'],
            'promotion_performed': (None if row['state'] in ('PROMOTING', 'RECONCILIATION_REQUIRED')
                                    else row['state'] == 'PROMOTED'),
            'receipt_sha256': result.get('receipt_sha256') if result else None,
            'reconciliation_required': row['state'] in ('PROMOTING', 'RECONCILIATION_REQUIRED'),
        }

    def status(self, identity: str, *, peer_uid: int, now: datetime) -> dict[str, Any]:
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, identity, now)
            if row['submitter_uid'] != peer_uid:
                raise ProtectedOperatorError('queue request unavailable')
            result = self._public(row)
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def pending(self, *, now: datetime) -> list[dict[str, Any]]:
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db, now)
            rows = db.execute("SELECT * FROM approval_requests WHERE state='PENDING' ORDER BY created_at,request_id LIMIT 16").fetchall()
            result = [self._public(row) for row in rows]
            db.commit()
            return result
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def claim(self, identity: str, *, reviewer: PeerIdentity, now: datetime) -> dict[str, Any]:
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, identity, now)
            if row['state'] != 'PENDING':
                raise ProtectedOperatorError('queue request is not pending')
            bundle = json.loads(row['bundle'])
            if _sha(_json_bytes(bundle)) != row['bundle_sha256']:
                raise ProtectedOperatorError('queued snapshot digest mismatch')
            db.execute("UPDATE approval_requests SET state='REVIEWING',reviewer_uid=? WHERE request_id=?", (reviewer.uid, identity))
            db.commit()
            return bundle
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def transition(self, identity: str, *, reviewer: PeerIdentity, expected: str,
                   state: str, now: datetime, result=None) -> None:
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            row = self._row(db, identity, now)
            if row['state'] != expected or row['reviewer_uid'] != reviewer.uid:
                raise ProtectedOperatorError('queue decision is expired, spent, or belongs to another reviewer')
            db.execute('UPDATE approval_requests SET state=?,result=? WHERE request_id=?',
                (state, _json_bytes(result).decode('utf-8') if result else None, identity))
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


def handle_generic_connection(connection, *, policy: OperatorPolicy,
                              peer: PeerIdentity, request: Mapping[str, Any],
                              agent: bool, now_fn) -> None:
    operation = request.get('operation')
    allowed = AGENT_OPERATIONS if agent else REVIEW_OPERATIONS
    if operation not in allowed:
        raise ProtectedOperatorError('operation unavailable to this peer role')
    queue = ApprovalQueue(policy.state_root / 'approval-queue.sqlite3')
    if operation == 'SUBMIT_GENERIC':
        if set(request) != {'operation', 'bundle'}:
            raise ProtectedOperatorError('submission fields mismatch')
        send_frame(connection, queue.submit(request['bundle'], peer=peer, now=now_fn()))
        return
    if operation == 'STATUS_GENERIC':
        if set(request) != {'operation', 'request_id'}:
            raise ProtectedOperatorError('status fields mismatch')
        send_frame(connection, queue.status(request['request_id'], peer_uid=peer.uid, now=now_fn()))
        return
    if operation == 'LIST_GENERIC':
        if set(request) != {'operation'}:
            raise ProtectedOperatorError('list fields mismatch')
        send_frame(connection, {'status': 'PENDING_REQUESTS', 'requests': queue.pending(now=now_fn())})
        return
    if set(request) != {'operation', 'request_id'}:
        raise ProtectedOperatorError('review fields mismatch')
    identity = _request_id(request['request_id'])
    # A queue decision and registry transition are serialized against other
    # reviewers by conditional state changes and the existing promotion lock.
    bundle = queue.claim(identity, reviewer=peer, now=now_fn())
    promoting = False
    completed = False
    try:
        session, challenge = prepare_promotion({'operation': 'PROMOTE', 'bundle': bundle},
            policy=policy, peer=peer, now=now_fn())
        send_frame(connection, {**challenge, 'request_id': identity,
            'bundle_sha256': _sha(_json_bytes(bundle)),
            'source_corpus_coverage': session.preview['source_corpus_coverage'],
            'coverage_warning': session.preview['warning']})
        connection.settimeout(CHALLENGE_SECONDS + 5)
        response = recv_frame(connection)
        if (set(response) != {'operation', 'request_id', 'challenge_id', 'decision'}
            or response.get('operation') != 'DECIDE_GENERIC'
            or response.get('request_id') != identity
            or response.get('challenge_id') != session.challenge_id
            or response.get('decision') not in ('APPROVE', 'REJECT', 'CANCEL')):
            raise ProtectedOperatorError('review decision binding mismatch')
        decision = response['decision']
        if decision != 'APPROVE':
            state = 'REJECTED' if decision == 'REJECT' else 'CANCELLED'
            queue.transition(identity, reviewer=peer, expected='REVIEWING', state=state, now=now_fn())
            completed = True
            send_frame(connection, {'status': state, 'request_id': identity, 'promotion_performed': False})
            return
        queue.transition(identity, reviewer=peer, expected='REVIEWING', state='PROMOTING', now=now_fn())
        promoting = True
        result = confirm_promotion(session,
            {'challenge_id': session.challenge_id, 'confirmation': challenge['confirmation']}, now=now_fn())
        queue.transition(identity, reviewer=peer, expected='PROMOTING', state='PROMOTED', now=now_fn(), result=result)
        completed = True
        send_frame(connection, {**result, 'request_id': identity})
    finally:
        if not completed:
            # A disconnect before approval cancels. Any ambiguity after a
            # decision starts is durable and never blindly retried.
            try:
                queue.transition(identity, reviewer=peer,
                    expected='PROMOTING' if promoting else 'REVIEWING',
                    state='RECONCILIATION_REQUIRED' if promoting else 'CANCELLED', now=now_fn())
            except ProtectedOperatorError:
                pass
