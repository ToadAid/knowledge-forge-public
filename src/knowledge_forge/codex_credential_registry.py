"""Host-owned Codex user-verification credential bindings."""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from .codex_p256 import CodexP256Error, verify_p256_sha256
from .secure_io import read_bounded_bytes


SCHEMA_VERSION = "0.1"
REGISTRY_KIND = "KNOWLEDGE_FORGE_CODEX_CREDENTIAL_REGISTRY"


class CodexCredentialError(ValueError):
    """Credential enrollment or proof verification failed closed."""


def build_credential_registry(
    *, credential_id: str, public_key: str, principal: str, enrolled_at: str
) -> dict[str, Any]:
    if not all(isinstance(value, str) and value for value in (
        credential_id, public_key, principal, enrolled_at
    )):
        raise CodexCredentialError("credential binding fields must be nonempty strings")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": REGISTRY_KIND,
        "credentials": {
            credential_id: {
                "algorithm": "ES256",
                "public_key_spki_base64url": public_key,
                "principal": principal,
                "enrolled_at": enrolled_at,
            }
        },
    }


def save_credential_registry(value: Mapping[str, Any], path: str | Path) -> None:
    path = Path(path)
    if path.exists() or path.is_symlink():
        raise CodexCredentialError(
            f"credential registry already exists at {path}; refusing to overwrite"
        )
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = (json.dumps(dict(value), sort_keys=True, indent=2) + "\n").encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise CodexCredentialError("credential registry write failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def load_credential_registry(path: str | Path) -> dict[str, Any]:
    path = Path(path)
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise CodexCredentialError("credential registry must be a regular file")
        if os.name == "posix" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise CodexCredentialError(
                "credential registry owner/mode mismatch (expected 0600)"
            )
        raw = read_bounded_bytes(path, 256 * 1024, "credential registry")
        value = json.loads(raw.decode("utf-8"))
    except CodexCredentialError:
        raise
    except Exception as exc:
        raise CodexCredentialError(f"credential registry is unavailable: {path}") from exc
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "kind", "credentials"
    }:
        raise CodexCredentialError("credential registry fields are invalid")
    if value["schema_version"] != SCHEMA_VERSION or value["kind"] != REGISTRY_KIND:
        raise CodexCredentialError("credential registry identity is invalid")
    credentials = value["credentials"]
    if not isinstance(credentials, dict) or not credentials:
        raise CodexCredentialError("credential registry has no enrolled reviewer")
    return value


def verify_registered_proof(
    registry_path: str | Path,
    challenge: str,
    credential_id: str,
    signature: str,
) -> str:
    registry = load_credential_registry(registry_path)
    binding = registry["credentials"].get(credential_id)
    if not isinstance(binding, dict) or set(binding) != {
        "algorithm", "public_key_spki_base64url", "principal", "enrolled_at"
    }:
        raise CodexCredentialError("Codex credential is not enrolled for this host")
    if binding["algorithm"] != "ES256":
        raise CodexCredentialError("Codex credential algorithm is not ES256")
    principal = binding["principal"]
    if not isinstance(principal, str) or not principal:
        raise CodexCredentialError("Codex credential principal is invalid")
    try:
        verify_p256_sha256(
            challenge, signature, binding["public_key_spki_base64url"]
        )
    except CodexP256Error as exc:
        raise CodexCredentialError(str(exc)) from exc
    return principal
