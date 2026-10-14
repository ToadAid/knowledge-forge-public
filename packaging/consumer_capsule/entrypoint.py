"""Consumer-only entry point for the deterministic Knowledge Forge capsule."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Sequence

from knowledge_forge.skill_index import build_skill_index


COMMAND = "index-build"
REGISTRY_DESCRIPTOR_PATH = "/proc/self/fd/4"


def _emit_error(message: str) -> None:
    sys.stderr.write(
        json.dumps(
            {
                "ok": False,
                "error": message,
                "authority": "KNOWLEDGE_ONLY",
                "execution_authority": "NONE",
                "tool_grants": [],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != [COMMAND, REGISTRY_DESCRIPTOR_PATH]:
        _emit_error("only fixed descriptor-bound index-build is supported")
        return 2

    try:
        value = json.loads(Path(REGISTRY_DESCRIPTOR_PATH).read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("registry JSON must be an object")
        index = build_skill_index(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        _emit_error(str(exc))
        return 2

    sys.stdout.write(
        json.dumps(index, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
