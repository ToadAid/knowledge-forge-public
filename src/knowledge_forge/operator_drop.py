"""Bounded user-owned operator drop for governed agent handoff.

The operator drop is deliberately outside an agent sandbox. This module never
writes, deletes, renames, or moves source entries. It may only discover regular
files without following symlinks and copy one selected file into a destination
root that the host already approved in an :class:`OperatorDropPolicy`.

Authorization is external to Knowledge Forge: callers must construct policy
from host-owned configuration. Agent/model input may select a relative source
and destination path, but it must never be allowed to choose or widen the
approved root set.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path, PureWindowsPath
import stat
from typing import Any

OPERATOR_DROP_SCHEMA_VERSION = "0.1"
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ENTRIES = 512
SOURCE_ACCESS = "READ_ONLY"
COPY_AUTHORITY = "HOST_APPROVED_ROOTS_ONLY"


class OperatorDropError(ValueError):
    """Raised when an operator-drop operation cannot remain within policy."""


@dataclass(frozen=True)
class OperatorDropPolicy:
    """Host-owned roots and bounds for one agent/consumer."""

    drop_root: Path
    allowed_copy_roots: tuple[Path, ...]
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_entries: int = DEFAULT_MAX_ENTRIES


@dataclass(frozen=True)
class _ResolvedPolicy:
    drop_root: Path
    allowed_copy_roots: tuple[Path, ...]
    max_file_bytes: int
    max_entries: int


def default_operator_knowledge_drop_root(home: Path | None = None) -> Path:
    """Return the shared convention; this function never creates the directory."""

    base = Path.home() if home is None else Path(home)
    return base / ".toadaid" / "drop" / "knowledge"


def _positive_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OperatorDropError(f"{label} must be a positive integer")
    return value


def _resolve_root(value: Path, label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink():
        raise OperatorDropError(f"{label} may not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise OperatorDropError(f"{label} is unavailable: {path}") from exc
    if not resolved.is_dir():
        raise OperatorDropError(f"{label} must be an existing directory")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_policy(policy: OperatorDropPolicy) -> _ResolvedPolicy:
    if not isinstance(policy, OperatorDropPolicy):
        raise OperatorDropError("policy must be an OperatorDropPolicy")

    drop_root = _resolve_root(policy.drop_root, "drop_root")
    if not isinstance(policy.allowed_copy_roots, tuple) or not policy.allowed_copy_roots:
        raise OperatorDropError("allowed_copy_roots must be a non-empty tuple")

    copy_roots: list[Path] = []
    for index, value in enumerate(policy.allowed_copy_roots):
        root = _resolve_root(Path(value), f"allowed_copy_roots[{index}]")
        if root in copy_roots:
            raise OperatorDropError("allowed_copy_roots must be unique")
        if _is_within(root, drop_root) or _is_within(drop_root, root):
            raise OperatorDropError(
                "operator drop and approved destination roots may not overlap"
            )
        copy_roots.append(root)

    return _ResolvedPolicy(
        drop_root=drop_root,
        allowed_copy_roots=tuple(copy_roots),
        max_file_bytes=_positive_int(policy.max_file_bytes, "max_file_bytes"),
        max_entries=_positive_int(policy.max_entries, "max_entries"),
    )


def validate_operator_drop_policy(policy: OperatorDropPolicy) -> dict[str, Any]:
    """Validate host-owned policy and return a non-authorizing summary."""

    resolved = _resolve_policy(policy)
    return {
        "valid": True,
        "schema_version": OPERATOR_DROP_SCHEMA_VERSION,
        "drop_root": str(resolved.drop_root),
        "allowed_copy_roots": [str(path) for path in resolved.allowed_copy_roots],
        "max_file_bytes": resolved.max_file_bytes,
        "max_entries": resolved.max_entries,
        "source_access": SOURCE_ACCESS,
        "copy_authority": COPY_AUTHORITY,
    }


def _relative_path(value: str, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise OperatorDropError(f"{label} must be a non-empty relative path")
    raw = value.strip()
    if "\x00" in raw:
        raise OperatorDropError(f"{label} contains a NUL byte")

    path = Path(raw)
    windows = PureWindowsPath(raw)
    if path.is_absolute() or windows.is_absolute() or windows.drive:
        raise OperatorDropError(f"{label} must be relative")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise OperatorDropError(f"{label} may not contain dot traversal")
    if "\\" in raw and os.sep != "\\":
        raise OperatorDropError(f"{label} may not use alternate path separators")
    return path


def _entry_budget_increment(count: int, maximum: int) -> int:
    updated = count + 1
    if updated > maximum:
        raise OperatorDropError(
            f"operator drop exceeds configured entry limit: {maximum}"
        )
    return updated


def discover_operator_drop(policy: OperatorDropPolicy) -> dict[str, Any]:
    """List bounded regular files without following or reading symlink targets."""

    resolved = _resolve_policy(policy)
    entries: list[dict[str, Any]] = []
    skipped_symlinks: list[str] = []
    skipped_special: list[str] = []
    observed = 0

    def walk(directory: Path, prefix: Path) -> None:
        nonlocal observed
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise OperatorDropError(
                f"operator drop directory is unreadable: {directory}"
            ) from exc

        for child in children:
            relative = prefix / child.name
            observed = _entry_budget_increment(observed, resolved.max_entries)
            try:
                if child.is_symlink():
                    skipped_symlinks.append(relative.as_posix())
                    continue
                if child.is_dir(follow_symlinks=False):
                    walk(Path(child.path), relative)
                    continue
                if child.is_file(follow_symlinks=False):
                    info = child.stat(follow_symlinks=False)
                    eligible = info.st_size <= resolved.max_file_bytes
                    entries.append(
                        {
                            "relative_path": relative.as_posix(),
                            "size_bytes": info.st_size,
                            "eligible": eligible,
                            "refusal_reason": None
                            if eligible
                            else "FILE_TOO_LARGE",
                        }
                    )
                    continue
                skipped_special.append(relative.as_posix())
            except OSError as exc:
                raise OperatorDropError(
                    f"operator drop entry became unreadable: {relative.as_posix()}"
                ) from exc

    walk(resolved.drop_root, Path())
    entries.sort(key=lambda item: item["relative_path"])
    skipped_symlinks.sort()
    skipped_special.sort()

    return {
        "schema_version": OPERATOR_DROP_SCHEMA_VERSION,
        "drop_root": str(resolved.drop_root),
        "source_access": SOURCE_ACCESS,
        "copy_authority": COPY_AUTHORITY,
        "entry_count": len(entries),
        "entries": entries,
        "skipped_symlinks": skipped_symlinks,
        "skipped_special": skipped_special,
    }


def _safe_source_file(root: Path, relative: Path) -> Path:
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise OperatorDropError(
                f"source path is unavailable: {relative.as_posix()}"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise OperatorDropError("source path may not traverse a symlink")
        final = index == len(relative.parts) - 1
        if final:
            if not stat.S_ISREG(info.st_mode):
                raise OperatorDropError("source must be a regular file")
        elif not stat.S_ISDIR(info.st_mode):
            raise OperatorDropError("source parent must be a directory")

    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise OperatorDropError("source path cannot be resolved") from exc
    if not _is_within(resolved, root):
        raise OperatorDropError("source escaped operator drop root")
    return resolved


def _approved_destination_root(value: Path, policy: _ResolvedPolicy) -> Path:
    requested = _resolve_root(Path(value), "destination_root")
    if requested not in policy.allowed_copy_roots:
        raise OperatorDropError("destination_root is not host-approved")
    return requested


def _ensure_safe_destination_parent(root: Path, relative_parent: Path) -> Path:
    current = root
    for part in relative_parent.parts:
        current = current / part
        if os.path.lexists(current):
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode):
                raise OperatorDropError("destination path may not traverse a symlink")
            if not stat.S_ISDIR(info.st_mode):
                raise OperatorDropError("destination parent must be a directory")
        else:
            try:
                os.mkdir(current, 0o700)
            except FileExistsError:
                pass
            except OSError as exc:
                raise OperatorDropError(
                    f"cannot create destination directory: {current}"
                ) from exc
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise OperatorDropError("destination directory creation was unsafe")

    try:
        resolved = current.resolve(strict=True)
    except OSError as exc:
        raise OperatorDropError("destination parent cannot be resolved") from exc
    if not _is_within(resolved, root):
        raise OperatorDropError("destination escaped approved root")
    return resolved


def copy_from_operator_drop(
    policy: OperatorDropPolicy,
    source_relative_path: str,
    *,
    destination_root: Path,
    destination_relative_path: str | None = None,
) -> dict[str, Any]:
    """Copy one source file into one exact host-approved destination root."""

    resolved = _resolve_policy(policy)
    source_relative = _relative_path(source_relative_path, "source_relative_path")
    destination_relative = _relative_path(
        destination_relative_path or source_relative.as_posix(),
        "destination_relative_path",
    )

    source = _safe_source_file(resolved.drop_root, source_relative)
    source_info = os.stat(source, follow_symlinks=False)
    if source_info.st_size > resolved.max_file_bytes:
        raise OperatorDropError(
            f"source exceeds configured file limit: {resolved.max_file_bytes}"
        )

    destination_base = _approved_destination_root(Path(destination_root), resolved)
    destination_parent = _ensure_safe_destination_parent(
        destination_base,
        destination_relative.parent,
    )
    destination = destination_parent / destination_relative.name
    if os.path.lexists(destination):
        raise OperatorDropError("destination already exists; overwrite is forbidden")

    source_flags = os.O_RDONLY
    destination_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
        destination_flags |= os.O_NOFOLLOW

    created = False
    digest = sha256()
    copied = 0
    source_fd: int | None = None
    try:
        source_fd = os.open(source, source_flags)
        with os.fdopen(source_fd, "rb", closefd=True) as source_handle:
            source_fd = None
            before = os.fstat(source_handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise OperatorDropError("opened source is not a regular file")

            destination_fd = os.open(destination, destination_flags, 0o600)
            created = True
            with os.fdopen(destination_fd, "wb", closefd=True) as destination_handle:
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > resolved.max_file_bytes:
                        raise OperatorDropError(
                            "source grew beyond configured file limit during copy"
                        )
                    digest.update(chunk)
                    destination_handle.write(chunk)
                destination_handle.flush()
                os.fsync(destination_handle.fileno())

            after = os.fstat(source_handle.fileno())
            if not all(
                (
                    before.st_dev == after.st_dev,
                    before.st_ino == after.st_ino,
                    before.st_size == after.st_size,
                    before.st_mtime_ns == after.st_mtime_ns,
                    copied == before.st_size,
                )
            ):
                raise OperatorDropError("source changed while being copied")
    except OperatorDropError:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise
    except OSError as exc:
        if created:
            try:
                destination.unlink()
            except OSError:
                pass
        raise OperatorDropError("operator drop copy failed") from exc
    finally:
        if source_fd is not None:
            try:
                os.close(source_fd)
            except OSError:
                pass

    return {
        "schema_version": OPERATOR_DROP_SCHEMA_VERSION,
        "operation": "COPY_FROM_OPERATOR_DROP",
        "source_access": SOURCE_ACCESS,
        "copy_authority": COPY_AUTHORITY,
        "source_relative_path": source_relative.as_posix(),
        "source_size_bytes": copied,
        "source_sha256": f"sha256:{digest.hexdigest()}",
        "destination_root": str(destination_base),
        "destination_relative_path": destination_relative.as_posix(),
        "destination_path": str(destination),
        "overwrite": False,
        "source_mutated": False,
    }
