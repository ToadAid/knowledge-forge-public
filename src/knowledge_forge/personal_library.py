"""Personal Forge P1: local personal knowledge library for VALIDATED skills.

PERSONAL MODE is a local-first, ordinary-user surface beside the governed
promotion path. It never touches the PROMOTED registry, the operator surface,
or any authority-bearing mechanic. A personal entry is a digest-bound
reference to an already VALIDATED package plus a stored copy of its validated
lifecycle record; lookup revalidates every entry against the actual package
bytes and returns strictly untrusted knowledge data.

``--accept`` records explicit conversational acceptance. It is not proof of
authenticated human identity, and library state is stamped to say so:
ACCEPTED != AUTHENTICATED, VALIDATED != PROMOTED.

Writers (add/remove) take a portable advisory lock for the whole
read-modify-write transaction; on contention they refuse instead of ever
silently dropping another writer's entry. Package, lifecycle, and library
reads are size-bounded before reading and fail closed if files change
during inspection.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import time
from typing import Any, Mapping

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from .consumer_discovery import GLOBAL_HARD_MAX_SKILLS_PER_QUERY
from .lifecycle import (
    VALIDATED,
    evidence_sha256,
    validate_lifecycle_record,
)
from .retrieval import (
    HARD_MAX_UNITS_PER_TURN,
    RetrievalError,
    route_question,
)
from .trust_wall import (
    AUTHORITY,
    EXECUTION_AUTHORITY,
    INSTRUCTION_AUTHORITY,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILES,
    TRUST_CLASS,
)
from .validate import PackageValidationError, validate_or_raise

PERSONAL_LIBRARY_SCHEMA_VERSION = "0.1"
PERSONAL_LIBRARY_KIND = "KNOWLEDGE_FORGE_PERSONAL_LIBRARY"
PERSONAL_LIBRARY_FILENAME = "personal-library.json"
PERSONAL_KNOWLEDGE_MODE = "LOCAL_PERSONAL_NOT_AUTHENTICATED_PROMOTION"
PERSONAL_ACCEPTANCE_CHANNEL = (
    "CONVERSATIONAL_ACCEPTANCE_FLAG_NOT_AUTHENTICATED_HUMAN"
)
PERSONAL_TOOL_POLICY_RULE = "PERSONAL_MODE_GRANTS_NO_TOOLS"
PERSONAL_LOCK_FILENAME = "personal-library.lock"
_PERSONAL_LOCK_TIMEOUT_SECONDS = 10.0
_PERSONAL_SCAN_ENTRY_LIMIT = 4096
_MAX_LIFECYCLE_RECORD_BYTES = 16 * 1024 * 1024
_MAX_LIBRARY_BYTES = 64 * 1024 * 1024

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "who", "why", "with",
})

_ENTRY_FIELDS = (
    "skill_id",
    "skill_version",
    "knowledge_id",
    "title",
    "source_ids",
    "candidate_sha256",
    "package_receipt_sha256",
    "lifecycle_head_sha256",
    "lifecycle_state",
    "package_root",
    "artifact_count",
    "artifact_sha256",
    "added_at",
    "acceptance",
    "lifecycle_record",
)

_ACCEPTANCE_FIELDS = (
    "accepted",
    "acceptance_channel",
    "authenticated_human",
    "accepted_at",
    "acceptance_evidence",
)


class PersonalLibraryError(ValueError):
    """Raised when a personal library operation would break the boundary."""


def _normalized_phrase(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip().lower())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN_RE.findall(value.lower())
        if token not in _STOPWORDS
    )


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _nonempty(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonalLibraryError(f"{label} must be a non-empty string")
    return value


def _sha256_ref(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def default_library_path() -> Path:
    """Default personal-library location under the user state root."""

    state = os.environ.get("XDG_STATE_HOME")
    base = Path(state).expanduser() if state else Path.home() / ".local" / "state"
    return base / "knowledge-forge" / "personal" / PERSONAL_LIBRARY_FILENAME


def _absolute_without_resolution(path_value: str | Path) -> Path:
    path = Path(path_value).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.absolute()


def _reject_symlink_components(path: Path, label: str) -> None:
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
            raise PersonalLibraryError(f"{label} path is unreadable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise PersonalLibraryError(
                f"{label} may not traverse a symlink: {current}"
            )


def _acquire_write_lock(descriptor: int) -> None:
    """Try to take the advisory writer lock; refuse after a bounded wait."""

    deadline = time.monotonic() + _PERSONAL_LOCK_TIMEOUT_SECONDS
    while True:
        try:
            if sys.platform == "win32":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except OSError as exc:
            if time.monotonic() >= deadline:
                raise PersonalLibraryError(
                    "another personal library writer is active; operation "
                    "refused without modifying the library"
                ) from exc
            time.sleep(0.05)


def _release_write_lock(descriptor: int) -> None:
    """Release the advisory writer lock taken by _acquire_write_lock.

    On native Windows a byte-range lock survives a plain close of another
    handle; it must be unlocked through the handle that holds it. Callers
    that never acquired (lock-refusal path) may surface a not-locked error,
    which the context manager swallows so a refusal or a completed save is
    never masked by the cleanup.
    """

    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)


@contextmanager
def _library_write_lock(path: Path):
    """Single-writer coordination for the library read-modify-write
    transaction (personal add / personal remove).

    An advisory lock file next to the library — never deleted, content
    meaningless — serializes writers across processes on both POSIX
    (fcntl.flock) and native Windows (msvcrt.locking). Readers stay
    lock-free: persistence is atomic via os.replace, so every read observes
    exactly one complete library file. On lock timeout the operation is
    explicitly refused and the library is left untouched; nothing silently
    overwrites another writer's changes.
    """

    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise PersonalLibraryError(
            "personal library parent is unavailable"
        ) from exc
    lock_path = path.parent / PERSONAL_LOCK_FILENAME
    _reject_symlink_components(lock_path, "personal library lock")
    try:
        open_flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, open_flags, 0o600)
    except OSError as exc:
        raise PersonalLibraryError(
            "personal library lock is unavailable"
        ) from exc
    try:
        _acquire_write_lock(descriptor)
        yield
    finally:
        try:
            _release_write_lock(descriptor)
        except OSError:
            # Cleanup must not mask a refusal or an already-saved result.
            pass
        os.close(descriptor)


def _read_bounded_bytes(path: Path, limit: int, label: str) -> bytes:
    """Read a regular file only after pre-checking its size; fail closed
    if the file changes between inspection and the completed read."""

    try:
        info = os.stat(path)
    except OSError as exc:
        raise PersonalLibraryError(f"{label} is unreadable: {path}") from exc
    if not stat.S_ISREG(info.st_mode):
        raise PersonalLibraryError(f"{label} must be a regular file: {path}")
    if info.st_size > limit:
        raise PersonalLibraryError(
            f"{label} exceeds {limit} byte limit: {path}"
        )
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise PersonalLibraryError(f"{label} is unreadable: {path}") from exc
    if len(payload) != info.st_size:
        raise PersonalLibraryError(f"{label} changed while being read: {path}")
    return payload


def _snapshot_package(package_root: str | Path) -> dict[str, bytes]:
    """Bounded package snapshot.

    Limits are enforced incrementally so a hostile or oversized tree is
    refused before exhausting resources: each artifact's size is checked
    before it is read, the file/byte caps apply as soon as they are passed,
    the directory scan is capped and never materializes the full tree list,
    and a post-read size check fails closed when an artifact changes during
    inspection. The captured bytes are exactly the bytes later hashed and
    validated.
    """

    root = Path(package_root)
    if root.is_symlink() or not root.is_dir():
        raise PersonalLibraryError(
            "personal package root must be a non-symlink directory"
        )

    files: dict[str, bytes] = {}
    total = 0
    scanned = 0
    for path in root.rglob("*"):
        scanned += 1
        if scanned > _PERSONAL_SCAN_ENTRY_LIMIT:
            raise PersonalLibraryError(
                "personal package exceeds "
                f"{_PERSONAL_SCAN_ENTRY_LIMIT} directory entry scan limit"
            )
        if path.is_symlink():
            raise PersonalLibraryError(
                "personal package must not contain symlinks"
            )
        if path.is_dir():
            continue
        if not path.is_file():
            raise PersonalLibraryError(
                "personal package contains a non-regular artifact"
            )
        relative = path.relative_to(root).as_posix()
        try:
            info = path.stat()
        except OSError as exc:
            raise PersonalLibraryError(
                f"personal package artifact is unreadable: {relative}"
            ) from exc
        if info.st_size > MAX_PACKAGE_BYTES:
            raise PersonalLibraryError(
                f"personal package artifact exceeds {MAX_PACKAGE_BYTES} "
                f"byte limit: {relative}"
            )
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise PersonalLibraryError(
                f"personal package artifact is unreadable: {relative}"
            ) from exc
        if len(payload) != info.st_size:
            raise PersonalLibraryError(
                "personal package artifact changed while being inspected: "
                f"{relative}"
            )
        files[relative] = payload
        total += len(payload)
        if len(files) > MAX_PACKAGE_FILES:
            raise PersonalLibraryError(
                f"personal package exceeds {MAX_PACKAGE_FILES} file limit"
            )
        if total > MAX_PACKAGE_BYTES:
            raise PersonalLibraryError(
                f"personal package exceeds {MAX_PACKAGE_BYTES} byte limit"
            )

    if not files:
        raise PersonalLibraryError("personal package is empty")
    return files


def _json_object(payload: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PersonalLibraryError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise PersonalLibraryError(f"{name} must be a JSON object")
    return value


def _artifact_digests(files: Mapping[str, bytes]) -> dict[str, str]:
    return {
        relative: _sha256_ref(files[relative]) for relative in sorted(files)
    }


def _library_bytes(library: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(library),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    ).encode("utf-8")


def _validate_library_shape(library: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(library, Mapping):
        return ["personal library must be a JSON object"]
    top_fields = {"schema_version", "kind", "entries_sha256", "entries"}
    if set(library) != top_fields:
        errors.append(
            f"personal library field set must be exactly {sorted(top_fields)}"
        )
        return errors
    if library.get("schema_version") != PERSONAL_LIBRARY_SCHEMA_VERSION:
        errors.append("personal library schema_version mismatch")
    if library.get("kind") != PERSONAL_LIBRARY_KIND:
        errors.append("personal library kind mismatch")
    entries = library.get("entries")
    if not isinstance(entries, Mapping):
        errors.append("personal library entries must be an object")
        return errors
    for skill_id, entry in entries.items():
        entry_errors = _validate_entry_shape(entry)
        for message in entry_errors:
            errors.append(f"entry {skill_id}: {message}")
    if not errors:
        expected = evidence_sha256(dict(entries))
        if library.get("entries_sha256") != expected:
            errors.append("personal library entries digest mismatch")
    return errors


def _validate_entry_shape(entry: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(entry, Mapping):
        return ["entry must be an object"]
    if set(entry) != set(_ENTRY_FIELDS):
        errors.append(
            f"entry field set must be exactly {sorted(_ENTRY_FIELDS)}"
        )
        return errors
    if not isinstance(entry.get("skill_id"), str) or not entry["skill_id"]:
        errors.append("skill_id must be a non-empty string")
    if not isinstance(entry.get("skill_version"), int) or isinstance(
        entry.get("skill_version"), bool
    ):
        errors.append("skill_version must be an integer")
    if not isinstance(
        entry.get("knowledge_id"), str
    ) or not entry["knowledge_id"]:
        errors.append("knowledge_id must be a non-empty string")
    if not isinstance(entry.get("title"), str) or not entry["title"]:
        errors.append("title must be a non-empty string")
    if not isinstance(entry.get("source_ids"), list):
        errors.append("source_ids must be a list")
    digest_fields = (
        "candidate_sha256",
        "package_receipt_sha256",
        "lifecycle_head_sha256",
    )
    for field in digest_fields:
        value = entry.get(field)
        if (
            not isinstance(value, str)
            or not value.startswith("sha256:")
            or len(value) != 71
        ):
            errors.append(f"{field} must be a sha256: reference")
    if entry.get("lifecycle_state") != VALIDATED:
        errors.append("lifecycle_state must remain VALIDATED")
    if not isinstance(entry.get("package_root"), str) or not entry[
        "package_root"
    ]:
        errors.append("package_root must be a non-empty string")
    artifact_count = entry.get("artifact_count")
    if (
        not isinstance(artifact_count, int)
        or isinstance(artifact_count, bool)
        or artifact_count < 1
    ):
        errors.append("artifact_count must be a positive integer")
    if not isinstance(entry.get("artifact_sha256"), Mapping):
        errors.append("artifact_sha256 must be an object")
    if not isinstance(entry.get("added_at"), str) or not entry["added_at"]:
        errors.append("added_at must be a non-empty string")
    acceptance = entry.get("acceptance")
    if not isinstance(acceptance, Mapping):
        errors.append("acceptance must be an object")
    else:
        if set(acceptance) != set(_ACCEPTANCE_FIELDS):
            errors.append(
                "acceptance field set must be exactly "
                f"{sorted(_ACCEPTANCE_FIELDS)}"
            )
        if acceptance.get("accepted") is not True:
            errors.append("acceptance.accepted must be true")
        if acceptance.get("acceptance_channel") != PERSONAL_ACCEPTANCE_CHANNEL:
            errors.append("acceptance channel mismatch")
        if acceptance.get("authenticated_human") is not False:
            errors.append(
                "acceptance.authenticated_human must remain false"
            )
    if not isinstance(entry.get("lifecycle_record"), Mapping):
        errors.append("lifecycle_record must be an object")
    return errors


def load_personal_library(path_value: str | Path) -> dict[str, Any]:
    """Load a personal library fail-closed; never creates the file."""

    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, "personal library")
    if not path.is_file():
        raise PersonalLibraryError(
            f"personal library does not exist: {path}"
        )
    payload = _read_bounded_bytes(
        path, _MAX_LIBRARY_BYTES, "personal library"
    )
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PersonalLibraryError(
            f"cannot read personal library: {path}"
        ) from exc
    errors = _validate_library_shape(value)
    if errors:
        raise PersonalLibraryError(
            "personal library validation failed: " + "; ".join(errors)
        )
    return value


def _save_library(library: Mapping[str, Any], path_value: str | Path) -> Path:
    """Atomically persist personal library state (0700 dir, 0600 file)."""

    path = _absolute_without_resolution(path_value)
    _reject_symlink_components(path, "personal library")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise PersonalLibraryError(
            "personal library parent is unavailable"
        ) from exc
    _reject_symlink_components(path.parent, "personal library parent")
    if path.exists() and not path.is_file():
        raise PersonalLibraryError(
            "personal library target must be a regular file"
        )

    payload = _library_bytes(library)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise
    return path


def _empty_library() -> dict[str, Any]:
    return {
        "schema_version": PERSONAL_LIBRARY_SCHEMA_VERSION,
        "kind": PERSONAL_LIBRARY_KIND,
        "entries_sha256": evidence_sha256({}),
        "entries": {},
    }


def _library_for_write(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_library()
    return load_personal_library(path)


def _validate_or_raise(files: Mapping[str, bytes]) -> dict[str, Any]:
    try:
        return validate_or_raise(files)
    except PackageValidationError as exc:
        raise PersonalLibraryError(
            f"personal package validation failed: {exc}"
        ) from exc


def _check_receipt_binding(
    record: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    receipt_digest: str,
    label: str,
) -> None:
    if receipt_digest != record.get("package_receipt_sha256"):
        raise PersonalLibraryError(
            f"{label}: lifecycle package receipt does not match exact "
            "package source receipt"
        )
    events = record.get("events")
    genesis_evidence = None
    if isinstance(events, list) and events:
        genesis = events[0]
        if isinstance(genesis, Mapping):
            evidence = genesis.get("evidence")
            if isinstance(evidence, Mapping):
                genesis_evidence = evidence.get("package_receipt_sha256")
    if genesis_evidence != receipt_digest:
        raise PersonalLibraryError(
            f"{label}: lifecycle genesis binding does not match exact "
            "package source receipt"
        )


def _check_identity_binding(
    manifest: Mapping[str, Any],
    record: Mapping[str, Any],
) -> None:
    for field in (
        "skill_id",
        "skill_version",
        "knowledge_id",
        "source_ids",
        "candidate_sha256",
    ):
        if manifest.get(field) != record.get(field):
            raise PersonalLibraryError(
                f"manifest {field} does not match validated lifecycle record"
            )
    units = manifest.get("units")
    manifest_unit_ids = sorted(
        item.get("unit_id")
        for item in units
        if isinstance(item, Mapping)
    ) if isinstance(units, list) else []
    record_unit_ids = record.get("unit_ids")
    if not isinstance(record_unit_ids, list):
        raise PersonalLibraryError(
            "validated lifecycle record requires unit_ids"
        )
    if manifest_unit_ids != sorted(record_unit_ids):
        raise PersonalLibraryError(
            "manifest unit set does not match validated lifecycle record"
        )
    title = manifest.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PersonalLibraryError("manifest title must be a non-empty string")
    if manifest.get("authority") != AUTHORITY:
        raise PersonalLibraryError(
            "manifest authority must remain KNOWLEDGE_ONLY"
        )


def add_personal_entry(
    package_root: str | Path,
    lifecycle_path: str | Path,
    *,
    library_path: str | Path | None = None,
    accept: bool = False,
    added_at: str | None = None,
) -> dict[str, Any]:
    """Accept a VALIDATED package into the personal library, fail-closed."""

    if accept is not True:
        raise PersonalLibraryError(
            "explicit acceptance is required: pass --accept after the user "
            "has agreed; acceptance is conversational and is not "
            "authenticated human identity"
        )
    stamp = _now_stamp() if added_at is None else _nonempty(
        added_at, "added_at"
    )
    path = _absolute_without_resolution(
        library_path if library_path is not None else default_library_path()
    )

    root = _absolute_without_resolution(package_root)
    files = _snapshot_package(root)

    record_path = _absolute_without_resolution(lifecycle_path)
    _reject_symlink_components(record_path, "validated lifecycle")
    if not record_path.is_file():
        raise PersonalLibraryError(
            f"validated lifecycle is not an existing regular file: "
            f"{record_path}"
        )
    record_bytes = _read_bounded_bytes(
        record_path, _MAX_LIFECYCLE_RECORD_BYTES, "validated lifecycle"
    )
    record = _json_object(record_bytes, name="validated lifecycle")
    report = validate_lifecycle_record(record)
    if not report["valid"]:
        raise PersonalLibraryError(
            "validated lifecycle record is invalid: "
            + "; ".join(report["errors"])
        )
    if record.get("state") != VALIDATED:
        raise PersonalLibraryError(
            "validated lifecycle state must be exactly VALIDATED, got "
            f"{record.get('state')}"
        )

    _validate_or_raise(files)
    manifest = _json_object(files["manifest.json"], name="manifest.json")
    receipt = _json_object(
        files["SOURCE_RECEIPT.json"], name="SOURCE_RECEIPT.json"
    )
    receipt_digest = evidence_sha256(receipt)
    _check_receipt_binding(
        record,
        receipt,
        receipt_digest=receipt_digest,
        label="add",
    )
    _check_identity_binding(manifest, record)

    events = record["events"]
    entry = {
        "skill_id": manifest["skill_id"],
        "skill_version": manifest["skill_version"],
        "knowledge_id": manifest["knowledge_id"],
        "title": manifest["title"],
        "source_ids": deepcopy(manifest["source_ids"]),
        "candidate_sha256": manifest["candidate_sha256"],
        "package_receipt_sha256": receipt_digest,
        "lifecycle_head_sha256": events[-1]["event_sha256"],
        "lifecycle_state": record["state"],
        "package_root": root.as_posix(),
        "artifact_count": len(files),
        "artifact_sha256": _artifact_digests(files),
        "added_at": stamp,
        "acceptance": {
            "accepted": True,
            "acceptance_channel": PERSONAL_ACCEPTANCE_CHANNEL,
            "authenticated_human": False,
            "accepted_at": stamp,
            "acceptance_evidence": (
                "explicit --accept flag on knowledge-forge personal add"
            ),
        },
        "lifecycle_record": deepcopy(dict(record)),
    }
    skill_id = manifest["skill_id"]
    with _library_write_lock(path):
        library = _library_for_write(path)
        if skill_id in library["entries"]:
            raise PersonalLibraryError(
                f"skill_id '{skill_id}' is already in the personal library; "
                "remove it before re-adding"
            )
        library["entries"][skill_id] = entry
        library["entries_sha256"] = evidence_sha256(dict(library["entries"]))
        _save_library(library, path)

    return {
        "schema_version": PERSONAL_LIBRARY_SCHEMA_VERSION,
        "ok": True,
        "command": "personal-add",
        "skill_id": skill_id,
        "skill_version": entry["skill_version"],
        "knowledge_id": entry["knowledge_id"],
        "title": entry["title"],
        "library": str(path),
        "package_root": entry["package_root"],
        "lifecycle_state": entry["lifecycle_state"],
        "package_receipt_sha256": receipt_digest,
        "lifecycle_head_sha256": entry["lifecycle_head_sha256"],
        "artifact_count": entry["artifact_count"],
        "added_at": entry["added_at"],
        "acceptance": deepcopy(entry["acceptance"]),
        "trust_class": TRUST_CLASS,
        "authority": AUTHORITY,
        "instruction_authority": INSTRUCTION_AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "executable": False,
        "tool_grants": [],
        "knowledge_mode": PERSONAL_KNOWLEDGE_MODE,
        "promotion_performed": False,
    }


def _require_library(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise PersonalLibraryError(
            f"personal library does not exist: {path}"
        )
    return load_personal_library(path)


def list_personal_entries(
    library_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Summarize personal entries; digests only, never knowledge content."""

    path = _absolute_without_resolution(
        library_path if library_path is not None else default_library_path()
    )
    library = _require_library(path)
    summaries: list[dict[str, Any]] = []
    for skill_id in sorted(library["entries"]):
        entry = library["entries"][skill_id]
        acceptance = dict(entry["acceptance"])
        summaries.append({
            "skill_id": entry["skill_id"],
            "skill_version": entry["skill_version"],
            "knowledge_id": entry["knowledge_id"],
            "title": entry["title"],
            "lifecycle_state": entry["lifecycle_state"],
            "package_receipt_sha256": entry["package_receipt_sha256"],
            "lifecycle_head_sha256": entry["lifecycle_head_sha256"],
            "artifact_count": entry["artifact_count"],
            "added_at": entry["added_at"],
            "package_root": entry["package_root"],
            "acceptance": {
                "accepted": acceptance.get("accepted"),
                "acceptance_channel": acceptance.get("acceptance_channel"),
                "authenticated_human": acceptance.get(
                    "authenticated_human"
                ),
                "accepted_at": acceptance.get("accepted_at"),
            },
        })
    return summaries


def remove_personal_entry(
    skill_id: str,
    *,
    library_path: str | Path | None = None,
) -> dict[str, Any]:
    """Remove a library reference; package files are never touched."""

    skill_id = _nonempty(skill_id, "skill_id")
    path = _absolute_without_resolution(
        library_path if library_path is not None else default_library_path()
    )
    if not path.exists():
        raise PersonalLibraryError(
            f"personal library does not exist: {path}"
        )
    with _library_write_lock(path):
        library = load_personal_library(path)
        if skill_id not in library["entries"]:
            raise PersonalLibraryError(
                f"skill is not in the personal library: {skill_id}"
            )
        del library["entries"][skill_id]
        library["entries_sha256"] = evidence_sha256(dict(library["entries"]))
        _save_library(library, path)
    return {
        "schema_version": PERSONAL_LIBRARY_SCHEMA_VERSION,
        "ok": True,
        "command": "personal-remove",
        "skill_id": skill_id,
        "removed": True,
        "package_files_untouched": True,
        "remaining_count": len(library["entries"]),
        "knowledge_mode": PERSONAL_KNOWLEDGE_MODE,
    }


def _revalidated_entry(
    entry: Mapping[str, Any],
    *,
    skill_id: str,
) -> dict[str, Any]:
    """Revalidate one entry against the actual package bytes."""

    root = entry.get("package_root")
    if not isinstance(root, str) or not root:
        raise PersonalLibraryError(f"entry has no package root: {skill_id}")
    files = _snapshot_package(root)
    _validate_or_raise(files)
    manifest = _json_object(files["manifest.json"], name="manifest.json")
    receipt = _json_object(
        files["SOURCE_RECEIPT.json"], name="SOURCE_RECEIPT.json"
    )
    claim_index = _json_object(
        files["claim-index.json"], name="claim-index.json"
    )
    retrieval_index = _json_object(
        files["retrieval-index.json"], name="retrieval-index.json"
    )

    record = entry.get("lifecycle_record")
    if not isinstance(record, Mapping):
        raise PersonalLibraryError(
            f"entry lifecycle record is missing: {skill_id}"
        )
    receipt_digest = evidence_sha256(receipt)
    _check_receipt_binding(
        record,
        receipt,
        receipt_digest=receipt_digest,
        label=f"lookup {skill_id}",
    )
    report = validate_lifecycle_record(record)
    if not report["valid"]:
        raise PersonalLibraryError(
            f"stored lifecycle record is invalid for {skill_id}: "
            + "; ".join(report["errors"])
        )
    if record.get("state") != VALIDATED:
        raise PersonalLibraryError(
            f"stored lifecycle state must be exactly VALIDATED for "
            f"{skill_id}, got {record.get('state')}"
        )
    _check_identity_binding(manifest, record)
    return {
        "manifest": manifest,
        "receipt": receipt,
        "claim_index": claim_index,
        "retrieval_index": retrieval_index,
        "files": files,
    }


def _lookup_stamp_constants() -> dict[str, Any]:
    return {
        "trust_class": TRUST_CLASS,
        "authority": AUTHORITY,
        "instruction_authority": INSTRUCTION_AUTHORITY,
        "execution_authority": EXECUTION_AUTHORITY,
        "executable": False,
        "knowledge_mode": PERSONAL_KNOWLEDGE_MODE,
        "promotion_performed": False,
    }


def lookup_personal(
    question: str,
    *,
    library_path: str | Path | None = None,
    max_skills: int = 1,
    max_units: int = 1,
) -> dict[str, Any]:
    """Bounded, revalidated personal retrieval. Never grants authority."""

    question = _nonempty(question, "question")
    if (
        not isinstance(max_skills, int)
        or isinstance(max_skills, bool)
        or not 1 <= max_skills <= GLOBAL_HARD_MAX_SKILLS_PER_QUERY
    ):
        raise PersonalLibraryError(
            f"max_skills must be between 1 and "
            f"{GLOBAL_HARD_MAX_SKILLS_PER_QUERY}"
        )
    if (
        not isinstance(max_units, int)
        or isinstance(max_units, bool)
        or not 1 <= max_units <= HARD_MAX_UNITS_PER_TURN
    ):
        raise PersonalLibraryError(
            f"max_units must be between 1 and {HARD_MAX_UNITS_PER_TURN}"
        )

    path = _absolute_without_resolution(
        library_path if library_path is not None else default_library_path()
    )
    library = _require_library(path)
    entries = library["entries"]
    if not entries:
        raise PersonalLibraryError("personal library has no entries")

    entry_errors: list[dict[str, str]] = []
    surviving: list[tuple[str, dict[str, Any]]] = []
    for skill_id in sorted(entries):
        entry = entries[skill_id]
        try:
            revalidated = _revalidated_entry(entry, skill_id=skill_id)
        except PersonalLibraryError as exc:
            entry_errors.append(
                {"skill_id": skill_id, "error": str(exc)}
            )
            continue
        surviving.append((skill_id, revalidated))

    scored: list[
        tuple[int, int, str, dict[str, Any], dict[str, Any], list[dict[str, Any]]]
    ] = []
    for skill_id, revalidated in surviving:
        try:
            routed = route_question(
                revalidated["retrieval_index"],
                question,
                max_units=HARD_MAX_UNITS_PER_TURN,
            )
        except RetrievalError:
            continue
        entry = entries[skill_id]
        query_phrase = _normalized_phrase(question)
        query_tokens = set(_tokens(question))
        exact_alias = max(
            (
                len(_tokens(alias))
                for unit in routed
                for alias in unit.get("aliases", [])
                if isinstance(alias, str)
                and alias
                and _normalized_phrase(alias) in query_phrase
            ),
            default=0,
        )
        overlap = len(
            query_tokens
            & {
                token
                for unit in routed
                for token in unit.get("terms", [])
            }
        )
        scored.append((exact_alias, overlap, skill_id, entry, revalidated, routed))

    if not scored:
        # Distinguish a fully-broken library from an honest no-match.
        if entry_errors and not surviving:
            raise PersonalLibraryError(
                "no personal library entry could be revalidated: "
                + "; ".join(
                    f"{item['skill_id']}: {item['error']}"
                    for item in entry_errors
                )
            )
        raise PersonalLibraryError(
            "no personal library entry matched the question"
        )

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    selected = scored[:max_skills]

    reasoning_context: list[dict[str, Any]] = []
    loaded_unit_ids: list[str] = []
    selected_skills: list[dict[str, Any]] = []
    for _, _, skill_id, entry, revalidated, routed in selected:
        manifest = revalidated["manifest"]
        claim_index = revalidated["claim_index"]
        files = revalidated["files"]
        record = entry["lifecycle_record"]
        selected_skills.append({
            "skill_id": entry["skill_id"],
            "skill_version": entry["skill_version"],
            "knowledge_id": entry["knowledge_id"],
            "title": entry["title"],
            "package_receipt_sha256": record["package_receipt_sha256"],
            "lifecycle_head_sha256": record["events"][-1]["event_sha256"],
        })
        manifest_units = manifest.get("units")
        units_by_id = {
            item.get("unit_id"): item
            for item in manifest_units
            if isinstance(item, Mapping)
        } if isinstance(manifest_units, list) else {}
        claim_items = claim_index.get("claims")
        claims_by_id = {
            item.get("unit_id"): item
            for item in claim_items
            if isinstance(item, Mapping)
        } if isinstance(claim_items, list) else {}

        taken = 0
        for routed_unit in routed:
            if taken >= max_units or len(loaded_unit_ids) >= (
                HARD_MAX_UNITS_PER_TURN
            ):
                break
            unit_id = routed_unit["unit_id"]
            manifest_unit = units_by_id.get(unit_id)
            claim_item = claims_by_id.get(unit_id)
            if not isinstance(manifest_unit, Mapping):
                raise PersonalLibraryError(
                    f"selected unit missing from manifest: {unit_id}"
                )
            if not isinstance(claim_item, Mapping):
                raise PersonalLibraryError(
                    f"selected unit missing from claim index: {unit_id}"
                )
            if manifest_unit.get("executable") is not False:
                raise PersonalLibraryError(
                    f"selected unit became executable: {unit_id}"
                )
            claim = claim_item.get("claim")
            if (
                not isinstance(claim, Mapping)
                or claim.get("executable") is not False
            ):
                raise PersonalLibraryError(
                    f"selected claim became executable: {unit_id}"
                )
            artifact_path = manifest_unit.get("path")
            if not isinstance(artifact_path, str) or artifact_path not in files:
                raise PersonalLibraryError(
                    f"selected unit artifact is missing: {unit_id}"
                )
            try:
                content = files[artifact_path].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise PersonalLibraryError(
                    f"selected unit artifact is not UTF-8: {unit_id}"
                ) from exc
            source_bindings = deepcopy(claim_item.get("source_bindings"))
            reasoning_context.append({
                "trust_class": TRUST_CLASS,
                "authority": AUTHORITY,
                "instruction_authority": INSTRUCTION_AUTHORITY,
                "execution_authority": EXECUTION_AUTHORITY,
                "executable": False,
                "knowledge_mode": PERSONAL_KNOWLEDGE_MODE,
                "promotion_performed": False,
                "skill_id": manifest["skill_id"],
                "skill_version": manifest["skill_version"],
                "knowledge_id": manifest["knowledge_id"],
                "unit_id": unit_id,
                "claim_key": claim_item.get("claim_key"),
                "kind": claim_item.get("kind"),
                "artifact_path": artifact_path,
                "claim_sha256": claim_item.get("claim_sha256"),
                "source_bindings": source_bindings,
                "content": content,
            })
            loaded_unit_ids.append(unit_id)
            taken += 1

    stamps = _lookup_stamp_constants()
    return {
        "schema_version": PERSONAL_LIBRARY_SCHEMA_VERSION,
        "command": "personal-lookup",
        "question": question,
        "question_sha256": _sha256_ref(question.encode("utf-8")),
        "library": str(path),
        "max_skills": max_skills,
        "max_units": max_units,
        "selected_skill_count": len(selected_skills),
        "selected_skills": selected_skills,
        "reasoning_context": reasoning_context,
        "loaded_unit_count": len(reasoning_context),
        "loaded_unit_ids": loaded_unit_ids,
        "entry_errors": entry_errors,
        "tool_grants": [],
        "tool_policy_rule": PERSONAL_TOOL_POLICY_RULE,
        "personal_library_mode": True,
        **stamps,
    }