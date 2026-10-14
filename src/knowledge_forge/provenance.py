"""Stable knowledge identity and compiler-profile provenance."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import re
from typing import Any, Mapping

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_KNOWLEDGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,255}$")


class ProvenanceError(ValueError):
    """Raised when semantic compilation provenance is incomplete or unstable."""


def normalize_knowledge_id(value: Any) -> str:
    if not isinstance(value, str) or not _KNOWLEDGE_ID_RE.fullmatch(value):
        raise ProvenanceError(
            "knowledge_id must be a stable lowercase identifier using letters, digits, '.', '_', ':', or '-'"
        )
    return value


def _sha256_ref(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ProvenanceError(f"{name} must be a sha256 reference")
    return value


def _nonempty(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProvenanceError(f"{name} must be a non-empty string")
    return value.strip()


def _canonical_parameters(parameters: Any) -> dict[str, Any]:
    if not isinstance(parameters, Mapping):
        raise ProvenanceError("compiler parameters must be a mapping")
    copied = deepcopy(dict(parameters))
    if any(not isinstance(key, str) or not key for key in copied):
        raise ProvenanceError("compiler parameter keys must be non-empty strings")
    try:
        json.dumps(
            copied,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProvenanceError("compiler parameters must be canonical JSON data") from exc
    return copied


def _profile_payload(
    *,
    compiler_digest: Any,
    compiler_version: Any,
    model_identity: Any,
    prompt_template_hash: Any,
    parameters: Any,
) -> dict[str, Any]:
    return {
        "compiler_digest": _sha256_ref(compiler_digest, "compiler_digest"),
        "compiler_version": _nonempty(compiler_version, "compiler_version"),
        "model_identity": _nonempty(model_identity, "model_identity"),
        "prompt_template_hash": _sha256_ref(
            prompt_template_hash, "prompt_template_hash"
        ),
        "parameters": _canonical_parameters(parameters),
    }


def _profile_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def build_compiler_profile(
    *,
    compiler_digest: str,
    compiler_version: str,
    model_identity: str,
    prompt_template_hash: str,
    parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Build a deterministic semantic compiler-profile identity."""

    payload = _profile_payload(
        compiler_digest=compiler_digest,
        compiler_version=compiler_version,
        model_identity=model_identity,
        prompt_template_hash=prompt_template_hash,
        parameters=parameters,
    )
    return {"compiler_profile_id": _profile_id(payload), **payload}


def normalize_compiler_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonicalize a supplied profile, including its derived id."""

    if not isinstance(profile, Mapping):
        raise ProvenanceError("compiler_profile must be a mapping")
    payload = _profile_payload(
        compiler_digest=profile.get("compiler_digest"),
        compiler_version=profile.get("compiler_version"),
        model_identity=profile.get("model_identity"),
        prompt_template_hash=profile.get("prompt_template_hash"),
        parameters=profile.get("parameters"),
    )
    expected = _profile_id(payload)
    supplied = profile.get("compiler_profile_id")
    if supplied != expected:
        raise ProvenanceError("compiler_profile_id does not match compiler profile content")
    return {"compiler_profile_id": expected, **payload}


def skill_id_for(knowledge_id: str, compiler_profile_id: str) -> str:
    """Bind one governed skill lineage to a knowledge subject and compiler profile."""

    knowledge = normalize_knowledge_id(knowledge_id)
    profile_id = _sha256_ref(compiler_profile_id, "compiler_profile_id")
    return f"knowledge-forge:{knowledge}@{profile_id}"
