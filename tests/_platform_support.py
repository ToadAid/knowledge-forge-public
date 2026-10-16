"""P3B-CP1: narrow, named platform skips for the stdlib regression suite.

Every helper here is deliberately specific. Linux behavior is unchanged: on
Linux none of these conditions skip anything except where a test was already
POSIX-only. Native Windows skips name the exact missing host capability so the
skip list in the CP1 report can be audited test by test. Nothing here grants,
fakes or weakens any security behavior; it only declines to run a test whose
fixture cannot be built on the current host.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

NATIVE_WINDOWS = sys.platform == "win32"


def _probe_symlinks() -> bool:
    """True when this process can actually create a symlink (real probe)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "target")
        with open(target, "wb"):
            pass
        try:
            os.symlink(target, os.path.join(tmp, "link"))
        except (OSError, NotImplementedError):
            return False
    return True


SYMLINKS_AVAILABLE = _probe_symlinks()

skip_unless_symlinks = unittest.skipUnless(
    SYMLINKS_AVAILABLE,
    "host cannot create symlinks (native Windows needs SeCreateSymbolicLinkPrivilege: "
    "Administrator or Developer Mode); symlink refusal stays covered on Linux CI",
)

skip_bash_launcher_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "executes a Bash launcher under bin/ that native Windows cannot run; the native "
    "Windows installer has its own tests in test_windows_agent_installer.py",
)

skip_posix_shebang_tools_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "fake OCR tools are '#!/usr/bin/env python3' scripts that native Windows cannot execute",
)


def _poppler_pdftotext_available() -> bool:
    """True only for Poppler's pdftotext (Git for Windows bundles Xpdf's, which
    the extractor correctly refuses as an unidentified extractor version)."""
    binary = shutil.which("pdftotext")
    if binary is None:
        return False
    try:
        probe = subprocess.run(
            [binary, "-v"], capture_output=True, text=True, timeout=20, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return "poppler" in (probe.stdout + probe.stderr).casefold()


skip_without_pdftotext_on_windows = unittest.skipIf(
    NATIVE_WINDOWS and not _poppler_pdftotext_available(),
    "Poppler pdftotext is not installed on this native Windows host (Linux CI installs it; "
    "a non-Poppler pdftotext such as Git for Windows' Xpdf build is correctly refused)",
)


def skip_linux_account_module_on_windows(module_name: str) -> None:
    """Module-level guard for tests of Linux-only pwd/grp review modules."""
    if NATIVE_WINDOWS:
        raise unittest.SkipTest(
            f"{module_name} is a Linux-only review module (imports pwd/grp); "
            "it is never imported by native Windows consumer/authoring entrypoints"
        )


# --- P3B-CP1 R2: upstream portable-native (P4/P6/P7) modules on native Windows ---
# These modules passively inspect *POSIX* host executables and Linux kernel
# namespaces. On native Windows they never resolve an executable or reach a
# namespace probe; they fail closed (UNRESOLVED/BLOCKED, every authority flag
# false). That fail-closed behaviour is asserted on Windows by
# tests/test_portable_native_windows_fail_closed.py. Each skip below names the
# exact Linux primitive the skipped test needs; none of them skips on Linux.

skip_posix_executable_fixture_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "builds an extensionless POSIX executable fixture (chmod 0o700) and expects shutil.which() "
    "plus POSIX mode bits (0o111 set, 0o002 clear) to resolve it; native Windows which() needs a "
    "PATHEXT extension and reports .exe files as mode 0o777, so the Linux-native module fails "
    "closed there (asserted in test_portable_native_windows_fail_closed.py)",
)

skip_without_os_pread = unittest.skipUnless(
    hasattr(os, "pread"),
    "patches or reads through os.pread, which native Windows Python does not provide",
)

skip_linux_proc_namespaces_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "reads Linux /proc/self/ns/* namespace inodes and stages a /tmp canary for the bubblewrap "
    "probe; native Windows has neither, and the module fails closed there (asserted in "
    "test_portable_native_windows_fail_closed.py)",
)

skip_posix_owner_fixture_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "the hook fixture root must be owned by os.getuid() (POSIX ownership); native Windows has no "
    "os.getuid, so stage_fixture refuses with a typed NativeHookFixtureError (asserted in "
    "test_portable_native_windows_fail_closed.py)",
)

skip_posix_shebang_host_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "the fake native host is a '#!/usr/bin/env python3' script that native Windows cannot execute; "
    "the credential/loader env allowlist is asserted on every OS by a subprocess.run mock in "
    "test_portable_native_windows_fail_closed.py",
)

POSIX_PATH_LABEL_SKIP_REASON = (
    "asserts POSIX ('/'-separated) $HOME path labels; the module builds labels with os.path, "
    "which is ntpath on native Windows, where the module never resolves an executable and so "
    "never emits a label"
)

skip_posix_path_labels_on_windows = unittest.skipIf(NATIVE_WINDOWS, POSIX_PATH_LABEL_SKIP_REASON)

skip_posix_file_modes_on_windows = unittest.skipIf(
    NATIVE_WINDOWS,
    "asserts POSIX file-mode bits (0700 directory / 0600 file) for the personal library state "
    "file, which native Windows cannot represent (chmod maps to a read-only attribute there)",
)
