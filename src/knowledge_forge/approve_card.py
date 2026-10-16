"""Claude-side on-screen approval card: a desktop host, not a chat surface.

The agent may stage the request and pop this card on the user's screen, but
the card window is the host that renders **Approve & Promote / Reject**. A
click-only host action (with one Confirm click, no typing) selects the
adapter's one-shot approval decision. The decision travels through the
generic native-host adapter contract (``prepare_generic_host_approval`` /
``complete_generic_host_approval``): chat text never authorizes, the agent
process never signs, and the card refuses fail-closed without a host key and
an initialized registry.

When the card shares the user's account with the agent, host independence is
procedural (a real desktop window owned by this host program, clicked inside
it), not OS-level — and the card says so.
"""

from __future__ import annotations

from datetime import datetime, timezone
import getpass
import html
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
from typing import Any, Mapping, Protocol

from .approval import ApprovalContractError
from .generic_host_adapter import (
    GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
    GenericHostAdapterError,
    HostAdapterProfile,
    complete_generic_host_approval,
    prepare_generic_host_approval,
)
from .approval_host import ApprovalHostError
from .approval_host_cli import ApprovalHostCliError
from .inline_approval import InlineApprovalError
from .operator_promotion import OperatorPromotionError
from .registry import (
    SkillRegistryError,
    create_skill_registry,
    save_skill_registry,
)
from .validate import PackageValidationError


CARD_SCHEMA_VERSION = "0.1"
CARD_KIND = "KNOWLEDGE_FORGE_CLAUDE_APPROVAL_CARD"
DEFAULT_STATE_ROOT_NAME = Path("knowledge-forge") / "approval-host"
HOST_KEY_FILENAME = "host-key"
DEFAULT_REGISTRY_FILENAME = "registry.json"
CLAUDE_ADAPTER_ID = "claude"

_LAWS = (
    "VALIDATED != PROMOTED",
    "REVIEWED != AUTHENTICATED != AUTHORIZED != EXECUTED",
    "KNOWLEDGE != AUTHORITY",
    "ACCEPTED != AUTHENTICATED",
)


class ApproveCardError(ValueError):
    """The approval card could not be shown or completed lawfully."""


class ApproveCardUI(Protocol):
    """Minimal host-window contract; the zenity implementation backs it."""

    def show(self, text: str) -> None: ...

    def ask_decision(self, text: str) -> str | None:
        """Return 'APPROVE', 'REJECT', or None when the user closes."""

    def ask_confirm(self, text: str) -> bool:
        """Second click: True = confirmed, False = cancelled."""


class ScriptedCard:
    """Test fake: replays scripted user actions in order."""

    def __init__(
        self,
        decisions: list[str | None],
        confirmations: list[bool | None],
    ) -> None:
        self.decisions = list(decisions)
        self.confirmations = list(confirmations)
        self.shown: list[str] = []

    def show(self, text: str) -> None:
        self.shown.append(text)

    def ask_decision(self, text: str) -> str | None:
        self.shown.append(text)
        return self.decisions.pop(0)

    def ask_confirm(self, text: str) -> bool | None:
        self.shown.append(text)
        if not self.confirmations:
            raise EOFError("no scripted confirmation left")
        confirm = self.confirmations.pop(0)
        if confirm is None:
            return False
        return bool(confirm)


def default_state_root() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path(os.path.expanduser("~")) / ".local" / "state"
    return root / DEFAULT_STATE_ROOT_NAME


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve(root: Path | None, filename: str) -> Path:
    base = Path(root) if root else default_state_root()
    return base / filename


def _card_text(card: Mapping[str, Any]) -> str:
    lines = [
        "Knowledge Forge — promotion approval card",
        "",
        f"  skill: {card['skill_id']} (version {card['skill_version']})",
        f"  title: {card['title']}",
        f"  units: {card['unit_count']}   claims: {card['claim_count']}",
        f"  source coverage: {card['source_corpus_coverage']}",
    ]
    if card.get("coverage_warning"):
        lines.append(f"  coverage warning: {card['coverage_warning']}")
    lines += [
        "",
        f"  package receipt:    {card['package_receipt_sha256']}",
        f"  validated lifecycle: {card['validated_lifecycle_head_sha256']}",
        f"  request digest:     {card['request_sha256']}",
        f"  request expires:    {card['expires_at']}",
        "",
        "What approving does:",
        "  one real PROMOTED registry transition through the unchanged",
        "  verified pipeline (signature, expiry, binding, single-use ledger).",
        "",
        "To approve: click [Approve & Promote], then confirm once.",
        "",
        f"Laws: {', '.join(_LAWS)}",
        "The card window is the approval host. When it shares this account",
        "with the agent, that independence is procedural — your clicks in a",
        "window the agent does not operate — not OS-level. Chat text can",
        "never authorize.",
    ]
    return "\n".join(lines)


def _run_approve_card_at(
    request_dir: Path,
    card: ApproveCardUI,
    *,
    bundle_dir: Path | None = None,
    state_root: Path | None = None,
    registry_path: Path | None = None,
    host_key_path: Path | None = None,
    host_id: str | None = None,
    decided_by: str | None = None,
    now: str,
) -> dict[str, Any]:
    """Deterministic-time core for tests; the CLI wraps this."""

    request_dir = Path(request_dir)
    state_root = Path(state_root) if state_root else default_state_root()
    host_id = host_id or "claude-approval-card"
    decided_by = decided_by or f"local-desktop-user:{getpass.getuser()}"

    try:
        prepared = prepare_generic_host_approval(
            request_dir,
            _bundle_for(request_dir, bundle_dir),
            adapter_id=CLAUDE_ADAPTER_ID,
            observed_at=now,
        )
    except (InlineApprovalError, GenericHostAdapterError) as exc:
        _refuse(card, f"the approval request cannot be used yet: {exc}")
        raise

    card.show(_card_text(prepared).rstrip("\n"))

    decision = card.ask_decision(
        "Approve this promotion? [Approve & Promote] or [Reject]"
    )
    if decision is None:
        _refuse(card, "no decision was made; refusing fail-closed")
        return _refused_payload(host_id, decided_by, "closed without a decision")

    host_event = {
        "schema_version": GENERIC_HOST_ADAPTER_SCHEMA_VERSION,
        "kind": "KNOWLEDGE_FORGE_NATIVE_HOST_APPROVAL_EVENT",
        "adapter_id": CLAUDE_ADAPTER_ID,
        "approval_id": f"approval-card-{secrets.token_hex(8)}",
        "request_sha256": prepared["request_sha256"],
        "decision": (
            prepared["host_adapter"]["approval_decision"]
            if decision == "APPROVE"
            else prepared["host_adapter"]["rejection_decision"]
        ),
        "reviewer_principal": decided_by,
        "decided_at": now,
        "authentication": {
            "mechanism": prepared["host_adapter"]["authentication_mechanism"],
            "one_shot": True,
            "bypass_active": False,
            "review_surface": prepared["host_adapter"]["review_surface"],
        },
    }

    if decision == "APPROVE":
        confirmed = card.ask_confirm(
            "Confirm promotion?\n\n"
            f"skill: {prepared['skill_id']}\n"
            f"request digest: {prepared['request_sha256']}\n\n"
            "This executes one real PROMOTED registry transition."
        )
        if not confirmed:
            _refuse(
                card, "the promotion was not confirmed; refusing fail-closed"
            )
            return _refused_payload(
                host_id, decided_by, "promotion not confirmed"
            )

    registry = registry_path or _resolve(state_root, DEFAULT_REGISTRY_FILENAME)
    key_file = host_key_path or _resolve(state_root, HOST_KEY_FILENAME)
    try:
        result = complete_generic_host_approval(
            request_dir,
            _bundle_for(request_dir, bundle_dir),
            host_event=host_event,
            adapter_id=CLAUDE_ADAPTER_ID,
            registry_path=registry,
            host_key_path=key_file,
            now=now,
        )
    except (
        ApprovalHostError,
        ApprovalHostCliError,
        ApprovalContractError,
        GenericHostAdapterError,
        InlineApprovalError,
        OperatorPromotionError,
        SkillRegistryError,
        PackageValidationError,
    ) as exc:
        _refuse(card, str(exc))
        raise

    if decision == "REJECT":
        card.show(
            "Decision: Reject. Nothing was promoted; no registry change.\n"
            f"Laws: {', '.join(_LAWS)}\n"
        )
    else:
        card.show(
            "PROMOTED. A real registry transition was executed.\n"
            f"  registry: {registry}\n"
            f"  promoted lifecycle: {_lifecycle_result_path(result)}\n"
            f"  receipt: {_receipt_result_path(result)}\n"
            f"Laws: {', '.join(_LAWS)}\n"
        )
    return {
        **result,
        "ok": True,
        "command": "approve-card",
        "card_schema_version": CARD_SCHEMA_VERSION,
        "card_kind": CARD_KIND,
        "host_id": host_id,
        "reviewer_principal": decided_by,
        "chat_text_authorized": False,
        "laws": list(_LAWS),
    }


def _bundle_for(request_dir: Path, bundle_dir: Path | None) -> Path:
    if bundle_dir is not None:
        return Path(bundle_dir)
    return Path(request_dir) / "promotion-bundle"


def _lifecycle_result_path(result: Mapping[str, Any]) -> str:
    for key in ("promoted_lifecycle_out", "promoted_lifecycle_path"):
        if key in result and isinstance(result[key], (str, Path)):
            return str(result[key])
    return "(see result JSON)"


def _receipt_result_path(result: Mapping[str, Any]) -> str:
    for key in ("receipt_out", "receipt_path"):
        if key in result and isinstance(result[key], (str, Path)):
            return str(result[key])
    return "(see result JSON)"


def _refused_payload(host_id: str, decided_by: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "command": "approve-card",
        "promoted": False,
        "model_authorized": False,
        "host_id": host_id,
        "reviewer_principal": decided_by,
        "refusal": reason,
        "laws": list(_LAWS),
    }


def _refuse(card: ApproveCardUI, message: str) -> None:
    card.show(f"REFUSED (fail-closed): {message}\nNo promotion occurred.")


def run_setup(state_root: Path | None, *, now: str) -> dict[str, Any]:
    """One-time administrative provisioning: state root, 0600 key, registry."""

    state_root = Path(state_root) if state_root else default_state_root()
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
    if registry_path.exists():
        raise ApprovalHostError(
            f"registry already exists at {registry_path}: refusing to overwrite"
        )
    save_skill_registry(create_skill_registry(created_at=now), registry_path)
    return {
        "ok": True,
        "command": "approve-card-setup",
        "state_root": str(state_root),
        "host_key_path": str(key_path),
        "registry_path": str(registry_path),
    }


class ZenityCard:
    """Real desktop card driven through zenity (no extra dependencies)."""

    def __init__(self) -> None:
        if not self._display_environment():
            raise ApproveCardError(
                "no desktop display session is available; the approval card "
                "requires the user's own graphical session"
            )
        zenity = shutil.which("zenity")
        if not zenity:
            raise ApproveCardError(
                "the desktop card needs zenity installed; the exact missing "
                "dependency is zenity (no approval is possible without it)"
            )
        self.zenity = zenity

    @staticmethod
    def _display_environment() -> bool:
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    def _run(self, args: list[str], *, text_input: bytes | None = None) -> int:
        proc = subprocess.run(  # noqa: S603 - fixed argv, host-owned binary
            [self.zenity, *args],
            input=text_input,
            capture_output=True,
        )
        diagnostic = proc.stderr.decode("utf-8", errors="replace").strip()
        if diagnostic:
            # Zenity uses exit 1 both for a human pressing the cancel button
            # and for some GTK startup failures.  A subprocess diagnostic is
            # therefore not a trustworthy human Reject/Cancel event.  Refuse
            # before the caller can create or sign a host decision.
            bounded = diagnostic[:500]
            raise ApproveCardError(
                "approval window failed before a human decision: " + bounded
            )
        if proc.returncode not in (0, 1):
            raise ApproveCardError(
                "approval window failed before a human decision "
                f"(zenity exit {proc.returncode})"
            )
        return proc.returncode

    def show(self, text: str) -> None:
        self._run(
            [
                "--text-info",
                "--title=Knowledge Forge — approval card",
                "--width=720",
                "--height=640",
            ],
            text_input=text.encode("utf-8"),
        )

    def ask_decision(self, text: str) -> str | None:
        rc = self._run(
            [
                "--question",
                "--title=Approve promotion?",
                f"--text={html.escape(text)}",
                "--ok-label=Approve && Promote",
                "--cancel-label=Reject",
                "--width=560",
            ]
        )
        if rc == 0:
            return "APPROVE"
        if rc == 1:
            return "REJECT"
        return None

    def ask_confirm(self, text: str) -> bool | None:
        rc = self._run(
            [
                "--question",
                "--title=Confirm promotion",
                f"--text={html.escape(text)}",
                "--ok-label=Confirm && Promote",
                "--cancel-label=Cancel",
                "--width=600",
            ]
        )
        if rc == 0:
            return True
        if rc == 1:
            return False
        return None


def _emit(payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def build_parser():
    from argparse import ArgumentParser

    parser = ArgumentParser(
        prog="approve-card",
        description=(
            "On-screen approval card (desktop host): one human Approve/Reject "
            "clicked in a real window drives the unchanged verified pipeline "
            "to a real PROMOTED registry transition."
        ),
    )
    parser.add_argument("request_dir", nargs="?", default=None,
                        help="directory holding the approval contract artifacts")
    parser.add_argument("--bundle", default=None,
                        help="override bundle directory (default: <request-dir>/promotion-bundle)")
    parser.add_argument("--state-root", default=None,
                        help="approval-host state root (default: XDG state knowledge-forge/approval-host)")
    parser.add_argument("--registry", default=None,
                        help="governed registry.json (default: <state-root>/registry.json)")
    parser.add_argument("--host-key", default=None,
                        help="host key file (default: <state-root>/host-key)")
    parser.add_argument("--host-id", default=None,
                        help="host id for the card presentation")
    parser.add_argument("--decided-by", default=None,
                        help="principal recorded for the human decision")
    parser.add_argument("--setup", action="store_true",
                        help="one-time administrative provisioning (state root, 0600 host key, registry)")
    parser.add_argument("--now", default=None, help="RFC3339 now (defaults to current UTC time)")
    return parser


_CLI_ERRORS = (
    ApprovalContractError,
    ApprovalHostError,
    ApprovalHostCliError,
    GenericHostAdapterError,
    InlineApprovalError,
    OperatorPromotionError,
    SkillRegistryError,
    PackageValidationError,
    ApproveCardError,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    now = args.now or _now()
    if args.setup:
        try:
            payload = run_setup(args.state_root, now=now)
        except ApprovalHostError as exc:
            _emit({"ok": False, "command": "approve-card-setup", "error": str(exc)})
            return 2
        _emit(payload)
        return 0
    if args.request_dir is None:
        parser.error("request_dir is required unless --setup is given")
        return 2
    card = None
    try:
        card = ZenityCard()
        payload = _run_approve_card_at(
            Path(args.request_dir),
            card,
            bundle_dir=Path(args.bundle) if args.bundle else None,
            state_root=Path(args.state_root) if args.state_root else None,
            registry_path=Path(args.registry) if args.registry else None,
            host_key_path=Path(args.host_key) if args.host_key else None,
            host_id=args.host_id,
            decided_by=args.decided_by,
            now=now,
        )
    except _CLI_ERRORS as exc:
        payload = {"ok": False, "command": "approve-card", "error": str(exc),
                   "promoted": False, "model_authorized": False}
        try:
            _refuse(card, str(exc))
        except Exception:
            pass
        _emit(payload)
        return 2
    _emit(payload)
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
