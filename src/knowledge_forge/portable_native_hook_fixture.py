"""P3D-P4-P1-P1-P0: temporary native hook configuration fixtures, NOT live dispatch.

Creates an isolated, knowledge-free synthetic event recorder with no host CLI
launch, no user configuration writes, no promotion, and no model context output.
A separate human approval and OS isolation proof are required for real sessions.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
from typing import Any, Sequence

HOSTS = frozenset({"claude-code", "codex-cli"})
ROOT_PREFIX = "kf-native-hook-fixture-"
MAX_EVENT_BYTES = 16_384

# Self-contained test handler; it never imports the Knowledge Forge runtime.
# A production hook must NOT be inferred from this synthetic-only fixture.
HANDLER_SOURCE = '''#!/usr/bin/env python3
"""Ephemeral test event recorder. No stdout, decisions, or knowledge data."""
import json
import sys

def main():
    try:
        data = sys.stdin.buffer.read(16385)
        if not 0 < len(data) <= 16384:
            return 0
        event = json.loads(data.decode("utf-8"))
        if not isinstance(event, dict) or event.get("hook_event_name") != "UserPromptSubmit":
            return 0
        prompt = event.get("prompt")
        if not isinstance(prompt, str):
            return 0
        # Record event metadata only; never raw prompt, ids, cwd, paths or secrets.
        record = {"event": "UserPromptSubmit", "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                  "has_session": isinstance(event.get("session_id"), str),
                  "has_turn": isinstance(event.get("turn_id"), str)}
        with open(sys.argv[1], "a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\\n")
    except Exception:
        # Test fixture fails open with no stderr; never block an ordinary prompt.
        pass
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
'''


class NativeHookFixtureError(ValueError):
    """An unsafe fixture request refused without exposing local paths."""


def _root_check(root: Path) -> None:
    if not isinstance(root, Path) or root.name.startswith(ROOT_PREFIX) is False:
        raise NativeHookFixtureError("fixture root refused")
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise NativeHookFixtureError("fixture root must be a real absolute directory")
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        # Native Windows has no POSIX owner id: refuse as a typed, path-free
        # refusal instead of an unhandled AttributeError (P3B-CP1 R2).
        raise NativeHookFixtureError("fixture root ownership cannot be verified on this platform")
    try:
        info = root.stat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != getuid() or
                stat.S_IMODE(info.st_mode) != 0o700 or any(root.iterdir())):
            raise NativeHookFixtureError("fixture root must be private and empty")
    except OSError:
        raise NativeHookFixtureError("fixture root unavailable") from None


def stage_fixture(host: str, root: Path) -> dict[str, Path]:
    """Stage configs ONLY within an owned, empty, 0700 temporary directory."""
    if host not in HOSTS:
        raise NativeHookFixtureError("unknown host")
    _root_check(root)
    home = root / "sandbox-home"
    project = root / "project"
    config_dir = home / (".claude" if host == "claude-code" else ".codex")
    config_dir.mkdir(parents=True, mode=0o700)
    project.mkdir(mode=0o700)
    handler = root / "event_recorder.py"
    handler.write_text(HANDLER_SOURCE, encoding="utf-8")
    handler.chmod(0o600)
    record = root / "synthetic-events.jsonl"
    # Native command is POSIX shell-quoted, absolute, knowledge-free and inert.
    command = " ".join(shlex.quote(x) for x in (sys.executable, str(handler), str(record)))
    config = {"hooks": {"UserPromptSubmit": [{"hooks": [
        {"type": "command", "command": command, "timeout": 10}
    ]}]}}
    config_file = config_dir / ("settings.json" if host == "claude-code" else "hooks.json")
    config_file.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    config_file.chmod(0o600)
    return {"root": root, "home": home, "project": project, "config": config_file,
            "handler": handler, "record": record, "config_dir": config_dir}


def replay_fixture(host: str) -> dict[str, Any]:
    """Invoke only the mock hook handler, never claude/codex or a real model."""
    if host not in HOSTS:
        raise NativeHookFixtureError("unknown host")
    with TemporaryDirectory(prefix=ROOT_PREFIX) as scratch:
        root = Path(scratch)
        fixture = stage_fixture(host, root)
        event = {"hook_event_name": "UserPromptSubmit", "session_id": "synthetic-only",
                 "prompt": "PRIVATE_SYNTHETIC_NO_MODEL_🐸"}
        if host == "codex-cli":
            event["turn_id"] = "synthetic-turn"
        wire = json.dumps(event, ensure_ascii=False).encode("utf-8")
        results = []
        for _ in range(2):
            # Explicit minimal env: do not inherit credentials, plugins, or loaders.
            env = {"PATH": os.defpath, "HOME": str(fixture["home"]),
                   "CODEX_HOME": str(fixture["config_dir"]) if host == "codex-cli" else str(root / "empty-codex"),
                   "CLAUDE_CONFIG_DIR": str(fixture["config_dir"]) if host == "claude-code" else str(root / "empty-claude"),
                   "TMPDIR": str(root), "LANG": "C.UTF-8", "CI": "1"}
            done = subprocess.run([sys.executable, str(fixture["handler"]), str(fixture["record"])],
                                  input=wire, cwd=fixture["project"], env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=5, check=False)
            if done.returncode != 0 or done.stdout or done.stderr:
                raise NativeHookFixtureError("synthetic handler was not silent and nonblocking")
            results.append(done.returncode)
        try:
            records = [json.loads(line) for line in fixture["record"].read_text(encoding="utf-8").splitlines()]
        except (OSError, ValueError):
            raise NativeHookFixtureError("synthetic evidence unavailable") from None
        expected = {"event": "UserPromptSubmit", "prompt_utf8_bytes": len(event["prompt"].encode("utf-8")),
                    "has_session": True, "has_turn": host == "codex-cli"}
        if records != [expected, expected]:
            raise NativeHookFixtureError("synthetic event replay mismatch")
        if event["prompt"] in fixture["record"].read_text(encoding="utf-8"):
            raise NativeHookFixtureError("synthetic prompt appeared in evidence")
        return {"schema_version": "0.1", "host": host, "mode": "STAGED_SYNTHETIC_ONLY",
                "synthetic_dispatch_count": len(records), "synthetic_return_codes": results,
                "all_stdout_bytes": 0, "all_stderr_bytes": 0,
                "native_host_sessions_started": False, "native_hook_dispatched": False,
                "native_dispatch_verified": False, "safe_to_activate_hooks": False,
                "host_user_configuration_modified": False,
                "fixture_removed_at_end_of_run": True, "model_context_injected": False,
                "promotion_authorized": False, "instruction_authority": "NONE",
                "execution_authority": "NONE", "tool_grants": []}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare temporary synthetic hook fixture, never run native hosts")
    parser.add_argument("--host", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args(argv)
    try:
        if args.mode != "PREPARE_ONLY":
            raise NativeHookFixtureError("native execution not authorized")
        print(json.dumps(replay_fixture(args.host), sort_keys=True))
        return 0
    except (NativeHookFixtureError, OSError, subprocess.TimeoutExpired):
        print("Native hook fixture refused; native host not started", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
