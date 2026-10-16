"""APPROVAL UX P1: signed host approval decisions that complete real promotion.

Chat may *request* promotion (``approval.build_approval_request``); chat alone
must never authenticate it. This module defines the smallest contract a
trusted approval host (for example the ToadAid-app backend, running in its own
account, holding its own secret) satisfies to convert one authenticated human
decision into promotion authority:

1. The host presents a *decision record* signed with the host's own HMAC key.
   The agent process never sees the key, so it cannot forge a decision.
2. The decision binds, by digest, the exact ``approval request`` bytes — an
   expired, altered, mismatched, or unsolicited request refuses.
3. A durable single-use ledger in a host-owned directory makes a decision
   replay impossible.
4. Only then does this module construct the existing
   ``OperatorAuthorization`` (``verified_by_host=True``) and the caller drives
   the unchanged engine in ``operator_promotion`` — ``VALIDATED != PROMOTED``
   and every promotion invariant live there is inherited, not bypassed.

If no trusted host with a signing key is available, this contract refuses with
the exact missing dependency instead of quietly simulating an approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from typing import Any, Mapping

from .approval import (
    approval_request_sha256,
    approval_request_state,
    validate_approval_request,
)
from .operator_promotion import OperatorAuthorization

APPROVAL_HOST_DECISION_SCHEMA_VERSION = "0.1"
APPROVAL_HOST_DECISION_KIND = "KNOWLEDGE_FORGE_APPROVAL_HOST_DECISION"
APPROVAL_HOST_SIGNATURE_SCHEME = "HMAC-SHA256"
APPROVAL_HOST_LEDGER_FILENAME = "approval-host-decisions.sqlite3"

HOST_DECISIONS = frozenset({"APPROVED", "REJECTED"})

_DECISION_FIELDS = frozenset({
    "schema_version",
    "kind",
    "decision",
    "request_sha256",
    "decided_by",
    "host_id",
    "decision_nonce",
    "decided_at",
    "host_signature",
})
_NONCE_RE = re.compile(r"^[0-9a-f]{32,128}$")
_HOST_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
_DECIDED_BY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:\-@]{1,160}$"
)
_SIGNATURE_RE = re.compile(r"^hmac-sha256:[0-9a-f]{64}$")


class ApprovalHostError(ValueError):
    """Raised when a host approval decision cannot authorize promotion."""


# Host key file bounds, shared by every trusted-host surface (the single
# approve CLI and the interactive approve TUI). The key is the whole trust
# anchor, so the file checks are deliberately strict.
MAX_HOST_KEY_BYTES = 4096


def read_host_key_file(path: Path) -> bytes:
    """Load a trusted host key file with fail-closed hygiene checks.

    The file must be a regular file with no symlink path components and
    1..4096 bytes of secret material. On POSIX it must additionally be mode
    0600 (not group/other readable) and owned by this process account, so a
    host running under its own account is the only reader.
    """

    resolved = Path(os.path.abspath(path))
    for parent in resolved.parents:
        if parent.is_symlink():
            raise ApprovalHostError(
                "host key path contains a symlink component; the host key must "
                "be a plain 0600 file owned by this process account"
            )
    try:
        key_stat = os.stat(resolved, follow_symlinks=False)
    except OSError as exc:
        raise ApprovalHostError(f"host key file is missing: {path}") from exc
    if not stat.S_ISREG(key_stat.st_mode):
        raise ApprovalHostError("host key file must be a regular file")
    if sys.platform != "win32":
        if key_stat.st_mode & 0o077:
            raise ApprovalHostError(
                "host key file is readable by group or others; refusing (mode "
                "must be 0600 and the file owned by this process account)"
            )
        if key_stat.st_uid != os.geteuid():
            raise ApprovalHostError(
                "host key file is not owned by this process account; the "
                "trusted host alone may hold and use its own key"
            )
    try:
        key = resolved.read_bytes()
    except OSError as exc:
        raise ApprovalHostError(f"host key file is unreadable: {path}") from exc
    if not key or len(key) > MAX_HOST_KEY_BYTES:
        raise ApprovalHostError(
            "host key file must be 1..4096 bytes of secret material"
        )
    return key


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ApprovalHostError("approval host data is not canonical JSON") from exc


def _digest(value: Any) -> str:
    return "sha256:" + sha256(_canonical_bytes(value)).hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ApprovalHostError(f"{label} must be a mapping")
    return value


def _exact_fields(value: Mapping[str, Any], expected: frozenset[str], label: str) -> None:
    if set(value) != set(expected):
        unknown = sorted(set(value) - set(expected))
        missing = sorted(set(expected) - set(value))
        raise ApprovalHostError(
            f"{label} fields must match exact contract; unknown={unknown} missing={missing}"
        )


def _text(value: Any, label: str, *, maximum: int = 160) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise ApprovalHostError(f"{label} must be bounded printable text")
    return value


def _time(value: Any, label: str) -> tuple[datetime, str]:
    text = _text(value, label, maximum=40)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ApprovalHostError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ApprovalHostError(f"{label} must include a timezone")
    utc = parsed.astimezone(timezone.utc)
    return utc, utc.isoformat().replace("+00:00", "Z")


def _sign(body: Mapping[str, Any], host_key: bytes) -> str:
    return "hmac-sha256:" + hmac.new(
        host_key, _canonical_bytes(body), sha256
    ).hexdigest()


def _decision_body(
    request: Mapping[str, Any],
    *,
    decision: str,
    decided_by: str,
    host_id: str,
    decision_nonce: str,
    decided_at: str,
):
    """Build the unsigned body; both builder and validator rebuild this."""

    request_sha256 = approval_request_sha256(request)
    decided_dt, decided = _time(decided_at, "decided_at")
    expires_dt, _expires = _time(request["expires_at"], "request expires_at")
    if decided_dt >= expires_dt:
        raise ApprovalHostError(
            "approved request expired before the host decision; "
            "issue a fresh approval request instead"
        )
    return {
        "schema_version": APPROVAL_HOST_DECISION_SCHEMA_VERSION,
        "kind": APPROVAL_HOST_DECISION_KIND,
        "decision": decision,
        "request_sha256": request_sha256,
        "decided_by": decided_by,
        "host_id": host_id,
        "decision_nonce": decision_nonce,
        "decided_at": decided,
    }


def build_host_decision(
    request: Mapping[str, Any],
    *,
    decision: str,
    decided_by: str,
    host_id: str,
    decision_nonce: str,
    decided_at: str,
    host_key: bytes,
) -> dict[str, Any]:
    """Sign one APPROVED/REJECTED decision binding the exact request bytes."""

    if not isinstance(host_key, bytes) or not host_key:
        raise ApprovalHostError("host_key must be non-empty bytes held by the host")
    if decision not in HOST_DECISIONS:
        raise ApprovalHostError(
            f"host decision must be one of {sorted(HOST_DECISIONS)}"
        )
    validated_request = validate_approval_request(request)
    decided_by = _text(decided_by, "decided_by", maximum=160)
    if not _DECIDED_BY_RE.fullmatch(decided_by):
        raise ApprovalHostError("decided_by must be a bounded principal reference")
    host_id = _text(host_id, "host_id", maximum=128)
    if not _HOST_ID_RE.fullmatch(host_id):
        raise ApprovalHostError("host_id must be a bounded host identifier")
    decision_nonce = _text(
        decision_nonce, "decision_nonce", maximum=128
    )
    if not _NONCE_RE.fullmatch(decision_nonce):
        raise ApprovalHostError(
            "decision_nonce must be 32..128 lowercase hex characters"
        )
    body = _decision_body(
        validated_request,
        decision=decision,
        decided_by=decided_by,
        host_id=host_id,
        decision_nonce=decision_nonce,
        decided_at=decided_at,
    )
    return {**body, "host_signature": _sign(body, host_key)}


def validate_host_decision(
    value: Any,
    *,
    request: Mapping[str, Any],
    host_key: bytes,
    as_of: str,
) -> dict[str, Any]:
    """Rebuild and verify a signed decision against the presented request.

    Refuses altered decisions (signature mismatch), altered or mismatched
    requests (request_sha256 binding), expired requests (as_of), and decisions
    recorded after their request expired.
    """

    decision = dict(_mapping(value, "host approval decision"))
    _exact_fields(decision, _DECISION_FIELDS, "host approval decision")
    if not isinstance(host_key, bytes) or not host_key:
        raise ApprovalHostError("host_key must be non-empty bytes held by the host")
    if decision.get("schema_version") != APPROVAL_HOST_DECISION_SCHEMA_VERSION:
        raise ApprovalHostError("unsupported approval host decision schema_version")
    if decision.get("kind") != APPROVAL_HOST_DECISION_KIND:
        raise ApprovalHostError("unsupported approval host decision kind")
    if not isinstance(decision.get("host_signature"), str) or not _SIGNATURE_RE.fullmatch(
        decision["host_signature"]
    ):
        raise ApprovalHostError("host_signature must be an hmac-sha256 signature")

    body = {key: value for key, value in decision.items() if key != "host_signature"}
    expected_signature = _sign(body, host_key)
    if not hmac.compare_digest(expected_signature, decision["host_signature"]):
        raise ApprovalHostError(
            "approval host decision signature invalid: the decision was "
            "altered or was not signed by this host key"
        )
    if decision.get("decision") not in HOST_DECISIONS:
        raise ApprovalHostError(
            f"host decision must be one of {sorted(HOST_DECISIONS)}"
        )
    validated_request = validate_approval_request(request)
    if approval_request_sha256(validated_request) != decision["request_sha256"]:
        raise ApprovalHostError(
            "approval host decision does not match the presented approval request"
        )
    if approval_request_state(validated_request, as_of=as_of) == "EXPIRED":
        raise ApprovalHostError(
            "approval request expired: issue a fresh request and approve again"
        )
    return decision


def decision_sha256(decision: Mapping[str, Any]) -> str:
    return _digest(_mapping(decision, "host approval decision"))


class ConsumedDecisionLedger:
    """Durable single-use ledger for signed host approval decisions.

    The database must live in a directory the trusted host owns; consuming a
    decision there is the atomic point that makes an approval non-replayable.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.connection = sqlite3.connect(
            self.path, isolation_level=None
        )
        try:
            os.chmod(self.path, 0o600)
            self.connection.execute(
                "PRAGMA journal_mode=DELETE"
            )
            self.connection.execute(
                "PRAGMA synchronous=FULL"
            )
            self.connection.execute(
                """
                CREATE TABLE IF NOT EXISTS host_decisions (
                    decision_digest TEXT PRIMARY KEY NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    principal_ref TEXT NOT NULL,
                    consumed_at TEXT NOT NULL
                )
                """
            )
            self.connection.commit()
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def claim(
        self,
        decision: Mapping[str, Any],
        *,
        principal_ref: str,
        consumed_at: str,
    ) -> str:
        _mapping(decision, "host approval decision")
        digest = decision_sha256(decision)
        try:
            self.connection.execute(
                """
                INSERT INTO host_decisions
                    (decision_digest, request_sha256, principal_ref, consumed_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    digest,
                    decision["request_sha256"],
                    principal_ref,
                    consumed_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ApprovalHostError(
                "single-use approval decision already consumed: "
                "replaying an approved decision is refused"
            ) from exc
        self.connection.commit()
        return digest


def authorize_from_host_decision(
    request: Mapping[str, Any],
    decision: Mapping[str, Any],
    *,
    host_key: bytes,
    ledger_path: str | Path,
    as_of: str,
) -> OperatorAuthorization:
    """Convert one verified, signed, first-use host decision into authority.

    This is the deliberate approval-host counterpart of the P2B0 seam that
    refuses unauthenticated review receipts: a decision only reaches
    ``OperatorAuthorization`` when a trusted host signed it (the key never
    passes through agent hands) and it has never been consumed before.
    """

    if host_key is None:
        raise ApprovalHostError(
            "no authenticated approval host is available: promotion requires a "
            "decision signed by a trusted host key held outside the agent "
            "process; chat and agent-held credentials cannot authorize it"
        )
    validated_request = validate_approval_request(request)
    validated_decision = validate_host_decision(
        decision, request=validated_request, host_key=host_key, as_of=as_of
    )
    if validated_decision["decision"] != "APPROVED":
        raise ApprovalHostError(
            "host decision is not APPROVED; nothing was or will be authorized"
        )
    principal_ref = (
        f"approval-host:{validated_decision['host_id']}"
        f":principal:{validated_decision['decided_by']}"
    )
    authorization_ref = f"signed-host-decision:{validated_decision['request_sha256']}"
    ledger = ConsumedDecisionLedger(ledger_path)
    try:
        ledger.claim(
            validated_decision,
            principal_ref=principal_ref,
            consumed_at=as_of,
        )
    finally:
        ledger.close()
    return OperatorAuthorization(
        principal_ref=principal_ref,
        authorization_ref=authorization_ref,
        verified_by_host=True,
    )