from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from _platform_support import skip_unless_symlinks

from knowledge_forge.cli import build_parser as build_read_only_parser
from knowledge_forge.operator_cli import (
    OperatorCliError,
    _snapshot_package,
    build_parser as build_operator_parser,
)


def _subcommands(parser):
    action = next(
        action
        for action in parser._actions
        if getattr(action, "choices", None)
    )
    return set(action.choices)


class OperatorCliTests(unittest.TestCase):
    def test_privileged_cli_exposes_promote_but_normal_cli_does_not(self):
        self.assertIn("promote", _subcommands(build_operator_parser()))
        self.assertNotIn("promote", _subcommands(build_read_only_parser()))

    @skip_unless_symlinks
    def test_snapshot_refuses_symlink_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.json"
            target.write_text("{}", encoding="utf-8")
            (root / "linked.json").symlink_to(target)
            with self.assertRaisesRegex(OperatorCliError, "symlink"):
                _snapshot_package(str(root))

    def test_snapshot_is_bounded_and_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "manifest.json").write_text("{}", encoding="utf-8")
            nested = root / "units"
            nested.mkdir()
            (nested / "u.md").write_text("unit", encoding="utf-8")
            files = _snapshot_package(str(root))
            self.assertEqual(
                sorted(files),
                ["manifest.json", "units/u.md"],
            )


if __name__ == "__main__":
    unittest.main()
