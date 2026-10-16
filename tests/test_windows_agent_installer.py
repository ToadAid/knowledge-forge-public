"""P3B-CP1: native Windows ordinary installer proofs.

Pure policy tests run on every OS. End-to-end tests run only on real native
Windows (sys.platform == 'win32'), as a standard user, through the real
bin\\knowledge-forge-install-agent.cmd transport and the real installed .exe
entrypoints. They never install, import or run knowledge-forge-operator.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from knowledge_forge import windows_installer as wi  # noqa: E402  (stdlib-only module)

NATIVE_WINDOWS = sys.platform == "win32"
CMD = REPO / "bin" / "knowledge-forge-install-agent.cmd"
STEMS = ("knowledge-forge", "knowledge-forge-authoring")
LINUX_ONLY = ("knowledge_forge.agent_runtime_gate", "knowledge_forge.protected_host_gate",
              "knowledge_forge.provision_review", "knowledge_forge.operator_cli")


class WindowsPathPolicyTests(unittest.TestCase):
    """Lexical policy (pure ntpath logic; identical on every OS)."""

    def test_accepts_only_absolute_local_drive_paths(self):
        cases = {
            r"C:\Users\x": r"C:\Users\x",
            "c:/Users/x y/\u00fc": "C:\\Users\\x y\\\u00fc",
            "E:\\a&b (c)\\%x%!^": "E:\\a&b (c)\\%x%!^",
            "C:\\a\\": r"C:\a",
        }
        for raw, expected in cases.items():
            self.assertEqual(wi.validate_windows_path(raw, "t"), expected, raw)

    def test_refuses_unc_device_traversal_alias_and_reserved_forms(self):
        for raw in ["", "rel", r"\x", r"C:x", r"\\srv\share\x", r"\\?\C:\x", r"\\.\C:\x",
                    r"\??\C:\x", r"C:\a\..\b", r"C:\a\.\b", "C:\\a\\\\b", r"C:\a:b",
                    r"C:\con", r"C:\NUL.txt", r"C:\COM1", r"C:\a.", "C:\\a ", r"C:\a<b",
                    "C:\\a\x01", "C:\\" + "x" * 300]:
            with self.subTest(raw=raw), self.assertRaises(wi.Refusal):
                wi.validate_windows_path(raw, "t")

    def test_non_text_is_refused(self):
        with self.assertRaises(wi.Refusal):
            wi.validate_windows_path(None, "t")  # type: ignore[arg-type]


class ExplicitPathOptionTests(unittest.TestCase):
    """P3B-CP1 R1: omitted -> default; explicitly empty -> refuse (never a fallback)."""

    def test_omitted_options_stay_unset_for_defaults(self):
        self.assertEqual(wi.explicit_paths(None, None, None),
                         {"--prefix": None, "--state-root": None, "--manifest": None})

    def test_explicit_empty_or_blank_values_are_refused_for_every_path_option(self):
        for flag in ("--prefix", "--state-root", "--manifest"):
            for value in ("", " ", "\t"):
                kwargs = {"prefix": None, "state_root": None, "manifest": None}
                kwargs[{"--prefix": "prefix", "--state-root": "state_root", "--manifest": "manifest"}[flag]] = value
                with self.subTest(flag=flag, value=value), self.assertRaisesRegex(wi.Refusal, flag):
                    wi.explicit_paths(**kwargs)

    def test_explicit_values_are_validated_not_defaulted(self):
        out = wi.explicit_paths(r"C:\p", r"C:\s", r"C:\s\m.json")
        self.assertEqual(out, {"--prefix": r"C:\p", "--state-root": r"C:\s", "--manifest": r"C:\s\m.json"})


class EntrypointGenerationTests(unittest.TestCase):
    FAKE_LAUNCHER = b"MZ" + b"\0" * 64

    def test_only_two_ordinary_modules_can_be_generated(self):
        self.assertEqual(wi.ENTRYPOINTS, (("knowledge-forge", "knowledge_forge"),
                                          ("knowledge-forge-authoring", "knowledge_forge.authoring_cli")))
        for stem, module in [("knowledge-forge-operator", "knowledge_forge.operator_cli"),
                             ("knowledge-forge", "knowledge_forge.operator_cli"),
                             ("knowledge-forge-review", "knowledge_forge.approval_tui")]:
            with self.assertRaises(wi.Refusal):
                wi.entrypoint_main_source(stem, module, r"C:\x\src")

    def test_generated_source_is_ascii_inert_and_operator_free(self):
        src = wi.entrypoint_main_source("knowledge-forge", "knowledge_forge",
                                        "C:\\Users\\J\u00f6hn\\kf & (x) %y% !z!\\src").decode("ascii")
        self.assertNotIn("operator", src.split("authority.")[-1])
        self.assertIn("runpy.run_module(MODULE, run_name='__main__', alter_sys=False)", src)
        self.assertIn("'C:\\\\Users\\\\J\\xf6hn\\\\kf & (x) %y% !z!\\\\src'", src)
        compile(src, "<entry>", "exec")

    def test_entrypoint_bytes_are_deterministic_and_pin_the_interpreter(self):
        a = wi.build_entrypoint(self.FAKE_LAUNCHER, r"C:\Py 3\python.exe", "knowledge-forge", "knowledge_forge", r"C:\c\src")
        b = wi.build_entrypoint(self.FAKE_LAUNCHER, r"C:\Py 3\python.exe", "knowledge-forge", "knowledge_forge", r"C:\c\src")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith(self.FAKE_LAUNCHER + b'#!"C:\\Py 3\\python.exe" -I\r\n'))
        import io
        with zipfile.ZipFile(io.BytesIO(a[len(self.FAKE_LAUNCHER):].split(b"\r\n", 1)[1])) as zf:
            self.assertEqual(zf.namelist(), ["__main__.py"])

    def test_refuses_non_executable_launcher_and_quoted_interpreter(self):
        with self.assertRaises(wi.Refusal):
            wi.build_entrypoint(b"#!/bin/sh", r"C:\py\python.exe", "knowledge-forge", "knowledge_forge", r"C:\c\src")
        with self.assertRaises(wi.Refusal):
            wi.build_entrypoint(self.FAKE_LAUNCHER, 'C:\\py\\py"thon.exe', "knowledge-forge", "knowledge_forge", r"C:\c\src")

    def test_doctor_bootstrap_is_fixed_code_with_argv_data(self):
        self.assertNotIn("{", wi._DOCTOR_BOOT)
        self.assertIn("src, mod = sys.argv[1], sys.argv[2]", wi._DOCTOR_BOOT)


class CmdTransportContractTests(unittest.TestCase):
    def test_transport_is_thin_isolated_and_operator_free(self):
        raw = CMD.read_bytes()
        text = raw.decode("ascii")
        self.assertIn("setlocal EnableExtensions DisableDelayedExpansion", text)
        self.assertIn('-I -B "%KF_CORE%" %*', text)
        self.assertIn(r"%~dp0..\src\knowledge_forge\windows_installer.py", text)
        for forbidden in ("operator_cli", "knowledge-forge-operator", "powershell", "reg add",
                          "setx", "icacls", "runas", "schtasks", "curl", "bitsadmin"):
            self.assertNotIn(forbidden, text.lower().replace("knowledge-forge-operator is", ""))
        if NATIVE_WINDOWS or b"\r\n" in raw:
            self.assertNotIn(b"\n", raw.replace(b"\r\n", b""), "cmd transport must be CRLF")


class OrdinaryCliImportIsolationTests(unittest.TestCase):
    def test_consumer_and_authoring_never_import_linux_or_operator_modules(self):
        probe = (
            "import runpy, sys\n"
            "for mod in ('knowledge_forge', 'knowledge_forge.authoring_cli'):\n"
            "    sys.argv = [mod, '--help']\n"
            "    try:\n"
            "        runpy.run_module(mod, run_name='__main__')\n"
            "    except SystemExit:\n"
            "        pass\n"
            f"bad = [m for m in {LINUX_ONLY!r} if m in sys.modules]\n"
            "print('LOADED=' + ','.join(bad))\n"
        )
        env = dict(os.environ, PYTHONPATH=str(REPO / "src"), PYTHONDONTWRITEBYTECODE="1")
        out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("LOADED=\n", out.stdout)


def _tree(root: Path) -> dict:
    snap = {}
    if root.exists():
        for p in sorted(root.rglob("*")):
            snap[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else "DIR"
    return snap


@unittest.skipUnless(NATIVE_WINDOWS, "native Windows end-to-end installer proof")
class NativeWindowsInstallerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        checks = wi.HostChecks()
        candidates = [os.environ.get("KF_WINDOWS_TEST_ROOT"), tempfile.gettempdir(), os.environ.get("USERPROFILE")]
        cls.base = None
        reasons = []
        for cand in filter(None, candidates):
            try:
                real = os.path.realpath(cand)
                canon = wi.validate_windows_path(real, "test root")
                checks.directory_policy(canon + "\\kf-cp1-probe", "test root")
            except wi.Refusal as exc:
                reasons.append(f"{cand}: {exc}")
                continue
            cls.base = Path(canon) / f"kf-cp1-{uuid.uuid4().hex[:10]}"
            break
        if cls.base is None:
            message = "no private, non-virtualized test root on this host: " + " | ".join(reasons)
            if os.environ.get("KF_REQUIRE_WINDOWS_E2E") == "1":
                raise AssertionError(message)  # CI: a skipped native proof must fail, never pass
            raise unittest.SkipTest(message)
        cls.base.mkdir()
        cls.env = dict(os.environ, KNOWLEDGE_FORGE_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE="1")
        cls.env.pop("PYTHONPATH", None)

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "base", None) and cls.base.exists():
            shutil.rmtree(cls.base, ignore_errors=True)

    def _case(self) -> Path:
        d = self.base / uuid.uuid4().hex[:8]
        d.mkdir()
        return d

    def _install(self, *args: str, cmd: Path = CMD, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([str(cmd), *args], capture_output=True, text=True, encoding="utf-8",
                              env=env or self.env, timeout=300)

    def _ready(self, prefix: Path, state: Path, cmd: Path = CMD) -> dict:
        run = self._install("--prefix", str(prefix), "--state-root", str(state), cmd=cmd)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result["status"], "READY")
        return result

    def test_check_only_writes_nothing_and_reports_zero_authority(self):
        case = self._case()
        before, src_before = _tree(case), _tree(REPO / "src")
        run = self._install("--check-only", "--prefix", str(case / "p"), "--state-root", str(case / "s"))
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(Path(result["agent_setup_guide"]), REPO / "docs" / "AGENT_SETUP.md")
        self.assertEqual(Path(result["agent_setup_helper"]), REPO / "src" / "knowledge_forge" / "agent_setup.py")
        self.assertFalse(result["protected_promotion_installed_by_this_command"])
        self.assertEqual(result["status"], "CHECK_OK")
        self.assertIs(result["operator_cli_installed"], False)
        self.assertEqual(result["promotion_authority"], "EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY")
        self.assertIs(result["windows_support"]["operator_security_verified"], False)
        self.assertIs(result["windows_support"]["path_modified"], False)
        self.assertEqual(_tree(case), before)
        self.assertEqual(_tree(REPO / "src"), src_before, "check-only must not write bytecode caches")

    def test_fresh_install_exposes_exactly_two_working_entrypoints_and_truthful_manifest(self):
        case = self._case()
        result = self._ready(case / "p", case / "s")
        bin_dir = case / "p" / "bin"
        self.assertEqual(sorted(p.name for p in bin_dir.iterdir()), ["knowledge-forge-authoring.exe", "knowledge-forge.exe"])
        manifest = json.loads((case / "s" / "agent-integration.json").read_text(encoding="utf-8"))
        for key, value in {
            "distribution": "CHECKOUT_LOCAL", "operator_cli_installed": False,
            "operator_surface": "HOST_OWNED_NOT_INSTALLED", "trust_class": "UNTRUSTED_KNOWLEDGE_DATA",
            "authority": "KNOWLEDGE_ONLY", "instruction_authority": "NONE", "execution_authority": "NONE",
            "executable": False, "promotion_authority": "EXTERNAL_AUTHENTICATED_HUMAN_HOST_ONLY",
            "checkout_root": str(REPO), "consumer_cli": str(bin_dir / "knowledge-forge.exe"),
            "authoring_cli": str(bin_dir / "knowledge-forge-authoring.exe"), "python_executable": sys.executable,
        }.items():
            self.assertEqual(manifest[key], value, key)
        for stem in STEMS:
            run = subprocess.run([str(bin_dir / f"{stem}.exe"), "--help"], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn("usage", run.stdout.lower())
        self.assertEqual(result["writes"], {"knowledge-forge": "CREATED", "knowledge-forge-authoring": "CREATED",
                                            "agent-integration.json": "CREATED"})

    def test_operator_is_never_installed_or_referenced(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        self.assertEqual([p for p in case.rglob("*operator*")], [])
        for stem in STEMS:
            blob = (case / "p" / "bin" / f"{stem}.exe").read_bytes()
            self.assertNotIn(b"operator_cli", blob)
            self.assertNotIn(b"knowledge-forge-operator", blob)

    def test_exact_repeat_install_is_idempotent(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        before = _tree(case)
        second = self._ready(case / "p", case / "s")
        self.assertEqual(set(second["writes"].values()), {"IDENTICAL"})
        self.assertEqual(_tree(case), before)

    def test_tampered_command_is_refused_and_preserved(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        target = case / "p" / "bin" / "knowledge-forge.exe"
        tampered = target.read_bytes() + b"tamper"
        target.write_bytes(tampered)
        run = self._install("--prefix", str(case / "p"), "--state-root", str(case / "s"))
        self.assertEqual(run.returncode, 2)
        self.assertIn("refusing to overwrite different existing file", run.stderr)
        self.assertEqual(target.read_bytes(), tampered)

    def test_tampered_manifest_is_refused_and_preserved(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        manifest = case / "s" / "agent-integration.json"
        manifest.write_bytes(manifest.read_bytes().replace(b'"NONE"', b'"FULL"', 1))
        tampered = manifest.read_bytes()
        run = self._install("--prefix", str(case / "p"), "--state-root", str(case / "s"))
        self.assertEqual(run.returncode, 2)
        self.assertEqual(manifest.read_bytes(), tampered)

    def test_existing_operator_or_shadowing_shim_is_refused_without_ready(self):
        for name in ("knowledge-forge-operator.cmd", "knowledge-forge-operator.exe", "knowledge-forge-operator",
                     "knowledge-forge.cmd", "knowledge-forge-authoring.bat"):
            with self.subTest(name=name):
                case = self._case()
                (case / "p" / "bin").mkdir(parents=True)
                (case / "p" / "bin" / name).write_bytes(b"@echo foreign\r\n")
                run = self._install("--prefix", str(case / "p"), "--state-root", str(case / "s"))
                self.assertEqual(run.returncode, 2, run.stdout)
                self.assertNotIn("READY", run.stdout)
                self.assertFalse((case / "p" / "bin" / "knowledge-forge.exe").exists())
                self.assertFalse((case / "s").exists())

    def test_spaces_unicode_and_cmd_metacharacters_in_paths(self):
        case = self._case()
        weird = case / "ck \u00fc\u00f1\u00ef \u732b & (x) %y% !z! ^w"
        checkout = weird / "checkout"
        shutil.copytree(REPO / "src", checkout / "src", ignore=shutil.ignore_patterns("__pycache__"))
        (checkout / "bin").mkdir()
        shutil.copy2(CMD, checkout / "bin" / CMD.name)
        prefix, state = weird / "pre fix", weird / "st\u00e4te"
        result = self._ready(prefix, state, cmd=checkout / "bin" / CMD.name)
        self.assertEqual(result["checkout_root"], str(checkout))
        manifest = json.loads((state / "agent-integration.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["consumer_cli"], str(prefix / "bin" / "knowledge-forge.exe"))
        for stem in STEMS:
            run = subprocess.run([str(prefix / "bin" / f"{stem}.exe"), "--help"], capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr)

    def test_model_controlled_arguments_never_reach_a_shell(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        canary = case / "PWNED.txt"
        hostile = [f"a&echo x>{canary}", "b|whoami", "c>%TEMP%\\x", "%PATH%", "!x!", '"q"', "(p)", "^z"]
        run = subprocess.run([str(case / "p" / "bin" / "knowledge-forge.exe"), *hostile], capture_output=True,
                             text=True, timeout=120, cwd=str(case))
        self.assertNotEqual(run.returncode, 0)  # argparse rejects; nothing executed
        self.assertFalse(canary.exists(), "argument text was interpreted by a shell")
        self.assertIn("a&echo x>", run.stderr)  # received literally by Python

    def test_unc_device_traversal_and_reparse_destinations_are_refused(self):
        case = self._case()
        real = case / "real"
        real.mkdir()
        junction = case / "junc"
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(real)],
                       check=True, capture_output=True, timeout=60)
        for prefix in [r"\\localhost\C$\kf", r"\\?\C:\kf", r"\\.\C:\kf", "relative\\kf",
                       str(case / "a" / ".." / "b"), str(case / "con"), str(case / "x:ads"),
                       str(junction / "p")]:
            with self.subTest(prefix=prefix):
                before = _tree(case)
                run = self._install("--prefix", prefix, "--state-root", str(case / "s"))
                self.assertEqual(run.returncode, 2, run.stdout)
                self.assertEqual(_tree(case), before)
        self.assertEqual(list(real.iterdir()), [])
        os.rmdir(junction)

    def test_other_user_shared_and_system_locations_are_refused(self):
        sysroot = os.environ.get("SystemRoot", r"C:\Windows")
        for prefix in [os.path.join(sysroot, "kf-cp1-never"), r"C:\Users\Public\kf-cp1-never",
                       r"C:\Users\Default\kf-cp1-never", os.environ.get("ProgramFiles", r"C:\Program Files") + r"\kf-cp1-never"]:
            with self.subTest(prefix=prefix):
                run = self._install("--check-only", "--prefix", prefix, "--state-root", str(self._case() / "s"))
                self.assertEqual(run.returncode, 2, run.stdout)
                self.assertFalse(os.path.exists(prefix))

    def test_missing_or_invalid_localappdata_is_refused_without_fallback(self):
        for value in (None, "relative", r"\\server\share", r"C:\does-not-exist-kf-cp1"):
            with self.subTest(value=value):
                env = dict(self.env)
                env.pop("LOCALAPPDATA", None)
                if value is not None:
                    env["LOCALAPPDATA"] = value
                run = self._install("--check-only", env=env)
                self.assertEqual(run.returncode, 2, run.stdout)
                self.assertIn("LOCALAPPDATA", run.stderr)

    def test_partial_install_reconciles_and_preflight_failure_writes_nothing(self):
        case = self._case()
        self._ready(case / "p", case / "s")
        (case / "p" / "bin" / "knowledge-forge-authoring.exe").unlink()
        (case / "s" / "agent-integration.json").unlink()
        second = self._ready(case / "p", case / "s")
        self.assertEqual(second["writes"], {"knowledge-forge": "IDENTICAL", "knowledge-forge-authoring": "CREATED",
                                            "agent-integration.json": "CREATED"})
        fresh = self._case()
        (fresh / "s" / "agent-integration.json").mkdir(parents=True)  # manifest path occupied
        run = self._install("--prefix", str(fresh / "p"), "--state-root", str(fresh / "s"))
        self.assertEqual(run.returncode, 2)
        self.assertFalse((fresh / "p").exists(), "no entrypoint may be written before preflight passes")

    def test_missing_or_unsupported_python_is_bounded(self):
        case = self._case()
        for interp in (str(case / "no-such-python.exe"), os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "where.exe")):
            with self.subTest(interp=interp):
                env = dict(self.env, KNOWLEDGE_FORGE_PYTHON=interp)
                run = self._install("--prefix", str(case / "p"), "--state-root", str(case / "s"), env=env)
                self.assertEqual(run.returncode, 2)
                self.assertFalse((case / "p").exists())

    def _unicode_venv(self) -> Path:
        venv = self._case() / "py \u00fc\u732b & (v)"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True,
                       capture_output=True, timeout=300)
        return venv / "Scripts" / "python.exe"

    def test_unicode_interpreter_path_survives_the_launcher_shebang(self):
        python_exe = self._unicode_venv()
        launcher, _source, _pip = wi.find_launcher()
        out = self._case() / "bin"
        out.mkdir()
        exe = out / "knowledge-forge.exe"
        exe.write_bytes(wi.build_entrypoint(launcher, str(python_exe), "knowledge-forge", "knowledge_forge", str(REPO / "src")))
        run = subprocess.run([str(exe), "--help"], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("usage", run.stdout.lower())

    def test_interpreter_without_pip_launcher_is_refused_without_shell_fallback(self):
        python_exe = self._unicode_venv()
        case = self._case()
        run = self._install("--prefix", str(case / "p"), "--state-root", str(case / "s"),
                            env=dict(self.env, KNOWLEDGE_FORGE_PYTHON=str(python_exe)))
        self.assertEqual(run.returncode, 2, run.stdout)
        self.assertIn("no pip", run.stderr)
        self.assertEqual(list(case.iterdir()), [])

    def test_explicitly_empty_path_options_refuse_with_no_writes_anywhere(self):
        # %LOCALAPPDATA% points at a private test folder, so a fallback to the default
        # location would visibly write here; install mode (not --check-only) so a
        # regression would really write.
        for flag in ("--prefix", "--state-root", "--manifest"):
            for value in ("", "   "):
                with self.subTest(flag=flag, value=value):
                    case = self._case()
                    fake_local = case / "localappdata"
                    fake_local.mkdir()
                    env = dict(self.env, LOCALAPPDATA=str(fake_local))
                    args = {"--prefix": str(case / "p"), "--state-root": str(case / "s"),
                            "--manifest": str(case / "s" / "agent-integration.json")}
                    args[flag] = value
                    before = _tree(case)
                    run = self._install(*[x for kv in args.items() for x in kv], env=env)
                    self.assertEqual(run.returncode, 2, run.stdout)
                    self.assertIn(flag, run.stderr)
                    self.assertIn("non-empty", run.stderr)
                    self.assertNotIn("READY", run.stdout)
                    self.assertEqual(_tree(case), before, "an explicitly empty option must write nothing")
        # an empty option alone must not silently install into the default location either
        case = self._case()
        fake_local = case / "localappdata"
        fake_local.mkdir()
        run = self._install("--prefix", "", env=dict(self.env, LOCALAPPDATA=str(fake_local)))
        self.assertEqual(run.returncode, 2, run.stdout)
        self.assertEqual(list(fake_local.iterdir()), [], "fell back to the default %LOCALAPPDATA% prefix")

    def test_unknown_and_malformed_flags_are_rejected(self):
        for args in (["--bogus"], ["--prefix"], ["--check"]):
            with self.subTest(args=args):
                self.assertEqual(self._install(*args).returncode, 2)

    def test_help_works(self):
        run = self._install("--help")
        self.assertEqual(run.returncode, 0)
        self.assertRegex(run.stdout, r"knowledge-forge-operator\s+is\s+deliberately\s+NOT\s+installed")


if __name__ == "__main__":
    unittest.main()
