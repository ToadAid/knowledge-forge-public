"""P3B-CP1: native Windows ordinary installer core (Python standard library only).

Invoked by ``bin\\knowledge-forge-install-agent.cmd`` as an isolated script
(``python -I -B <this file>``). It installs exactly two ordinary command
entrypoints into a user-controlled prefix:

    <prefix>\\bin\\knowledge-forge.exe            -> python -m knowledge_forge
    <prefix>\\bin\\knowledge-forge-authoring.exe  -> python -m knowledge_forge.authoring_cli

plus a host-owned, zero-authority ``agent-integration.json``. It never installs,
exposes, imports or runs ``knowledge-forge-operator`` / ``operator_cli``.

Why ``.exe`` entrypoints rather than ``.cmd`` wrappers
------------------------------------------------------
Agent-facing commands receive model-controlled arguments. A ``.cmd`` wrapper
must re-expand ``%*``, and cmd.exe then re-parses ``&``, ``|``, ``<``, ``>``
and ``%`` outside quotes (the "BatBadBut" class): an argument such as
``a&b`` would execute ``b``. The entrypoints are therefore built from the
console launcher stub shipped with the selected interpreter's own ``pip``
(``pip/_vendor/distlib/t64.exe``, the same mechanism pip uses for every
Windows console script). The stub reads a shebang that pins the exact
interpreter and passes the original command line straight to Python; no
shell ever parses it. Nothing is downloaded and no binary is added to this
repository. If the stub is unavailable the installer refuses; it never falls
back to a shell wrapper.

Security doctrine (unchanged): INSTALLED != AUTHORIZED; PLATFORM DETECTED !=
TRUSTED HOST; AGENT REQUEST != HUMAN AUTHORIZATION. Installation is not an
authority event and does not integrate Knowledge Forge with any host agent.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import ntpath
import os
import platform
import re
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from typing import Optional, Sequence

PROG = "knowledge-forge-install-agent"
SCHEMA_VERSION = "0.1"
PROMOTION_AUTHORITY = "EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY"
MIN_PYTHON = (3, 10)

# (installed stem, module run from the reviewed checkout). Nothing else.
ENTRYPOINTS = (
    ("knowledge-forge", "knowledge_forge"),
    ("knowledge-forge-authoring", "knowledge_forge.authoring_cli"),
)
OPERATOR_STEM = "knowledge-forge-operator"
# Any of these beside an installed stem would shadow or confuse the command.
SHADOW_SUFFIXES = ("", ".exe", ".com", ".bat", ".cmd", ".ps1", ".py", ".pyw", ".lnk", ".vbs", ".js")
ENTRYPOINT_KIND = "DISTLIB_CONSOLE_LAUNCHER_EXE"
MAX_DIR_CHARS = 200  # leaves room for \bin\knowledge-forge-authoring.exe under MAX_PATH

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9\u00b9\u00b2\u00b3]|lpt[0-9\u00b9\u00b2\u00b3]|conin\$|conout\$)(\..*)?$", re.I)
_INVALID_CHARS = set('<>"|?*')
_DOCTOR_BOOT = (
    "import runpy, sys\n"
    "src, mod = sys.argv[1], sys.argv[2]\n"
    "sys.path.insert(0, src)\n"
    "sys.argv = [mod] + sys.argv[3:]\n"
    "runpy.run_module(mod, run_name='__main__')\n"
)


class Refusal(Exception):
    """Fail-closed installer refusal (exit 2). Never an authorization decision."""


# --------------------------------------------------------------------------
# Lexical Windows path policy (pure; uses ntpath so it is testable anywhere)
# --------------------------------------------------------------------------
def validate_windows_path(raw: object, label: str) -> str:
    """Return a canonical absolute local-drive path or raise Refusal.

    Supported: ``X:\\component\\...`` only. Refused: relative and
    drive-relative paths, UNC (``\\\\server\\share``), device / extended-length
    (``\\\\.\\``, ``\\\\?\\``, ``\\??\\``), ``.``/``..`` traversal, empty
    components, alternate data streams, reserved device names, trailing
    dots/spaces (Win32 alias stripping), control and invalid characters.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise Refusal(f"{label} must be a non-empty path")
    if any(ord(c) < 32 for c in raw):
        raise Refusal(f"{label} contains control characters")
    value = raw.replace("/", "\\")
    if value.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        raise Refusal(f"{label} device or extended-length paths are not supported: {raw}")
    if value.startswith("\\\\"):
        raise Refusal(f"{label} UNC/network paths are not supported: {raw}")
    if not re.match(r"^[A-Za-z]:\\", value):
        raise Refusal(f"{label} must be an absolute local drive path like C:\\...: {raw}")
    drive, rest = value[:2], value[3:]
    parts = rest.split("\\")
    if parts and parts[-1] == "":
        parts = parts[:-1]  # one trailing separator is fine
    clean = []
    for part in parts:
        if part == "":
            raise Refusal(f"{label} contains an empty path component: {raw}")
        if part in (".", ".."):
            raise Refusal(f"{label} must not contain '.' or '..' components: {raw}")
        if ":" in part:
            raise Refusal(f"{label} must not name an alternate data stream: {raw}")
        if any(c in _INVALID_CHARS for c in part):
            raise Refusal(f"{label} contains characters Windows does not allow: {raw}")
        if part[-1] in (" ", "."):
            raise Refusal(f"{label} component ends with a dot or space (Windows alias): {raw}")
        if _RESERVED.match(part):
            raise Refusal(f"{label} uses a reserved Windows device name: {raw}")
        clean.append(part)
    canonical = drive.upper() + "\\" + "\\".join(clean)
    if len(canonical) > MAX_DIR_CHARS:
        raise Refusal(f"{label} is longer than {MAX_DIR_CHARS} characters: {raw}")
    return canonical


def explicit_paths(prefix: Optional[str], state_root: Optional[str], manifest: Optional[str]) -> dict:
    """Validate EXPLICIT path options; an omitted option (None) stays None.

    P3B-CP1 R1: an explicitly supplied value -- including "" or whitespace --
    is never treated as "not given" and never falls back to a default
    location; it must pass validate_windows_path or the run is refused.
    """
    given = {"--prefix": prefix, "--state-root": state_root, "--manifest": manifest}
    return {flag: (None if value is None else validate_windows_path(value, flag)) for flag, value in given.items()}


def is_within(child: str, parent: str) -> bool:
    c, p = ntpath.normcase(child), ntpath.normcase(parent).rstrip("\\")
    return c == p or c.startswith(p + "\\")


# --------------------------------------------------------------------------
# Real Windows host checks (ctypes; only on native Windows)
# --------------------------------------------------------------------------
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_ALLOWED_WRITERS_FIXED = {
    "S-1-5-18",      # LocalSystem
    "S-1-5-32-544",  # BUILTIN\Administrators
    "S-1-3-0",       # CREATOR OWNER (resolves to the object's owner)
}
_TRUSTED_OWNERS = frozenset({"S-1-5-18", "S-1-5-32-544"})  # LocalSystem, Administrators
_APPCONTAINER_PREFIXES = ("S-1-15-2-", "S-1-15-3-")  # AppContainer package / capability SIDs
_WRITE_LIKE = (
    0x0002 | 0x0004 | 0x0040        # write data/add file, append/add subdir, delete child
    | 0x00010000 | 0x00040000 | 0x00080000   # DELETE, WRITE_DAC, WRITE_OWNER
    | 0x10000000 | 0x40000000       # GENERIC_ALL, GENERIC_WRITE
)


@dataclass(frozen=True)
class HostIdentity:
    user_sid: str
    owner_sids: frozenset  # SIDs that legitimately own objects this process creates
    elevated: bool


class _Win:
    """Minimal advapi32/kernel32 bindings for owner, DACL and drive checks."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.wt = wintypes
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PSID = ctypes.c_void_p
        self.adv.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
            ctypes.POINTER(PSID), ctypes.POINTER(PSID), ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ]
        self.adv.GetNamedSecurityInfoW.restype = wintypes.DWORD
        self.adv.ConvertSidToStringSidW.argtypes = [PSID, ctypes.POINTER(wintypes.LPWSTR)]
        self.adv.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.adv.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
        self.adv.GetAce.restype = wintypes.BOOL
        self.adv.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
        self.adv.GetAclInformation.restype = wintypes.BOOL
        self.adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.adv.OpenProcessToken.restype = wintypes.BOOL
        self.adv.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.adv.GetTokenInformation.restype = wintypes.BOOL
        self.k32.GetCurrentProcess.restype = wintypes.HANDLE
        self.k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.k32.LocalFree.argtypes = [ctypes.c_void_p]
        self.k32.LocalFree.restype = ctypes.c_void_p
        self.k32.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
        self.k32.GetDriveTypeW.restype = wintypes.UINT

    def _sid_str(self, psid) -> str:
        out = self.wt.LPWSTR()
        if not self.adv.ConvertSidToStringSidW(psid, self.ctypes.byref(out)):
            raise Refusal("cannot convert a security identifier")
        try:
            return out.value
        finally:
            self.k32.LocalFree(out)

    def _token_info(self, cls: int) -> bytes:
        ctypes = self.ctypes
        token = self.wt.HANDLE()
        if not self.adv.OpenProcessToken(self.k32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            raise Refusal("cannot open the process token")
        try:
            need = self.wt.DWORD(0)
            self.adv.GetTokenInformation(token, cls, None, 0, ctypes.byref(need))
            size = need.value or 4  # some classes (TokenElevation) demand the exact size
            buf = ctypes.create_string_buffer(size)
            if not self.adv.GetTokenInformation(token, cls, buf, size, ctypes.byref(need)):
                raise Refusal("cannot read the process token")
            return buf
        finally:
            self.k32.CloseHandle(token)

    def identity(self) -> HostIdentity:
        ctypes = self.ctypes
        user_buf = self._token_info(1)   # TokenUser: SID_AND_ATTRIBUTES
        owner_buf = self._token_info(4)  # TokenOwner: default owner SID
        elev_buf = self._token_info(20)  # TokenElevation: DWORD
        user = self._sid_str(ctypes.c_void_p.from_buffer(user_buf).value)
        owner = self._sid_str(ctypes.c_void_p.from_buffer(owner_buf).value)
        elevated = bool(self.wt.DWORD.from_buffer(elev_buf).value)
        return HostIdentity(user, frozenset({user, owner}), elevated)

    def drive_type(self, path: str) -> int:
        return int(self.k32.GetDriveTypeW(path[:3]))

    def security(self, path: str):
        """Return (owner_sid, [(ace_type, flags, mask, sid)...] or None for NULL DACL)."""
        ctypes = self.ctypes
        owner = ctypes.c_void_p()
        dacl = ctypes.c_void_p()
        psd = ctypes.c_void_p()
        rc = self.adv.GetNamedSecurityInfoW(
            path, 1, 0x1 | 0x4, ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(psd)
        )
        if rc != 0:
            raise Refusal(f"cannot read the security descriptor of {path} (error {rc})")
        try:
            owner_sid = self._sid_str(owner)
            if not dacl.value:
                return owner_sid, None
            info = (self.wt.DWORD * 3)()
            if not self.adv.GetAclInformation(dacl, info, ctypes.sizeof(info), 2):
                raise Refusal(f"cannot read the access list of {path}")
            aces = []
            for i in range(info[0]):
                pace = ctypes.c_void_p()
                if not self.adv.GetAce(dacl, i, ctypes.byref(pace)):
                    raise Refusal(f"cannot read an access entry of {path}")
                ace_type = ctypes.c_ubyte.from_address(pace.value).value
                flags = ctypes.c_ubyte.from_address(pace.value + 1).value
                mask = ctypes.c_uint32.from_address(pace.value + 4).value
                sid = self._sid_str(ctypes.c_void_p(pace.value + 8)) if ace_type in (0, 1) else None
                aces.append((ace_type, flags, mask, sid))
            return owner_sid, aces
        finally:
            self.k32.LocalFree(psd)


class HostChecks:
    """Native-Windows filesystem truth: reparse points, aliases, owner, DACL."""

    def __init__(self, win: Optional[_Win] = None) -> None:
        self.win = win or _Win()
        self.ident = self.win.identity()
        # AppContainer package/capability SIDs seen with write rights (reported, not refused).
        self.appcontainer_writers: set = set()

    def lexical_matches_real(self, path: str) -> None:
        """Refuse 8.3 short-name aliases, SUBST/network drives and links:
        the OS-resolved real path of the existing portion must equal it."""
        existing = nearest_existing(path)
        real = os.path.realpath(existing)
        if real.startswith("\\\\?\\"):
            real = real[4:]
        if ntpath.normcase(real.rstrip("\\")) != ntpath.normcase(existing.rstrip("\\")):
            raise Refusal(
            "path does not resolve to itself (8.3 alias, link, SUBST, network drive, or packaged-app "
            f"file virtualization; run from a normal cmd.exe): {existing} -> {real}"
        )

    def no_reparse_chain(self, path: str) -> None:
        cur = path[:3]
        for part in path[3:].split("\\") if len(path) > 3 else []:
            cur = ntpath.join(cur, part)
            try:
                st = os.lstat(cur)
            except FileNotFoundError:
                return
            if getattr(st, "st_file_attributes", 0) & _REPARSE:
                raise Refusal(f"refusing reparse point (symlink/junction/mount) in path: {cur}")

    def local_drive(self, path: str) -> None:
        kind = self.win.drive_type(path)
        if kind not in (2, 3):  # DRIVE_REMOVABLE, DRIVE_FIXED
            raise Refusal(f"refusing non-local drive (type {kind}) for {path}")

    def owned_and_private(self, path: str, label: str, trusted_owner_ok: bool = False) -> None:
        owner, aces = self.win.security(path)
        owners = self.ident.owner_sids | (_TRUSTED_OWNERS if trusted_owner_ok else frozenset())
        if owner not in owners:
            raise Refusal(f"{label} is owned by {owner}, not by this user: {path}")
        if aces is None:
            raise Refusal(f"{label} has a NULL DACL (anyone may write): {path}")
        allowed = _ALLOWED_WRITERS_FIXED | self.ident.owner_sids
        for ace_type, _flags, mask, sid in aces:
            if ace_type == 1:  # ACCESS_DENIED only restricts
                continue
            if ace_type != 0:
                if mask & _WRITE_LIKE:
                    raise Refusal(f"{label} has an unsupported access entry type {ace_type}: {path}")
                continue
            if mask & _WRITE_LIKE and sid not in allowed:
                if sid and sid.startswith(_APPCONTAINER_PREFIXES):
                    # A lowbox sub-identity of THIS user: Windows grants access only
                    # if the user's own SID is also allowed, so it can never exceed
                    # this user's rights. It is not another account. Report it.
                    self.appcontainer_writers.add(sid)
                    continue
                raise Refusal(f"{label} grants write access to {sid}; refusing a shared/mutable location: {path}")

    def directory_policy(self, path: str, label: str) -> None:
        """Checks for a directory the installer will create or write into."""
        self.local_drive(path)
        self.no_reparse_chain(path)
        self.lexical_matches_real(path)
        existing = nearest_existing(path)
        if not os.path.isdir(existing):
            raise Refusal(f"{label} path collides with a non-directory: {existing}")
        # Windows commonly makes SYSTEM the owner of the user's OWN profile folder.
        # Only that exact directory may have a trusted (SYSTEM/Administrators)
        # owner; its access list must still be private. Everything else, and
        # everything the installer creates, must be owned by this user.
        profile = self.user_profile()
        is_profile = profile is not None and ntpath.normcase(existing.rstrip("\\")) == ntpath.normcase(profile)
        self.owned_and_private(existing, f"{label} (nearest existing directory)", trusted_owner_ok=is_profile)

    def user_profile(self) -> Optional[str]:
        raw = os.environ.get("USERPROFILE")
        if not raw:
            return None
        try:
            profile = validate_windows_path(raw, "%USERPROFILE%")
            self.lexical_matches_real(profile)
        except Refusal:
            return None
        return profile if os.path.isdir(profile) else None


def nearest_existing(path: str) -> str:
    cur = path
    while not os.path.lexists(cur):
        parent = ntpath.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return cur


# --------------------------------------------------------------------------
# Entrypoint and manifest generation (deterministic bytes)
# --------------------------------------------------------------------------
def entrypoint_main_source(stem: str, module: str, checkout_src: str) -> bytes:
    if (stem, module) not in ENTRYPOINTS:
        raise Refusal("refusing to generate an entrypoint outside the ordinary allowlist")
    return (
        "# Generated by knowledge-forge-install-agent (native Windows). DO NOT EDIT.\n"
        "# Ordinary Knowledge Forge entrypoint: runs one allowlisted module from\n"
        "# the exact reviewed checkout. It has no operator surface and no authority.\n"
        "import runpy\n"
        "import sys\n"
        f"CHECKOUT_SRC = {ascii(checkout_src)}\n"
        f"MODULE = {ascii(module)}\n"
        "sys.path.insert(0, CHECKOUT_SRC)\n"
        f"sys.argv[0] = {ascii(stem)}\n"
        "runpy.run_module(MODULE, run_name='__main__', alter_sys=False)\n"
    ).encode("ascii")


def build_entrypoint(launcher: bytes, python_exe: str, stem: str, module: str, checkout_src: str) -> bytes:
    if not launcher.startswith(b"MZ"):
        raise Refusal("console launcher stub is not a Windows executable")
    if '"' in python_exe or any(ord(c) < 32 for c in python_exe):
        raise Refusal("interpreter path is not representable in a launcher shebang")
    shebang = b'#!"' + python_exe.encode("utf-8") + b'" -I\r\n'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        info = zipfile.ZipInfo("__main__.py", date_time=(1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        info.create_system = 0
        zf.writestr(info, entrypoint_main_source(stem, module, checkout_src))
    return launcher + shebang + buf.getvalue()


def manifest_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def find_launcher() -> tuple:
    """Locate pip's console launcher stub for this OS architecture, without
    importing pip. Returns (bytes, relative source, pip version)."""
    import importlib.util
    from importlib import metadata

    spec = importlib.util.find_spec("pip")
    if spec is None or not spec.submodule_search_locations:
        raise Refusal("the selected Python has no pip; its console launcher stub is required (no shell fallback)")
    machine = platform.machine().upper()
    name = {"AMD64": "t64.exe", "X86_64": "t64.exe", "ARM64": "t64-arm.exe", "X86": "t32.exe"}.get(machine)
    if name is None:
        raise Refusal(f"unsupported Windows architecture for the launcher stub: {machine}")
    path = os.path.join(list(spec.submodule_search_locations)[0], "_vendor", "distlib", name)
    try:
        st = os.lstat(path)
        if getattr(st, "st_file_attributes", 0) & _REPARSE or not stat.S_ISREG(st.st_mode):
            raise Refusal(f"launcher stub is not a regular file: {path}")
        with open(path, "rb") as fh:
            data = fh.read(4 * 1024 * 1024 + 1)
    except FileNotFoundError:
        raise Refusal(f"pip console launcher stub not found: {path}") from None
    if len(data) > 4 * 1024 * 1024 or not data.startswith(b"MZ"):
        raise Refusal(f"launcher stub is not a plausible Windows executable: {path}")
    try:
        pip_version = metadata.version("pip")
    except metadata.PackageNotFoundError:
        pip_version = "UNKNOWN"
    return data, f"pip/_vendor/distlib/{name}", pip_version


# --------------------------------------------------------------------------
# Create-once writes
# --------------------------------------------------------------------------
def classify_target(path: str, expected: bytes) -> str:
    """'ABSENT', 'IDENTICAL', or raise Refusal (never overwrites)."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return "ABSENT"
    if getattr(st, "st_file_attributes", 0) & _REPARSE or not stat.S_ISREG(st.st_mode):
        raise Refusal(f"refusing non-regular or reparse-point existing target: {path}")
    with open(path, "rb") as fh:
        current = fh.read(len(expected) + 1)
    if current != expected:
        raise Refusal(f"refusing to overwrite different existing file (original preserved): {path}")
    return "IDENTICAL"


def write_once(path: str, data: bytes) -> str:
    if classify_target(path, data) == "IDENTICAL":
        return "IDENTICAL"
    directory = ntpath.dirname(path)
    tmp = ntpath.join(directory, f".kf-install-{os.getpid()}-{os.urandom(6).hex()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.rename(tmp, path)  # Windows: fails if the target appeared meanwhile
    except FileExistsError:
        _silent_unlink(tmp)
        raise Refusal(f"target appeared during installation; refusing to overwrite: {path}") from None
    except BaseException:
        _silent_unlink(tmp)
        raise
    return "CREATED"


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def make_dirs(path: str, checks: HostChecks, label: str) -> None:
    """Create missing directories one level at a time, re-verifying each."""
    missing = []
    cur = path
    while not os.path.lexists(cur):
        missing.append(cur)
        cur = ntpath.dirname(cur)
    for d in reversed(missing):
        try:
            os.mkdir(d)
        except FileExistsError:
            pass
        except OSError as exc:
            raise Refusal(f"cannot create {label} directory {d}: {exc.strerror}") from None
        checks.no_reparse_chain(d)
        if not os.path.isdir(d):
            raise Refusal(f"{label} path is not a directory: {d}")
        # Re-verify the NEW directory resolves to itself. Packaged (MSIX) app
        # processes have AppData writes silently virtualized into the package's
        # private LocalCache; an install there is invisible to ordinary programs
        # and must never be reported READY.
        checks.lexical_matches_real(d)
        checks.owned_and_private(d, label)


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        allow_abbrev=False,
        description=(
            "Install the ordinary Knowledge Forge commands for the current Windows "
            "user from this checkout (native Windows, no administrator rights)."
        ),
        epilog=(
            "Defaults: --prefix %LOCALAPPDATA%\\ToadAid\\knowledge-forge (commands in <prefix>\\bin), "
            "--state-root %LOCALAPPDATA%\\ToadAid\\knowledge-forge\\state, "
            "--manifest <state-root>\\agent-integration.json. Installs only knowledge-forge.exe "
            "and knowledge-forge-authoring.exe; knowledge-forge-operator is deliberately NOT "
            "installed. PATH is never modified. Promotion remains an external authenticated "
            "human-host action."
        ),
    )
    parser.add_argument("--prefix", metavar="PATH", help="command prefix; entrypoints go in PATH\\bin")
    parser.add_argument("--state-root", metavar="PATH", help="host-owned integration state root")
    parser.add_argument("--manifest", metavar="PATH", help="integration manifest path")
    parser.add_argument("--check-only", action="store_true", help="verify prerequisites and paths without writing")
    return parser


def _localappdata_default(checks: HostChecks) -> str:
    raw = os.environ.get("LOCALAPPDATA")
    if not raw:
        raise Refusal("%LOCALAPPDATA% is not set; pass explicit --prefix and --state-root (no fallback is guessed)")
    base = validate_windows_path(raw, "%LOCALAPPDATA%")
    if not os.path.isdir(base):
        raise Refusal(f"%LOCALAPPDATA% is not an existing directory: {base}")
    checks.local_drive(base)
    checks.no_reparse_chain(base)
    checks.lexical_matches_real(base)
    checks.owned_and_private(base, "%LOCALAPPDATA%")
    return base


def _doctor(python_exe: str, checkout_src: str, module: str) -> int:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [python_exe, "-I", "-B", "-c", _DOCTOR_BOOT, checkout_src, module, "--help"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env, timeout=120, check=False,
    ).returncode


def run(argv: Sequence[str], checks: Optional[HostChecks] = None) -> dict:
    args = _parser().parse_args(list(argv))
    if os.name != "nt":
        raise Refusal("this installer is for native Windows; use bin/knowledge-forge-install-agent on POSIX")
    if sys.version_info < MIN_PYTHON:
        raise Refusal("Python 3.10 or newer is required")
    explicit = explicit_paths(args.prefix, args.state_root, args.manifest)  # before any host check or write
    checks = checks or HostChecks()

    here = os.path.abspath(__file__)
    checkout = validate_windows_path(ntpath.dirname(ntpath.dirname(ntpath.dirname(here))), "checkout")
    checkout_src = ntpath.join(checkout, "src")
    checks.local_drive(checkout)
    checks.no_reparse_chain(checkout)
    checks.lexical_matches_real(checkout)
    for rel in ("__init__.py", "__main__.py", "cli.py", "authoring_cli.py", "windows_installer.py"):
        f = ntpath.join(checkout_src, "knowledge_forge", rel)
        st = os.lstat(f) if os.path.lexists(f) else None
        if st is None or not stat.S_ISREG(st.st_mode) or getattr(st, "st_file_attributes", 0) & _REPARSE:
            raise Refusal(f"required checkout file must be a real file: {f}")

    # Diagnostic only: the entrypoints execute this checkout's code, so report
    # truthfully whether other local principals could modify it.
    try:
        checks.owned_and_private(checkout, "checkout")
        checkout_private, checkout_note = True, "OWNER_ONLY_WRITE"
    except Refusal as exc:
        checkout_private, checkout_note = False, str(exc)[:200]

    python_exe = os.path.abspath(sys.executable or "")
    if not sys.executable or not os.path.isfile(python_exe):
        raise Refusal("cannot determine the absolute path of the selected Python interpreter")
    validate_windows_path(ntpath.dirname(python_exe), "interpreter directory")
    python_version = platform.python_version()
    launcher, launcher_source, pip_version = find_launcher()

    default_root = None
    if explicit["--prefix"] is None or explicit["--state-root"] is None:
        default_root = ntpath.join(_localappdata_default(checks), "ToadAid", "knowledge-forge")
    prefix = explicit["--prefix"] if explicit["--prefix"] is not None else default_root
    state_root = explicit["--state-root"] if explicit["--state-root"] is not None else ntpath.join(default_root, "state")
    manifest = explicit["--manifest"] if explicit["--manifest"] is not None else ntpath.join(state_root, "agent-integration.json")
    bin_dir = ntpath.join(prefix, "bin")
    manifest_dir = ntpath.dirname(manifest)
    for other_label, other in (("--prefix", prefix), ("--state-root", state_root), ("--manifest directory", manifest_dir)):
        if is_within(other, checkout):
            raise Refusal(f"{other_label} must be outside the checkout: {other}")

    for label, d in (("command bin directory", bin_dir), ("state root", state_root), ("manifest directory", manifest_dir)):
        checks.directory_policy(d, label)

    # Collisions: operator surface, shadowing shims, foreign entrypoints/manifest.
    if os.path.isdir(bin_dir):
        for suffix in SHADOW_SUFFIXES:
            if os.path.lexists(ntpath.join(bin_dir, OPERATOR_STEM + suffix)):
                raise Refusal(f"refusing prefix that already exposes {OPERATOR_STEM}: {ntpath.join(bin_dir, OPERATOR_STEM + suffix)}")
        for stem, _ in ENTRYPOINTS:
            for suffix in SHADOW_SUFFIXES:
                if suffix == ".exe":
                    continue
                if os.path.lexists(ntpath.join(bin_dir, stem + suffix)):
                    raise Refusal(f"refusing conflicting shim that would shadow or confuse {stem}: {ntpath.join(bin_dir, stem + suffix)}")

    targets = {stem: ntpath.join(bin_dir, stem + ".exe") for stem, _ in ENTRYPOINTS}
    contents = {stem: build_entrypoint(launcher, python_exe, stem, module, checkout_src) for stem, module in ENTRYPOINTS}
    launcher_sha = hashlib.sha256(launcher).hexdigest()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "distribution": "CHECKOUT_LOCAL",
        "checkout_root": checkout,
        "prefix": prefix,
        "state_root": state_root,
        "manifest_path": manifest,
        "consumer_cli": targets["knowledge-forge"],
        "authoring_cli": targets["knowledge-forge-authoring"],
        "operator_cli_installed": False,
        "operator_surface": "HOST_OWNED_NOT_INSTALLED",
        "promotion_authority": PROMOTION_AUTHORITY,
        "trust_class": "UNTRUSTED_KNOWLEDGE_DATA",
        "authority": "KNOWLEDGE_ONLY",
        "instruction_authority": "NONE",
        "execution_authority": "NONE",
        "executable": False,
        "host_family": "windows",
        "entrypoint_kind": ENTRYPOINT_KIND,
        "python_executable": python_exe,
        "python_version": python_version,
        "launcher_source": launcher_source,
        "launcher_sha256": launcher_sha,
    }
    manifest_data = manifest_bytes(payload)
    for stem, path in targets.items():
        classify_target(path, contents[stem])
    classify_target(manifest, manifest_data)

    for stem, module in ENTRYPOINTS:
        if _doctor(python_exe, checkout_src, module) != 0:
            raise Refusal(f"checkout CLI doctor failed for {stem}")

    common = {
        "schema_version": SCHEMA_VERSION,
        "agent_setup_guide": ntpath.join(checkout, "docs", "AGENT_SETUP.md"),
        "agent_setup_helper": ntpath.join(checkout_src, "knowledge_forge", "agent_setup.py"),
        "protected_promotion_installed_by_this_command": False,
        "distribution": "CHECKOUT_LOCAL",
        "checkout_root": checkout,
        "prefix": prefix,
        "state_root": state_root,
        "manifest_path": manifest,
        "operator_cli_installed": False,
        "promotion_authority": PROMOTION_AUTHORITY,
        "host_family": "windows",
        "python_executable": python_exe,
        "python_version": python_version,
        "entrypoint_kind": ENTRYPOINT_KIND,
        "launcher_source": launcher_source,
        "launcher_pip_version": pip_version,
        "process_elevated": checks.ident.elevated,
        "same_user_appcontainer_write_grants": sorted(checks.appcontainer_writers),
        "checkout_location_private": checkout_private,
        "checkout_location_note": checkout_note,
        "windows_support": {
            "native_windows_ordinary_install": "THIS_INSTALLER",
            "path_policy": "LOCAL_DRIVE_NO_REPARSE_OWNER_PRIVATE_DACL",
            "path_modified": False,
            "operator_installed": False,
            "operator_security_verified": False,
            "agent_integrated": False,
        },
    }
    if args.check_only:
        return {**common, "status": "CHECK_OK", "consumer_cli_installed": False, "authoring_cli_installed": False}

    for label, d in (("command bin directory", bin_dir), ("state root", state_root), ("manifest directory", manifest_dir)):
        make_dirs(d, checks, label)
        checks.owned_and_private(d, label)
    results = {stem: write_once(targets[stem], contents[stem]) for stem, _ in ENTRYPOINTS}
    for path in targets.values():
        checks.lexical_matches_real(path)
    for stem, path in targets.items():
        rc = subprocess.run([path, "--help"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=120, check=False).returncode
        if rc != 0:
            raise Refusal(f"installed entrypoint doctor failed for {stem} (exit {rc}); manifest not written")
    manifest_result = write_once(manifest, manifest_data)
    checks.lexical_matches_real(manifest)
    return {
        **common,
        "status": "READY",
        "consumer_cli": targets["knowledge-forge"],
        "authoring_cli": targets["knowledge-forge-authoring"],
        "consumer_cli_installed": True,
        "authoring_cli_installed": True,
        "writes": {**results, "agent-integration.json": manifest_result},
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        result = run(sys.argv[1:] if argv is None else argv)
    except Refusal as exc:
        print(f"{PROG}: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print(f"{PROG}: a doctor check timed out", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
