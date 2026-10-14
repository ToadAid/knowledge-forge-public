"""CLI for model-assisted, zero-promotion agent draft materialization."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from .agent_authoring import (
    AgentAuthoringError,
    finalize_agent_draft,
    prepare_agent_draft,
)


def _emit(value: Mapping[str, Any], *, stream=None) -> None:
    target = stream if stream is not None else sys.stdout
    target.write(json.dumps(dict(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n")


def _load_plan(path_value: str) -> dict[str, Any]:
    path = Path(path_value)
    if path.is_symlink() or not path.is_file():
        raise AgentAuthoringError("plan must be a regular non-symlink file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AgentAuthoringError("plan must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise AgentAuthoringError("plan must contain a JSON object")
    return value


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source")
    parser.add_argument("draft")
    parser.add_argument("--knowledge-id", required=True)
    parser.add_argument("--skill-slug", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument(
        "--default-kind",
        default="CONCEPT",
        choices=(
            "CONCEPT",
            "FRAMEWORK",
            "PROCEDURE",
            "DECISION_RULE",
            "TRADEOFF",
            "ANTI_PATTERN",
            "DEFINITION",
            "EXAMPLE",
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knowledge-forge-authoring",
        description=(
            "Prepare and finalize model-assisted agent-authored knowledge. "
            "This CLI cannot promote."
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="Emit exact normalized source + authored claims for support selection.",
    )
    _add_common(prepare)
    prepare.add_argument("--occurred-at")

    finalize = commands.add_parser(
        "finalize",
        help="Build a new-only VALIDATED package from an exact model support plan.",
    )
    _add_common(finalize)
    finalize.add_argument("--plan", required=True)
    finalize.add_argument("--output-root", required=True)
    finalize.add_argument("--occurred-at", required=True)
    finalize.add_argument("--validator-id", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            value = prepare_agent_draft(
                args.source,
                args.draft,
                knowledge_id=args.knowledge_id,
                skill_slug=args.skill_slug,
                title=args.title,
                default_kind=args.default_kind,
                occurred_at=args.occurred_at,
            )
        else:
            value = finalize_agent_draft(
                args.source,
                args.draft,
                _load_plan(args.plan),
                args.output_root,
                knowledge_id=args.knowledge_id,
                skill_slug=args.skill_slug,
                title=args.title,
                default_kind=args.default_kind,
                occurred_at=args.occurred_at,
                validator_id=args.validator_id,
            )
        _emit(value)
        return 0
    except AgentAuthoringError as exc:
        _emit(
            {
                "schema_version": "0.1",
                "ok": False,
                "command": args.command,
                "error": str(exc),
                "promotion_performed": False,
            },
            stream=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
