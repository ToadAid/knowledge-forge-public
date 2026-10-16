"""One-word interactive promotion approval — the trusted host in a terminal.

``bin/approve`` (or ``python3 -m knowledge_forge.approve_tui <request-dir>``)
is run by the human in their own terminal, never by the agent process. One
invocation is the whole user-facing flow:

1. show the approval card (skill, source, validation, what will be promoted);
2. require a typed ``APPROVE <token>`` (or ``REJECT <token>``) derived from
   the request digest, with bounded mismatch retries and fail-closed EOF;
3. sign the host decision with the host key this process owns;
4. drive the unchanged promotion engine (``approval_host_cli.run_approval``)
   to perform the real VALIDATED -> PROMOTED registry transition and write
   real receipts.

One-time administrator setup (``approve --setup``) creates the state root,
the 0600 host key, and an initialized governed registry. Everyday use is one
word. The honest boundary: when the host runs under the same Linux account as
the agent, independence is procedural (real TTY, typed confirmation, the
agent is built to never run this command), not OS-level; the hardened
separate-account deployment path remains the operator service.
"""

from __future__ import annotations

from argparse import ArgumentParser
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import sys
from typing import Any, Mapping, Protocol

from .approval import (
    ApprovalContractError,
    approval_request_state,
    bind_request_evidence,
    project_approval_request,
    require_current_candidate,
)
from .approval_host import ApprovalHostError, build_host_decision, read_host_key_file
from .approval_host_cli import ApprovalHostCliError, run_approval
from .operator_promotion import OperatorPromotionError
from .approval_tui import _confirmation_token
from .registry import (
    SkillRegistryError,
    create_skill_registry,
    save_skill_registry,
)
from .validate import PackageValidationError

DEFAULT_STATE_ROOT_NAME = Path("knowledge-forge") / "approval-host"
HOST_KEY_FILENAME = "host-key"
DEFAULT_REGISTRY_FILENAME = "registry.json"
BUNDLE_SUBDIRS = ("promotion-bundle", "bundle")
_DEFAULT_MISMATCH_ATTEMPTS = 3

_LAWS = (
    "VALIDATED != PROMOTED", "REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED",
    "KNOWLEDGE != AUTHORITY", "ACCEPTED != AUTHENTICATED",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_state_root() -> Path:
    home = Path(os.path.expanduser("~"))
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else home / ".local" / "state"
    return root / DEFAULT_STATE_ROOT_NAME


class ApproveTerminal(Protocol):
    """Same minimal terminal contract as the review TUI."""

    @property
    def input_is_tty(self) -> bool: ...

    @property
    def output_is_tty(self) -> bool: ...

    def write(self, text: str) -> None: ...

    def read_line(self, prompt: str) -> str: ...


class SystemApproveTerminal:
    @property
    def input_is_tty(self) -> bool:
        return sys.stdin.isatty()

    @property
    def output_is_tty(self) -> bool:
        return sys.stdout.isatty()

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def read_line(self, prompt: str) -> str:
        sys.stdout.write(prompt)
        sys.stdout.flush()
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.rstrip("\n")


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ApprovalContractError(f"{label} is missing or not a regular file: {path}")
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalContractError(f"{label} is not readable JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ApprovalContractError(f"{label} must contain a JSON object")
    return value


def _host_card(projection: Mapping[str, Any], *, host_id: str) -> str:
    width = 74
    expired = projection["state"] == "EXPIRED"
    warning = (
        "WARNING: REQUEST EXPIRED — approval unavailable"
        if expired
        else "REAL transition: this click promotes the skill in the registry"
    )
    border = "+" + "-" * (width + 2) + "+"

    def row(text: str) -> str:
        return f"| {text:<{width}} |"

    def wrapped(label: str, value: Any) -> list[str]:
        text = f"{label}: {value}"
        return [row(text[index:index + width]) for index in range(0, len(text), width)]

    lines = [border, row("KNOWLEDGE FORGE — PROMOTION APPROVAL"), border]
    lines.extend(wrapped("Agent", projection["requesting_agent"]))
    lines.extend(wrapped("Action", "Promote validated skill (registry transition)"))
    lines.extend(wrapped("Skill", projection["target_skill_id"]))
    lines.extend(wrapped("Version", projection["target_skill_version"]))
    lines.extend(wrapped("Source validation", "VALIDATED (package receipt-bound)"))
    lines.extend(wrapped(
        "What will be promoted",
        "Registry VALIDATED->PROMOTED + receipts; no tools granted,"
    ))
    lines.extend(wrapped("", "model stays unauthorized (model_authorized: false)"))
    lines.append(row("Package digest:"))
    lines.append(row(str(projection["package_receipt_sha256"])))
    lines.extend(wrapped(
        "Evidence", f"{len(projection['evidence_items'])} digest-bound items."
    ))
    lines.extend(wrapped("", "Recompute the hashes yourself before approving:"))
    for item in projection["evidence_items"]:
        lines.append(row(f"  {item['label']}: {item['sha256']}"))
    lines.extend(wrapped("Expires", projection["expires_at"]))
    lines.extend(wrapped("Host", host_id))
    lines.extend([row(warning), border])
    lines.extend([
        row("Type: APPROVE <confirmation phrase>   or  REJECT <confirmation phrase>"),
        row("The phrase is shown above each prompt; three mismatches refuse."),
        border,
        "",
    ])
    return "\n".join(lines)


def _resolve_bundle(request_dir: Path, bundle_dir: Path | None) -> Path:
    if bundle_dir is not None:
        if not bundle_dir.is_dir():
            raise ApprovalContractError(f"promotion bundle directory is missing: {bundle_dir}")
        return bundle_dir
    for name in BUNDLE_SUBDIRS:
        candidate = request_dir / name
        if (candidate / "package").is_dir() and (candidate / "lifecycle.validated.json").is_file():
            return candidate
    raise ApprovalContractError(
        f"no promotion bundle found under {request_dir} (expected "
        f"{' or '.join(name + '/' for name in BUNDLE_SUBDIRS)} with package/ and "
        "lifecycle.validated.json): the agent must stage the validated bundle "
        "next to the request artifacts"
    )


def _resolve_registry(
    registry_path: Path | None, state_root: Path
) -> Path:
    if registry_path is not None:
        return registry_path
    return state_root / DEFAULT_REGISTRY_FILENAME


def _resolve_host_key(
    host_key_path: Path | None, state_root: Path
) -> Path:
    if host_key_path is not None:
        return host_key_path
    return state_root / HOST_KEY_FILENAME


def _write_decision_file(
    request_dir: Path, decision: Mapping[str, Any]
) -> Path:
    path = request_dir / "approval-host-decision.json"
    path.write_text(
        json.dumps(decision, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def _fail_closed(terminal: ApproveTerminal, message: str) -> None:
    terminal.write(f"\nREFUSED: {message}\n")
    terminal.write(
        "promoted: false | model_authorized: false | "
        f"laws: {', '.join(_LAWS)}\n"
    )


def run_approve_tui(
    request_dir: Path,
    terminal: ApproveTerminal,
    *,
    bundle_dir: Path | None = None,
    state_root: Path | None = None,
    registry_path: Path | None = None,
    host_key_path: Path | None = None,
    host_id: str | None = None,
    decided_by: str | None = None,
) -> dict[str, Any]:
    return _run_approve_tui_at(
        request_dir,
        terminal,
        bundle_dir=bundle_dir,
        state_root=state_root,
        registry_path=registry_path,
        host_key_path=host_key_path,
        host_id=host_id,
        decided_by=decided_by,
        approved_at=_now(),
    )


def _run_approve_tui_at(
    request_dir: Path,
    terminal: ApproveTerminal,
    *,
    bundle_dir: Path | None,
    state_root: Path | None,
    registry_path: Path | None,
    host_key_path: Path | None,
    host_id: str | None,
    decided_by: str | None,
    approved_at: str,
) -> dict[str, Any]:
    """Deterministic-time core for tests; see run_approve_tui."""

    if not terminal.input_is_tty:
        raise ApprovalContractError("interactive TTY input is required")
    if not terminal.output_is_tty:
        raise ApprovalContractError("interactive TTY output is required")

    request_dir = Path(request_dir)
    state_root = Path(state_root) if state_root else default_state_root()
    host_id = host_id or "local-terminal-shell"
    decided_by = decided_by or f"local-terminal-user:{getpass.getuser()}"

    request = _load_json(
        request_dir / "approval-request.json", "approval request"
    )
    evidence = _load_json(
        request_dir / "approval-evidence.json", "approval evidence"
    )
    binding = _load_json(
        request_dir / "current-candidate.json", "current candidate binding"
    )
    request, evidence = bind_request_evidence(request, evidence)
    require_current_candidate(request, binding)

    projection = project_approval_request(
        request,
        evidence,
        surface="TERMINAL_TUI",
        as_of=approved_at,
    )
    terminal.write(_host_card(projection, host_id=host_id))
    if approval_request_state(request, as_of=approved_at) == "EXPIRED":
        _fail_closed(terminal, "expired approval request refused")
        raise ApprovalContractError("expired approval request refused")

    try:
        bundle = _resolve_bundle(request_dir, bundle_dir)
    except ApprovalContractError as exc:
        _fail_closed(terminal, str(exc))
        raise
    registry = _resolve_registry(registry_path, state_root)
    key_file = _resolve_host_key(host_key_path, state_root)

    token = _confirmation_token(projection["request_sha256"])
    approved = None
    mismatches = 0
    for _ in range(_DEFAULT_MISMATCH_ATTEMPTS):
        try:
            answer = terminal.read_line(
                f"Approve this promotion? Type APPROVE {token} "
                f"(or REJECT {token}) > "
            )
        except EOFError:
            _fail_closed(terminal, "no decision entered (input ended)")
            raise ApprovalContractError(
                "no decision entered: interactive approval requires a typed "
                "confirmation; refusing fail-closed"
            ) from None
        answer = answer.strip()
        if answer == f"APPROVE {token}":
            approved = True
            break
        if answer == f"REJECT {token}":
            approved = False
            break
        mismatches += 1
        remaining = _DEFAULT_MISMATCH_ATTEMPTS - mismatches
        if remaining:
            terminal.write(
                "confirmation mismatch after "
                f"{mismatches} attempt(s); {remaining} attempt(s) remain\n"
            )
    if approved is None:
        _fail_closed(
            terminal,
            f"approval confirmation mismatch after {_DEFAULT_MISMATCH_ATTEMPTS} "
            "attempts; refusing fail-closed",
        )
        raise ApprovalContractError(
            "approval confirmation mismatch after "
            f"{_DEFAULT_MISMATCH_ATTEMPTS} attempts; refusing fail-closed"
        )

    key = read_host_key_file(key_file)
    decision = build_host_decision(
        request,
        decision="APPROVED" if approved else "REJECTED",
        decided_by=decided_by,
        host_id=host_id,
        decision_nonce=secrets.token_hex(16),
        decided_at=approved_at,
        host_key=key,
    )
    decision_path = _write_decision_file(request_dir, decision)
    terminal.write(
        f"signed decision recorded: {decision_path}\nhost: {host_id} "
        f"as {decided_by}\n"
    )

    if not approved:
        terminal.write(
            "\nDecision: REJECTED. Nothing was promoted; no registry change.\n"
            f"Laws: {', '.join(_LAWS)}\n"
        )
        return {
            "ok": True,
            "command": "approve-tui",
            "decision": "REJECTED",
            "promoted": False,
            "model_authorized": False,
            "host_id": host_id,
            "decided_by": decided_by,
            "decision_path": str(decision_path),
            "laws": list(_LAWS),
        }

    if not registry.is_file():
        _fail_closed(
            terminal,
            f"governed registry not found at {registry}: the approval state "
            "root must be initialized by the administrator (approve --setup) "
            "before a promotion can complete",
        )
        raise ApprovalHostError(
            f"governed registry not found at {registry}: run 'approve --setup' "
            "once as the administrator"
        )

    try:
        result = run_approval(
            request_dir,
            bundle=bundle,
            registry_path=registry,
            host_key_path=key_file,
            promoted_lifecycle_out=state_root / "lifecycle.promoted.json",
            receipt_out=state_root / "receipt.json",
            now=approved_at,
        )
    except (
        ApprovalHostError,
        ApprovalHostCliError,
        ApprovalContractError,
        OperatorPromotionError,
        SkillRegistryError,
        PackageValidationError,
    ) as exc:
        _fail_closed(terminal, str(exc))
        raise

    terminal.write(
        "\nPROMOTED. A real registry transition was executed and receipts "
        "written:\n"
        f"  registry: {registry}\n"
        f"  promoted lifecycle: {state_root / 'lifecycle.promoted.json'}\n"
        f"  receipt: {state_root / 'receipt.json'}\n"
    )
    return {
        **result,
        "ok": True,
        "command": "approve-tui",
        "host_id": host_id,
        "decided_by": decided_by,
        "decision_path": str(decision_path),
        "laws": list(_LAWS),
    }


def run_setup(
    state_root: Path, *, now: str
) -> dict[str, Any]:
    """One-time administrator setup: state root, 0600 host key, registry."""

    state_root = Path(state_root)
    state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key_path = state_root / HOST_KEY_FILENAME
    if key_path.exists():
        raise ApprovalHostError(
            f"host key already exists at {key_path}: refusing to overwrite "
            "the trust anchor"
        )
    fd = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, secrets.token_bytes(32))
    finally:
        os.close(fd)
    registry_path = state_root / DEFAULT_REGISTRY_FILENAME
    created_registry = False
    if not registry_path.exists():
        save_skill_registry(create_skill_registry(created_at=now), registry_path)
        created_registry = True
    return {
        "ok": True,
        "command": "approve-setup",
        "state_root": str(state_root),
        "host_key_path": str(key_path),
        "registry_path": str(registry_path),
        "registry_created": created_registry,
        "note": (
            "One-time administrator setup complete. Everyday use is now one "
            "word: run 'approve <request-dir>' in your own terminal."
        ),
    }


def build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="approve",
        description=(
            "One-word promotion approval: shows the approval card in this "
            "terminal, takes a typed APPROVE/REJECT, signs the host decision "
            "with the trusted host key this process holds, and — only after "
            "every check — performs the real PROMOTED registry transition."
        ),
    )
    parser.add_argument(
        "request_dir", nargs="?",
        help="directory holding the staged approval artifacts (approval-*.json, current-candidate.json)",
    )
    parser.add_argument("--bundle", default=None, help="override the promotion bundle directory (default: <request-dir>/promotion-bundle)")
    parser.add_argument("--state-root", default=None, help="approval-host state root (default: XDG state knowledge-forge/approval-host)")
    parser.add_argument("--registry", default=None, help="governed registry.json (default: <state-root>/registry.json)")
    parser.add_argument("--host-key", default=None, help="host key file (default: <state-root>/host-key)")
    parser.add_argument("--host-id", default=None, help="presentation host id (default: local-terminal-shell)")
    parser.add_argument("--decided-by", default=None, help="principal reference recorded as the host-verified decision maker")
    parser.add_argument("--setup", action="store_true", help="one-time administrator setup: create the state root, 0600 host key and initial registry")
    parser.add_argument("--now", default=None, help="RFC3339 now (defaults to current UTC time)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    now = args.now or _now()
    state_root = Path(args.state_root) if args.state_root else default_state_root()
    if args.setup:
        try:
            payload = run_setup(state_root, now=now)
        except ApprovalHostError as exc:
            _emit({"ok": False, "command": "approve-setup", "error": str(exc)})
            return 2
        _emit(payload)
        return 0
    if args.request_dir is None:
        parser.error("request_dir is required unless --setup is given")
        return 2
    try:
        payload = run_approve_tui(
            Path(args.request_dir),
            SystemApproveTerminal(),
            bundle_dir=Path(args.bundle) if args.bundle else None,
            state_root=state_root,
            registry_path=Path(args.registry) if args.registry else None,
            host_key_path=Path(args.host_key) if args.host_key else None,
            host_id=args.host_id,
            decided_by=args.decided_by,
        )
    except (
        ApprovalContractError,
        ApprovalHostError,
        ApprovalHostCliError,
        OperatorPromotionError,
        SkillRegistryError,
        PackageValidationError,
    ) as exc:
        _emit({"ok": False, "command": "approve-tui", "error": str(exc), "promoted": False, "model_authorized": False})
        return 2
    _emit(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())