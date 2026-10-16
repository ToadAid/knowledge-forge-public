"""Small, shared primitives for bounded reads of untrusted local files.

These helpers do not create authority.  They only make host-side inspection
of agent-staged bytes fail closed on symlinks, non-regular files, excessive
growth, tree fan-out, and mutation during a read.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat


class SecureIOError(ValueError):
    """An untrusted filesystem input could not be read safely."""


def prepare_private_regular_file(path_value: str | Path, label: str) -> Path:
    """Create or validate a host-private 0600 regular file.

    The direct parent is part of the trust boundary. On POSIX it must be a
    host-owned 0700 directory, preventing an untrusted peer from replacing
    the pathname between validation and a library reopening it.
    """

    path = reject_symlink_components(path_value, label)
    parent = path.parent
    try:
        parent_info = os.lstat(parent)
    except OSError as exc:
        raise SecureIOError(f"{label} parent is unavailable: {parent}") from exc
    if not stat.S_ISDIR(parent_info.st_mode):
        raise SecureIOError(f"{label} parent must be a real directory: {parent}")
    if os.name == "posix" and (
        parent_info.st_uid != os.geteuid()
        or stat.S_IMODE(parent_info.st_mode) != 0o700
    ):
        raise SecureIOError(f"{label} parent owner/mode mismatch (expected 0700)")

    flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
    except FileExistsError:
        try:
            info = os.lstat(path)
        except OSError as exc:
            raise SecureIOError(f"{label} is unreadable: {path}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise SecureIOError(f"{label} must be a regular file: {path}")
        if os.name == "posix" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise SecureIOError(f"{label} owner/mode mismatch (expected 0600)")
        return path
    except OSError as exc:
        raise SecureIOError(f"{label} is unavailable: {path}") from exc

    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise SecureIOError(f"{label} must be a regular file: {path}")
        if os.name == "posix" and (
            info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise SecureIOError(f"{label} owner/mode mismatch (expected 0600)")
    except Exception:
        if created:
            try:
                os.unlink(path)
            except OSError:
                pass
        raise
    finally:
        os.close(descriptor)
    return path


def reject_symlink_components(path_value: str | Path, label: str) -> Path:
    path = Path(path_value).absolute()
    current = Path(path.anchor)
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current = current / part
        if not os.path.lexists(current):
            continue
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise SecureIOError(f"{label} path is unreadable: {current}") from exc
        if stat.S_ISLNK(info.st_mode):
            raise SecureIOError(f"{label} may not traverse a symlink: {current}")
    return path


def read_bounded_bytes(path_value: str | Path, limit: int, label: str) -> bytes:
    """Read at most ``limit`` bytes through one regular-file descriptor."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise SecureIOError(f"{label} has an invalid byte limit")
    path = reject_symlink_components(path_value, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SecureIOError(f"{label} is unreadable: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise SecureIOError(f"{label} must be a regular file: {path}")
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > limit:
            raise SecureIOError(f"{label} exceeds {limit} bytes: {path}")
        after = os.fstat(descriptor)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        if any(
            getattr(before, name, None) != getattr(after, name, None)
            for name in stable_fields
        ):
            raise SecureIOError(f"{label} changed while being read: {path}")
        if after.st_size != len(payload):
            raise SecureIOError(f"{label} size changed while being read: {path}")
        return payload
    finally:
        os.close(descriptor)


def snapshot_regular_tree(
    root_value: str | Path,
    *,
    maximum_files: int,
    maximum_bytes: int,
    maximum_entries: int,
    label: str,
) -> dict[str, bytes]:
    """Snapshot a bounded, symlink-free regular-file tree."""

    root = reject_symlink_components(root_value, label)
    try:
        root_info = os.lstat(root)
    except OSError as exc:
        raise SecureIOError(f"{label} is unavailable: {root}") from exc
    if not stat.S_ISDIR(root_info.st_mode):
        raise SecureIOError(f"{label} must be a real directory: {root}")

    files: dict[str, bytes] = {}
    total = 0
    entry_count = 0
    pending: list[Path] = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda item: item.name)
        except OSError as exc:
            raise SecureIOError(f"{label} directory is unreadable: {directory}") from exc
        entry_count += len(entries)
        if entry_count > maximum_entries:
            raise SecureIOError(f"{label} exceeds entry limit")
        for entry in entries:
            relative = Path(entry.path).relative_to(root).as_posix()
            if entry.is_symlink():
                raise SecureIOError(f"{label} contains a symlink: {relative}")
            if entry.is_dir(follow_symlinks=False):
                pending.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                raise SecureIOError(f"{label} contains a non-regular entry: {relative}")
            if len(files) >= maximum_files:
                raise SecureIOError(f"{label} exceeds file limit")
            payload = read_bounded_bytes(
                Path(entry.path), maximum_bytes - total, f"{label} artifact"
            )
            total += len(payload)
            files[relative] = payload
    if not files:
        raise SecureIOError(f"{label} contains no regular files")
    return dict(sorted(files.items()))
