"""Provider-neutral binding from native host approval events to promotion.

This module is called by trusted host code, never exposed as a model tool.  It
normalizes the one-shot approval vocabularies used by generic agent hosts,
binds the event to the exact prepared Knowledge Forge request, signs the
decision with host-owned key material, and delegates to the existing inline
promotion path.

The event fields are evidence supplied by the host; they do not create a trust
boundary by themselves.  A deployment is authenticated only when the caller,
key, registry, and this completion function are outside agent permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import re
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from .approval import approval_request_sha256
from .approval_host import build_host_decision, read_host_key_file
from .inline_approval import complete_inline_promotion, prepare_inline_approval


GENERIC_HOST_ADAPTER_SCHEMA_VERSION = "0.1"
GENERIC_HOST_APPROVAL_EVENT_KIND = "KNOWLEDGE_FORGE_NATIVE_HOST_APPROVAL_EVENT"

_EVENT_FIELDS = frozenset({
    "schema_version",
    "kind",
    "adapter_id",
    "approval_id",
    "request_sha256",
    "decision",
    "reviewer_principal",
    "decided_at",
    "authentication",
})
_AUTHENTICATION_FIELDS = frozenset({
    "mechanism",
    "one_shot",
    "bypass_active",
    "review_surface",
})
_APPROVAL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-]{7,199}$")
_PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:\-@]{1,160}$")
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class GenericHostAdapterError(ValueError):
    """A host event cannot be used for a Knowledge Forge decision."""


@dataclass(frozen=True)
class HostAdapterProfile:
    adapter_id: str
    host_id: str
    approval_decision: str
    rejection_decision: str
    authentication_mechanism: str
    review_surface: str


HOST_ADAPTER_PROFILES: Mapping[str, HostAdapterProfile] = MappingProxyType(
    {
        "generic-native-host": HostAdapterProfile(
            adapter_id="generic-native-host",
            host_id="generic-native-host",
            approval_decision="approve-once",
            rejection_decision="deny",
            authentication_mechanism="native-host-approval",
            review_surface="NATIVE_HOST_UI",
        ),
        "openclaw": HostAdapterProfile(
            adapter_id="openclaw",
            host_id="openclaw-gateway",
            approval_decision="allow-once",
            rejection_decision="deny",
            authentication_mechanism="plugin.approval",
            review_surface="OPENCLAW_OPERATOR_APPROVAL_UI",
        ),
        "hermes": HostAdapterProfile(
            adapter_id="hermes",
            host_id="hermes-host",
            approval_decision="once",
            rejection_decision="deny",
            authentication_mechanism="approval_transport",
            review_surface="HERMES_HOST_APPROVAL_UI",
        ),
        "claude": HostAdapterProfile(
            adapter_id="claude",
            host_id="claude-approval-card",
            approval_decision="approve-once",
            rejection_decision="deny",
            authentication_mechanism="desktop-card-approval",
            review_surface="CLAUDE_USER_APPROVAL_CARD",
        ),
        "codex": HostAdapterProfile(
            adapter_id="codex",
            host_id="codex-desktop",
            approval_decision="approve-once",
            rejection_decision="deny",
            authentication_mechanism="codex-user-verification",
            review_surface="CODEX_NATIVE_USER_VERIFICATION",
        ),
    }
)


def _profile(adapter_id: str) -> HostAdapterProfile:
    try:
        return HOST_ADAPTER_PROFILES[adapter_id]
    except (KeyError, TypeError) as exc:
        raise GenericHostAdapterError(
            f"unsupported host adapter: {adapter_id!r}"
        ) from exc


def _exact_fields(
    value: Mapping[str, Any], expected: frozenset[str], label: str
) -> None:
    if set(value) != set(expected):
        raise GenericHostAdapterError(f"{label} fields must match exact contract")


def _rfc3339_instant(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value or len(value) > 40:
        raise GenericHostAdapterError(f"{label} must be bounded RFC3339 text")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise GenericHostAdapterError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GenericHostAdapterError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def prepare_generic_host_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    adapter_id: str,
    observed_at: str,
) -> dict[str, Any]:
    """Return a host-specific, still non-authorizing approval card."""

    profile = _profile(adapter_id)
    card = prepare_inline_approval(
        request_dir, bundle, observed_at=observed_at
    )
    return {
        **card,
        "host_adapter": {
            "adapter_id": profile.adapter_id,
            "approval_decision": profile.approval_decision,
            "rejection_decision": profile.rejection_decision,
            "authentication_mechanism": profile.authentication_mechanism,
            "review_surface": profile.review_surface,
            "persistent_approval_allowed": False,
            "bypass_allowed": False,
        },
    }


def _validated_event(
    value: Mapping[str, Any],
    *,
    profile: HostAdapterProfile,
    expected_request_sha256: str,
    now: str,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise GenericHostAdapterError(
            "native host approval event must be a mapping; chat text cannot authorize"
        )
    event = dict(value)
    _exact_fields(event, _EVENT_FIELDS, "native host approval event")
    if event.get("schema_version") != GENERIC_HOST_ADAPTER_SCHEMA_VERSION:
        raise GenericHostAdapterError("unsupported native host approval schema_version")
    if event.get("kind") != GENERIC_HOST_APPROVAL_EVENT_KIND:
        raise GenericHostAdapterError("unsupported native host approval kind")
    if event.get("adapter_id") != profile.adapter_id:
        raise GenericHostAdapterError("native host approval adapter mismatch")
    approval_id = event.get("approval_id")
    if not isinstance(approval_id, str) or not _APPROVAL_ID_RE.fullmatch(approval_id):
        raise GenericHostAdapterError("approval_id must be bounded host-generated text")
    request_digest = event.get("request_sha256")
    if (
        not isinstance(request_digest, str)
        or not _SHA256_RE.fullmatch(request_digest)
        or request_digest != expected_request_sha256
    ):
        raise GenericHostAdapterError(
            "native host approval does not bind the prepared request"
        )
    principal = event.get("reviewer_principal")
    if not isinstance(principal, str) or not _PRINCIPAL_RE.fullmatch(principal):
        raise GenericHostAdapterError("reviewer_principal must be a bounded host principal")
    decided_at = event.get("decided_at")
    if _rfc3339_instant(decided_at, "decided_at") != _rfc3339_instant(now, "now"):
        raise GenericHostAdapterError(
            "native host decision time must match the trusted completion time"
        )

    authentication = event.get("authentication")
    if not isinstance(authentication, Mapping):
        raise GenericHostAdapterError("host authentication evidence must be a mapping")
    authentication = dict(authentication)
    _exact_fields(
        authentication, _AUTHENTICATION_FIELDS, "host authentication evidence"
    )
    if authentication.get("mechanism") != profile.authentication_mechanism:
        raise GenericHostAdapterError("host authentication mechanism mismatch")
    if authentication.get("review_surface") != profile.review_surface:
        raise GenericHostAdapterError("host review surface mismatch")
    if authentication.get("one_shot") is not True:
        raise GenericHostAdapterError(
            "persistent or reusable approval is forbidden for promotion"
        )
    if authentication.get("bypass_active") is not False:
        raise GenericHostAdapterError(
            "approval bypass/YOLO mode is forbidden for promotion"
        )
    decision = event.get("decision")
    if decision not in {profile.approval_decision, profile.rejection_decision}:
        raise GenericHostAdapterError(
            "host decision must be the adapter one-shot approval or denial token"
        )
    event["authentication"] = authentication
    return event


def complete_generic_host_approval(
    request_dir: str | Path,
    bundle: str | Path,
    *,
    host_event: Mapping[str, Any],
    adapter_id: str,
    registry_path: str | Path,
    host_key_path: str | Path,
    now: str,
) -> dict[str, Any]:
    """Complete one authenticated, one-shot native host decision.

    A denial returns a non-promoting terminal result.  Approval is signed with
    the host key and enters the existing single-use promotion path.
    """

    profile = _profile(adapter_id)
    card = prepare_generic_host_approval(
        request_dir, bundle, adapter_id=adapter_id, observed_at=now
    )
    event = _validated_event(
        host_event,
        profile=profile,
        expected_request_sha256=card["request_sha256"],
        now=now,
    )
    if event["decision"] == profile.rejection_decision:
        return {
            "schema_version": GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
            "status": "REJECTED",
            "adapter_id": profile.adapter_id,
            "approval_id": event["approval_id"],
            "request_sha256": event["request_sha256"],
            "reviewer_principal": event["reviewer_principal"],
            "promoted": False,
            "model_authorized": False,
            "user_command_required": False,
        }

    host_key = read_host_key_file(Path(host_key_path))
    nonce = sha256(
        f"{profile.adapter_id}\0{event['approval_id']}".encode("utf-8")
    ).hexdigest()
    request = _request_from_card_path(Path(request_dir))
    if approval_request_sha256(request) != event["request_sha256"]:
        raise GenericHostAdapterError(
            "approval request changed after native host review"
        )
    decision = build_host_decision(
        request,
        decision="APPROVED",
        decided_by=event["reviewer_principal"],
        host_id=profile.host_id,
        decision_nonce=nonce,
        decided_at=event["decided_at"],
        host_key=host_key,
    )
    result = complete_inline_promotion(
        request_dir,
        bundle,
        signed_host_decision=decision,
        registry_path=registry_path,
        host_key_path=host_key_path,
        now=now,
    )
    return {
        **result,
        "adapter_id": profile.adapter_id,
        "approval_id": event["approval_id"],
        "reviewer_principal": event["reviewer_principal"],
        "host_authentication_mechanism": profile.authentication_mechanism,
    }


def _request_from_card_path(request_dir: Path) -> dict[str, Any]:
    """Read the request with the same bounded/no-follow host primitive."""

    # Kept local to avoid exporting approval-host CLI internals as public API.
    from .approval_host_cli import _read_json_object

    return _read_json_object(
        request_dir / "approval-request.json", "approval request"
    )
