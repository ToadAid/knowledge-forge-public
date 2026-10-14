"""Headless human-authorized promotion control plane.

This module deliberately lives outside the normal read-only Knowledge Forge
consumer CLI. Authorization is external: a trusted host authenticates the
human operator, constructs :class:`OperatorAuthorization`, and only then calls
this module. Actor/decision/reference strings are provenance labels and are
never treated as authentication by themselves.

The authoritative durable truth is the skill registry. The routing projection
returned here is rebuildable evidence derived from that registry; it is NOT a
consumer-specific catalog and must never overwrite one. Consumer adapters
refresh their own catalog/index from registry truth after promotion.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Mapping

from .consumer_discovery import (
    AUTHORITY,
    DISCOVERY_SURFACE,
    EXECUTION_AUTHORITY,
    build_discovery_descriptors,
    validate_discovery_descriptors,
)
from .lifecycle import (
    PROMOTED,
    VALIDATED,
    LifecycleError,
    transition_lifecycle,
    validate_lifecycle_record,
)
from .registry import (
    SkillRegistryError,
    load_skill_registry,
    register_promoted_skill,
    registry_bytes,
    save_skill_registry,
    validate_skill_registry,
)

OPERATOR_PROMOTION_SCHEMA_VERSION = "0.1"
ROUTING_PROJECTION_SCHEMA_VERSION = "0.1"
AUTHORIZATION_BOUNDARY = "EXTERNAL_HOST_AUTHENTICATION_REQUIRED"
PROMOTION_AUTHORITY = "HUMAN_OPERATOR_ONLY"
ROUTING_PROJECTION_KIND = "REBUILDABLE_ROUTING_PROJECTION_NOT_CONSUMER_CATALOG"


class OperatorPromotionError(ValueError):
    """Raised when promotion cannot preserve the operator/knowledge boundary."""


@dataclass(frozen=True)
class OperatorAuthorization:
    """Host-attested authorization passed into the privileged control plane.

    ``verified_by_host`` is not authentication performed by Knowledge Forge.
    It is an assertion from the already-authenticated host boundary. A model
    must never be given authority to construct a verified authorization object.
    """

    principal_ref: str
    authorization_ref: str
    verified_by_host: bool


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OperatorPromotionError(f"{label} must be a non-empty string")
    return value.strip()


def _canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OperatorPromotionError("value is not canonical JSON data") from exc


def _pretty_json_bytes(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(
                dict(value),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise OperatorPromotionError("value is not JSON serializable") from exc


def _sha256_ref(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _lifecycle_head(record: Mapping[str, Any]) -> str:
    events = record.get("events")
    if not isinstance(events, list) or not events:
        raise OperatorPromotionError("lifecycle requires event history")
    head = events[-1].get("event_sha256")
    return _nonempty(head, "lifecycle head event_sha256")


def validate_operator_authorization(value: OperatorAuthorization) -> None:
    if not isinstance(value, OperatorAuthorization):
        raise OperatorPromotionError(
            "authorization must be an OperatorAuthorization from a trusted host"
        )
    _nonempty(value.principal_ref, "principal_ref")
    _nonempty(value.authorization_ref, "authorization_ref")
    if value.verified_by_host is not True:
        raise OperatorPromotionError(
            "operator authorization was not verified by the external host"
        )


def build_operator_routing_projection(
    registry: Mapping[str, Any],
) -> dict[str, Any]:
    """Build zero-authority routing evidence from authoritative registry truth."""

    report = validate_skill_registry(registry)
    if not report["valid"]:
        raise OperatorPromotionError(
            "cannot build routing projection from invalid registry: "
            + "; ".join(report["errors"])
        )
    try:
        skills = build_discovery_descriptors(registry)
    except Exception as exc:
        raise OperatorPromotionError(
            f"cannot derive routing projection: {exc}"
        ) from exc

    projection = {
        "schema_version": ROUTING_PROJECTION_SCHEMA_VERSION,
        "projection_kind": ROUTING_PROJECTION_KIND,
        "surface": DISCOVERY_SURFACE,
        "registry_sha256": _sha256_ref(registry_bytes(registry)),
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
        "skills": skills,
    }
    projection_report = validate_operator_routing_projection(projection)
    if not projection_report["valid"]:
        raise OperatorPromotionError(
            "derived routing projection is invalid: "
            + "; ".join(projection_report["errors"])
        )
    return projection


def validate_operator_routing_projection(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    expected = {
        "schema_version",
        "projection_kind",
        "surface",
        "registry_sha256",
        "authority",
        "execution_authority",
        "tool_grants",
        "skills",
    }
    if not isinstance(projection, Mapping):
        return {"valid": False, "errors": ["routing projection must be a mapping"]}
    if set(projection) != expected:
        errors.append(
            "routing projection fields must match exact operator contract"
        )
    if projection.get("schema_version") != ROUTING_PROJECTION_SCHEMA_VERSION:
        errors.append("routing projection schema_version mismatch")
    if projection.get("projection_kind") != ROUTING_PROJECTION_KIND:
        errors.append("routing projection kind mismatch")
    if projection.get("surface") != DISCOVERY_SURFACE:
        errors.append("routing projection surface mismatch")
    registry_digest = projection.get("registry_sha256")
    digest = (
        registry_digest.removeprefix("sha256:")
        if isinstance(registry_digest, str)
        else ""
    )
    if (
        not isinstance(registry_digest, str)
        or not registry_digest.startswith("sha256:")
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        errors.append("routing projection registry_sha256 must be a sha256 reference")
    if projection.get("authority") != AUTHORITY:
        errors.append("routing projection authority mismatch")
    if projection.get("execution_authority") != EXECUTION_AUTHORITY:
        errors.append("routing projection execution_authority mismatch")
    if projection.get("tool_grants") != []:
        errors.append("routing projection must not grant tools")
    descriptor_report = validate_discovery_descriptors(projection.get("skills"))
    errors.extend(descriptor_report["errors"])
    return {"valid": not errors, "errors": errors}


def promote_validated_skill(
    registry: Mapping[str, Any],
    manifest: Mapping[str, Any],
    validated_lifecycle: Mapping[str, Any],
    claim_index: Mapping[str, Any],
    source_receipt: Mapping[str, Any],
    *,
    authorization: OperatorAuthorization,
    decision_ref: str,
    occurred_at: str,
) -> dict[str, Any]:
    """Promote one exact VALIDATED package without mutating caller-owned inputs."""

    validate_operator_authorization(authorization)
    decision = _nonempty(decision_ref, "decision_ref")
    timestamp = _nonempty(occurred_at, "occurred_at")

    lifecycle_report = validate_lifecycle_record(validated_lifecycle)
    if not lifecycle_report["valid"]:
        raise OperatorPromotionError(
            "validated lifecycle is invalid: "
            + "; ".join(lifecycle_report["errors"])
        )
    if validated_lifecycle.get("state") != VALIDATED:
        raise OperatorPromotionError(
            "operator promotion accepts VALIDATED lifecycle state only"
        )

    registry_report = validate_skill_registry(registry)
    if not registry_report["valid"]:
        raise OperatorPromotionError(
            "operator promotion requires a valid registry: "
            + "; ".join(registry_report["errors"])
        )

    before_registry_sha256 = _sha256_ref(registry_bytes(registry))
    before_lifecycle_head = _lifecycle_head(validated_lifecycle)

    try:
        promoted_lifecycle = transition_lifecycle(
            validated_lifecycle,
            PROMOTED,
            occurred_at=timestamp,
            actor=authorization.principal_ref,
            evidence={"decision_ref": decision},
        )
        updated_registry = register_promoted_skill(
            registry,
            manifest,
            promoted_lifecycle,
            claim_index,
            source_receipt,
            registered_at=timestamp,
        )
    except (LifecycleError, SkillRegistryError) as exc:
        raise OperatorPromotionError(f"promotion refused: {exc}") from exc

    routing_projection = build_operator_routing_projection(updated_registry)
    after_registry_sha256 = _sha256_ref(registry_bytes(updated_registry))
    after_lifecycle_head = _lifecycle_head(promoted_lifecycle)

    receipt = {
        "schema_version": OPERATOR_PROMOTION_SCHEMA_VERSION,
        "operation": "PROMOTE_VALIDATED_SKILL",
        "authorization_boundary": AUTHORIZATION_BOUNDARY,
        "promotion_authority": PROMOTION_AUTHORITY,
        "principal_ref": authorization.principal_ref.strip(),
        "authorization_ref": authorization.authorization_ref.strip(),
        "decision_ref": decision,
        "occurred_at": timestamp,
        "skill_id": promoted_lifecycle["skill_id"],
        "skill_version": promoted_lifecycle["skill_version"],
        "knowledge_id": promoted_lifecycle["knowledge_id"],
        "package_receipt_sha256": promoted_lifecycle["package_receipt_sha256"],
        "lifecycle_before_head_sha256": before_lifecycle_head,
        "lifecycle_after_head_sha256": after_lifecycle_head,
        "registry_before_sha256": before_registry_sha256,
        "registry_after_sha256": after_registry_sha256,
        "routing_projection_sha256": _sha256_ref(
            _canonical_json_bytes(routing_projection)
        ),
        "consumer_catalog_refresh_required": True,
        "authority": AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "tool_grants": [],
        "model_authorized": False,
    }

    return {
        "schema_version": OPERATOR_PROMOTION_SCHEMA_VERSION,
        "promoted_lifecycle": promoted_lifecycle,
        "registry": updated_registry,
        "routing_projection": routing_projection,
        "receipt": receipt,
    }


def _absolute_without_resolution(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def _reject_symlink_components(path: Path, label: str) -> None:
    """Reject every existing symlink component without resolving through it."""

    absolute = _absolute_without_resolution(path)
    anchor = Path(absolute.anchor)
    current = anchor
    parts = absolute.parts[1:] if absolute.anchor else absolute.parts
    for part in parts:
        current = current / part
        if not os.path.lexists(current):
            continue
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise OperatorPromotionError(f"{label} path is unreadable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise OperatorPromotionError(
                f"{label} may not traverse a symlink: {current}"
            )


def _prepare_output_target(
    path_value: str | Path,
    label: str,
    *,
    overwrite: bool,
) -> Path:
    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, label)

    if path.exists():
        if not path.is_file():
            raise OperatorPromotionError(f"{label} must be a regular file path")
        if not overwrite:
            raise OperatorPromotionError(
                f"{label} already exists; overwrite is forbidden"
            )

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise OperatorPromotionError(f"{label} parent is unavailable") from exc

    _reject_symlink_components(path.parent, f"{label} parent")
    return path


def _existing_registry_target(path_value: str | Path) -> Path:
    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, "registry_path")
    if not path.exists() or not path.is_file():
        raise OperatorPromotionError(
            "registry_path must be an existing regular registry file"
        )
    return path


def _atomic_write(path: Path, payload: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def persist_operator_promotion(
    result: Mapping[str, Any],
    *,
    registry_path: str | Path,
    promoted_lifecycle_path: str | Path,
    receipt_path: str | Path,
) -> dict[str, str]:
    """Persist one promotion with registry truth authoritative and receipt last.

    The on-disk registry must still match ``registry_before_sha256`` before any
    promotion artifact is written. This prevents a stale promotion plan from
    silently clobbering a newer registry.

    Consumer-specific catalogs are intentionally NOT written here. They are
    derived artifacts owned by consumer adapters and must refresh separately
    from the updated authoritative registry.
    """

    required = {
        "schema_version",
        "promoted_lifecycle",
        "registry",
        "routing_projection",
        "receipt",
    }
    if not isinstance(result, Mapping) or set(result) != required:
        raise OperatorPromotionError("promotion result fields mismatch")
    if result.get("schema_version") != OPERATOR_PROMOTION_SCHEMA_VERSION:
        raise OperatorPromotionError("promotion result schema_version mismatch")

    registry = result["registry"]
    routing_projection = result["routing_projection"]
    lifecycle = result["promoted_lifecycle"]
    receipt = result["receipt"]

    registry_report = validate_skill_registry(registry)
    if not registry_report["valid"]:
        raise OperatorPromotionError("refusing to persist invalid registry")
    projection_report = validate_operator_routing_projection(routing_projection)
    if not projection_report["valid"]:
        raise OperatorPromotionError(
            "refusing to persist invalid routing projection evidence"
        )
    lifecycle_report = validate_lifecycle_record(lifecycle)
    if not lifecycle_report["valid"] or lifecycle.get("state") != PROMOTED:
        raise OperatorPromotionError(
            "refusing to persist invalid promoted lifecycle"
        )
    if receipt.get("model_authorized") is not False:
        raise OperatorPromotionError(
            "promotion receipt cannot claim model authorization"
        )
    if receipt.get("consumer_catalog_refresh_required") is not True:
        raise OperatorPromotionError(
            "promotion receipt must require consumer catalog refresh"
        )
    if receipt.get("registry_after_sha256") != _sha256_ref(
        registry_bytes(registry)
    ):
        raise OperatorPromotionError(
            "promotion receipt registry digest mismatch"
        )
    if receipt.get("routing_projection_sha256") != _sha256_ref(
        _canonical_json_bytes(routing_projection)
    ):
        raise OperatorPromotionError(
            "promotion receipt routing projection digest mismatch"
        )

    # Stale-registry preflight occurs before creating any immutable outputs.
    registry_target = _existing_registry_target(registry_path)
    try:
        current_registry = load_skill_registry(registry_target)
    except SkillRegistryError as exc:
        raise OperatorPromotionError(
            f"current registry is invalid: {exc}"
        ) from exc
    current_registry_sha256 = _sha256_ref(registry_bytes(current_registry))
    if current_registry_sha256 != receipt.get("registry_before_sha256"):
        raise OperatorPromotionError(
            "registry changed since promotion was prepared; refusing stale overwrite"
        )

    lifecycle_target = _prepare_output_target(
        promoted_lifecycle_path,
        "promoted_lifecycle_path",
        overwrite=False,
    )
    receipt_target = _prepare_output_target(
        receipt_path,
        "receipt_path",
        overwrite=False,
    )

    targets = {
        "registry": registry_target,
        "promoted_lifecycle": lifecycle_target,
        "receipt": receipt_target,
    }
    if len(set(targets.values())) != len(targets):
        raise OperatorPromotionError("promotion output paths must be distinct")

    # Immutable lifecycle first, authoritative registry second, completion
    # receipt last. Receipt-last prevents a false claim of completed ceremony.
    _atomic_write(lifecycle_target, _pretty_json_bytes(lifecycle))

    # Recheck immediately before the authoritative replace. This is a bounded
    # stale-write defense for cooperating local writers; registry truth remains
    # the canonical recovery surface if a crash occurs after this point.
    current_registry = load_skill_registry(registry_target)
    current_registry_sha256 = _sha256_ref(registry_bytes(current_registry))
    if current_registry_sha256 != receipt.get("registry_before_sha256"):
        try:
            lifecycle_target.unlink()
        except OSError:
            pass
        raise OperatorPromotionError(
            "registry changed during promotion; refusing stale overwrite"
        )

    save_skill_registry(registry, registry_target)
    _atomic_write(receipt_target, _pretty_json_bytes(receipt))

    return {key: str(path) for key, path in targets.items()}
