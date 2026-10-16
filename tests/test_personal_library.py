from __future__ import annotations

from contextlib import contextmanager, redirect_stderr, redirect_stdout
from hashlib import sha256
from io import StringIO
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import threading
from typing import Any
import unittest
from unittest import mock
from _platform_support import (
    skip_posix_file_modes_on_windows,
    skip_unless_symlinks,
)

import knowledge_forge.personal_library as personal_library
from knowledge_forge.agent_authoring import finalize_agent_draft
from knowledge_forge.cli import build_parser, main
from knowledge_forge.lifecycle import (
    COMPILED_CANDIDATE,
    PROMOTED,
    VALIDATED,
    create_lifecycle_record,
    transition_lifecycle,
)
from knowledge_forge.personal_library import (
    PERSONAL_LIBRARY_SCHEMA_VERSION,
    PERSONAL_KNOWLEDGE_MODE,
    PersonalLibraryError,
    add_personal_entry,
    evidence_sha256,
    load_personal_library,
    remove_personal_entry,
)
from knowledge_forge.retrieval import HARD_MAX_UNITS_PER_TURN
from knowledge_forge.validate import validate_skill_package

STAMP = "2026-10-16T06:00:00Z"


def claim_liquidity():
    return {
        "claim_key": "pool.liquidity.active_in_range",
        "subject": "pool.liquidity",
        "predicate": "active_in_range",
        "value": True,
        "qualifiers": {},
        "scope": {},
        "validity": {"as_of_source": "test-source"},
        "executable": False,
    }


def claim_depth():
    return {
        "claim_key": "pool.depth.deposit_bounded",
        "subject": "pool.depth",
        "predicate": "deposit_bounded",
        "value": True,
        "qualifiers": {},
        "scope": {},
        "validity": {"as_of_source": "test-source"},
        "executable": False,
    }


def claim_quorum():
    return {
        "claim_key": "governance.quorum.approval_count",
        "subject": "governance.quorum",
        "predicate": "approval_count",
        "value": 7,
        "qualifiers": {},
        "scope": {},
        "validity": {"as_of_source": "test-source"},
        "executable": False,
    }


def _source_for(skill: str) -> str:
    if skill in ("liquidity", "two-unit"):
        return (
            "# Concentrated Liquidity\n"
            "Liquidity is active only while the current price is inside "
            "the position range.\n\n"
            "# Other\n"
            "Unrelated material.\n"
        )
    return (
        "# Approval Quorum\n"
        "The approval quorum is exactly 7 members.\n\n"
        "# Tea Recipe\n"
        "Use 3 grams of tea per cup.\n"
    )


_SKILLS = {
    "liquidity": {
        "knowledge_id": "test:liquidity",
        "slug": "liquidity",
        "title": "Liquidity",
        "units": [
            {
                "title": "Liquidity is range bounded",
                "body": (
                    "Liquidity becomes inactive outside the selected "
                    "price range."
                ),
                "claim": claim_liquidity,
            },
        ],
    },
    "quorum": {
        "knowledge_id": "test:quorum",
        "slug": "quorum",
        "title": "Quorum",
        "units": [
            {
                "title": "Approval quorum is exact",
                "body": "Approval requires exactly seven reviewers.",
                "claim": claim_quorum,
            },
        ],
    },
    "two-unit": {
        "knowledge_id": "test:two-unit",
        "slug": "two-unit",
        "title": "Two Units",
        "units": [
            {
                "title": "Liquidity is range bounded",
                "body": (
                    "Liquidity becomes inactive outside the selected "
                    "price range."
                ),
                "claim": claim_liquidity,
            },
            {
                "title": "Deposits are depth bounded",
                "body": (
                    "Deposit depth stays bounded by the pool depth bound."
                ),
                "claim": claim_depth,
            },
        ],
    },
}


def _draft_text(source: Path, units: list[dict[str, Any]]) -> str:
    digest = sha256(source.read_bytes()).hexdigest()
    parts = [f"# SKILL: test\n# source_sha256: {digest}\n"]
    for unit in units:
        parts.append(
            f"\n# {unit['title']}\n\n{unit['body']}\n\n"
            "## Structured claim\n\n```json\n"
            + json.dumps(unit["claim"](), indent=2, sort_keys=True)
            + "\n```\n"
        )
    return "".join(parts)


def _finalize_skill(root: Path, skill: str) -> Path:
    """Build a real VALIDATED package end-to-end; return its output root."""

    spec = _SKILLS[skill]
    out = root / f"out-{skill}"
    if (out / "package").is_dir():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    source = root / f"source-{skill}.md"
    source.write_text(_source_for(skill), encoding="utf-8")
    draft = root / f"draft-{skill}.md"
    draft.write_text(_draft_text(source, spec["units"]), encoding="utf-8")
    plan = {
        "schema_version": "0.1",
        "judge_id": "test:model:faithfulness-v1",
        "units": [
            {
                "claim_key": unit["claim"]()["claim_key"],
                "section_ids": ["s001"],
                "entailment": "SUPPORTED",
                "qualifiers_preserved": True,
            }
            for unit in spec["units"]
        ],
    }
    result = finalize_agent_draft(
        source,
        draft,
        plan,
        out,
        knowledge_id=spec["knowledge_id"],
        skill_slug=spec["slug"],
        title=spec["title"],
        default_kind="DECISION_RULE",
        occurred_at=STAMP,
        validator_id="automated-validator:test",
    )
    if result["lifecycle_state"] != VALIDATED:  # pragma: no cover
        raise AssertionError("fixture did not validate")
    return out


def _run(argv):
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        rc = main(argv)
    out = json.loads(stdout.getvalue()) if stdout.getvalue() else None
    err = json.loads(stderr.getvalue()) if stderr.getvalue() else None
    return rc, out, err


@contextmanager
def _state_home(tmp: Path):
    with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp / "xdg")}):
        yield


def _default_library(tmp: Path) -> Path:
    return (
        tmp / "xdg" / "knowledge-forge" / "personal"
        / "personal-library.json"
    )


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _consistent_tamper(package_root: Path) -> None:
    """Flip a unit byte and repair SOURCE_RECEIPT hashes so the package
    still validates — only receipt recompute against the lifecycle can
    catch this tamper."""

    root = Path(package_root)
    manifest = json.loads(
        (root / "manifest.json").read_text(encoding="utf-8")
    )
    unit_path = manifest["units"][0]["path"]
    (root / unit_path).write_text(
        (root / unit_path).read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )
    receipt = json.loads(
        (root / "SOURCE_RECEIPT.json").read_text(encoding="utf-8")
    )
    receipt["artifact_hashes"][unit_path] = (
        "sha256:" + sha256((root / unit_path).read_bytes()).hexdigest()
    )
    _write_json(root / "SOURCE_RECEIPT.json", receipt)
    files = {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in root.rglob("*")
        if p.is_file()
    }
    if not validate_skill_package(files)["valid"]:  # pragma: no cover
        raise AssertionError("tamper helper broke package validation")


def _file_hashes(root: Path) -> dict:
    return {
        p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file()
    }


class PersonalLibraryTests(unittest.TestCase):
    def _library(self, tmp: Path) -> Path:
        return tmp / "library.json"

    def _add(self, tmp: Path, skill: str, *, accept: bool = True) -> tuple:
        out = _finalize_skill(tmp, skill)
        argv = [
            "personal", "add",
            str(out / "package"),
            "--validated-lifecycle", str(out / "lifecycle.validated.json"),
            "--library", str(self._library(tmp)),
            "--added-at", STAMP,
        ]
        if accept:
            argv.append("--accept")
        return _run(argv)

    def _lookup(self, tmp: Path, question: str, *extra: str) -> tuple:
        return _run([
            "personal", "lookup", question,
            "--library", str(self._library(tmp)), *extra,
        ])

    def test_personal_add_list_lookup_remove_happy_path_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, err = self._add(root, "liquidity")
            self.assertEqual((rc, err), (0, None))
            skill_id = out["skill_id"]
            self.assertTrue(
                skill_id.startswith("knowledge-forge:test:liquidity@")
            )
            self.assertTrue(out["acceptance"]["accepted"])
            self.assertFalse(out["acceptance"]["authenticated_human"])
            self.assertEqual(
                out["acceptance"]["acceptance_channel"],
                "CONVERSATIONAL_ACCEPTANCE_FLAG_NOT_AUTHENTICATED_HUMAN",
            )
            self.assertFalse(out["promotion_performed"])
            self.assertEqual(out["tool_grants"], [])
            library = self._library(root)
            self.assertTrue(library.is_file())

            rc, out, err = _run([
                "personal", "list", "--library", str(library),
            ])
            self.assertEqual((rc, err), (0, None))
            self.assertEqual(out["entry_count"], 1)
            entry = out["entries"][0]
            stored = json.loads(library.read_text(encoding="utf-8"))
            self.assertEqual(
                entry["package_receipt_sha256"],
                stored["entries"][skill_id]["package_receipt_sha256"],
            )
            self.assertEqual(entry["lifecycle_state"], "VALIDATED")

            rc, out, err = self._lookup(
                root, "What does the book say about liquidity and the range?"
            )
            self.assertEqual((rc, err), (0, None))
            self.assertEqual(out["selected_skill_count"], 1)
            self.assertEqual(out["loaded_unit_count"], 1)
            unit = out["reasoning_context"][0]
            self.assertEqual(unit["trust_class"], "UNTRUSTED_KNOWLEDGE_DATA")
            self.assertEqual(unit["instruction_authority"], "NONE")
            self.assertEqual(unit["execution_authority"], "NONE")
            self.assertFalse(unit["executable"])
            self.assertEqual(unit["knowledge_mode"], PERSONAL_KNOWLEDGE_MODE)
            self.assertFalse(unit["promotion_performed"])
            self.assertIn(
                "inactive outside the selected price range", unit["content"]
            )
            self.assertTrue(unit["source_bindings"])
            self.assertEqual(out["tool_grants"], [])
            self.assertEqual(
                out["tool_policy_rule"], "PERSONAL_MODE_GRANTS_NO_TOOLS"
            )
            self.assertNotIn("effective_tools", out)
            self.assertTrue(out["personal_library_mode"])
            self.assertEqual(out["selected_skills"][0]["skill_id"], skill_id)

            rc, out, err = _run([
                "personal", "remove", skill_id,
                "--library", str(library),
            ])
            self.assertEqual((rc, err), (0, None))
            self.assertTrue(out["removed"])
            self.assertTrue(out["package_files_untouched"])
            self.assertEqual(out["remaining_count"], 0)

            rc, out, err = _run([
                "personal", "list", "--library", str(library),
            ])
            self.assertEqual(out["entry_count"], 0)

    def test_personal_add_refuses_missing_accept_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, err = self._add(root, "liquidity", accept=False)
            self.assertEqual(rc, 2)
            self.assertIsNone(out)
            self.assertFalse(err["ok"])
            self.assertIn("--accept", err["error"])
            self.assertFalse(self._library(root).exists())

    def test_personal_add_refuses_invalid_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            manifest = json.loads(
                (out_root / "package" / "manifest.json").read_text()
            )
            (out_root / "package" / manifest["units"][0]["path"]).unlink()
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle",
                str(out_root / "lifecycle.validated.json"),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertFalse(err["ok"])
            self.assertIn("validation failed", err["error"])
            self.assertFalse(self._library(root).exists())

    def test_personal_add_refuses_missing_lifecycle_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle", str(root / "missing.json"),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("validated lifecycle", err["error"])

    def test_personal_add_refuses_directory_lifecycle_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            (root / "lifedir").mkdir()
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle", str(root / "lifedir"),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("validated lifecycle", err["error"])

    @skip_unless_symlinks
    def test_personal_add_refuses_symlinked_lifecycle_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            link = root / "link.json"
            link.symlink_to(out_root / "lifecycle.validated.json")
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle", str(link),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("symlink", err["error"])

    def test_personal_add_refuses_compiled_candidate_lifecycle_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            package = out_root / "package"
            manifest = json.loads(
                (package / "manifest.json").read_text(encoding="utf-8")
            )
            receipt = json.loads(
                (package / "SOURCE_RECEIPT.json").read_text(encoding="utf-8")
            )
            genesis = create_lifecycle_record(
                manifest, receipt, created_at=STAMP
            )
            self.assertEqual(genesis["state"], COMPILED_CANDIDATE)
            lifecycle_path = root / "genesis.json"
            _write_json(lifecycle_path, genesis)
            rc, out, err = _run([
                "personal", "add", str(package),
                "--validated-lifecycle", str(lifecycle_path),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("VALIDATED", err["error"])

    def test_personal_add_refuses_promoted_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            record = json.loads(
                (out_root / "lifecycle.validated.json").read_text(
                    encoding="utf-8"
                )
            )
            promoted = transition_lifecycle(
                record,
                PROMOTED,
                occurred_at=STAMP,
                actor="operator:test",
                evidence={"decision_ref": "decision:test:personal"},
            )
            self.assertEqual(promoted["state"], PROMOTED)
            lifecycle_path = root / "promoted.json"
            _write_json(lifecycle_path, promoted)
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle", str(lifecycle_path),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("VALIDATED", err["error"])

    def test_personal_add_refuses_duplicate_skill_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _, _ = self._add(root, "liquidity")
            self.assertEqual(rc, 0)
            library = self._library(root)
            before = library.read_text(encoding="utf-8")
            rc, out, err = self._add(root, "liquidity")
            self.assertEqual(rc, 2)
            self.assertIn("already in the personal library", err["error"])
            self.assertIn("remove it before re-adding", err["error"])
            self.assertEqual(before, library.read_text(encoding="utf-8"))

    def test_personal_add_refuses_lifecycle_from_different_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            quorum = _finalize_skill(root, "quorum")
            rc, out, err = _run([
                "personal", "add", str(liquidity / "package"),
                "--validated-lifecycle",
                str(quorum / "lifecycle.validated.json"),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("package source receipt", err["error"])

    def test_personal_add_refuses_consistent_tamper_via_receipt_recompute(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            package = root / "copy-package"
            shutil.copytree(out_root / "package", package)
            _consistent_tamper(package)
            rc, out, err = _run([
                "personal", "add", str(package),
                "--validated-lifecycle",
                str(out_root / "lifecycle.validated.json"),
                "--library", str(self._library(root)),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("package source receipt", err["error"])

    @skip_posix_file_modes_on_windows
    def test_personal_library_write_creates_dir_0700_and_file_0600_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, _, _ = self._add(root, "liquidity")
            self.assertEqual(rc, 0)
            library = self._library(root)
            self.assertEqual(
                stat.S_IMODE(library.parent.stat().st_mode), 0o700
            )
            self.assertEqual(stat.S_IMODE(library.stat().st_mode), 0o600)

    def test_personal_list_shows_digests_and_stamps_without_claim_leak(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, out, err = self._add(root, "liquidity")
            self.assertEqual((rc, err), (0, None))
            skill_id = out["skill_id"]
            rc, out, err = _run([
                "personal", "list", "--library", str(self._library(root)),
            ])
            self.assertEqual((rc, err), (0, None))
            encoded = json.dumps(out)
            self.assertNotIn("inactive outside the selected price", encoded)
            self.assertNotIn("active only while the current price", encoded)
            entry = out["entries"][0]
            self.assertTrue(
                entry["package_receipt_sha256"].startswith("sha256:")
            )
            self.assertTrue(
                entry["lifecycle_head_sha256"].startswith("sha256:")
            )
            self.assertEqual(out["authority"], "KNOWLEDGE_ONLY")
            self.assertEqual(out["execution_authority"], "NONE")
            self.assertEqual(out["knowledge_mode"], PERSONAL_KNOWLEDGE_MODE)
            self.assertFalse(out["promotion_performed"])
            library_json = json.loads(
                self._library(root).read_text(encoding="utf-8")
            )
            self.assertIn(
                "lifecycle_record",
                library_json["entries"][skill_id],
            )

    def test_personal_list_refuses_missing_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = _run([
                "personal", "list",
                "--library", str(Path(tmp) / "missing.json"),
            ])
            self.assertEqual(rc, 2)
            self.assertIn("does not exist", err["error"])

    def test_personal_lookup_returns_unit_with_stamps_and_citations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            rc, out, err = self._lookup(
                root, "What does the book say about liquidity and the range?"
            )
            self.assertEqual((rc, err), (0, None))
            unit = out["reasoning_context"][0]
            for field in (
                "trust_class", "authority", "instruction_authority",
                "execution_authority", "executable", "knowledge_mode",
                "promotion_performed", "skill_id", "skill_version",
                "knowledge_id", "unit_id", "claim_key", "kind",
                "artifact_path", "claim_sha256", "source_bindings",
                "content",
            ):
                self.assertIn(field, unit)
            self.assertEqual(
                unit["claim_key"], "pool.liquidity.active_in_range"
            )
            self.assertTrue(unit["claim_sha256"].startswith("sha256:"))
            self.assertTrue(unit["artifact_path"].startswith("units/"))
            self.assertEqual(
                out["question_sha256"],
                "sha256:"
                + sha256(out["question"].encode("utf-8")).hexdigest(),
            )
            self.assertEqual(out["entry_errors"], [])

    def test_personal_lookup_no_match_refuses_with_exit_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            rc, out, err = self._lookup(root, "unrelated zigzag query")
            self.assertEqual(rc, 2)
            self.assertIsNone(out)
            self.assertIn("matched the question", err["error"])

    def test_personal_lookup_tampered_entry_skipped_good_entry_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, out, _ = self._add(root, "liquidity")
            liquidity_id = out["skill_id"]
            _, out, _ = self._add(root, "quorum")
            quorum_id = out["skill_id"]
            _consistent_tamper(root / "out-liquidity" / "package")
            rc, out, err = self._lookup(
                root, "liquidity range and approval quorum"
            )
            self.assertEqual((rc, err), (0, None))
            self.assertEqual(len(out["entry_errors"]), 1)
            self.assertEqual(
                out["entry_errors"][0]["skill_id"], liquidity_id
            )
            self.assertIn("receipt", out["entry_errors"][0]["error"])
            self.assertEqual(
                out["selected_skills"][0]["skill_id"], quorum_id
            )
            selected_ids = [
                item["skill_id"] for item in out["selected_skills"]
            ]
            self.assertNotIn(liquidity_id, selected_ids)

    def test_personal_lookup_refuses_library_lifecycle_copy_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, out, _ = self._add(root, "liquidity")
            library_path = self._library(root)
            library = json.loads(library_path.read_text(encoding="utf-8"))
            library["entries"][out["skill_id"]]["lifecycle_record"][
                "state"
            ] = "PROMOTED"
            _write_json(library_path, library)
            rc, out, err = self._lookup(root, "liquidity and range")
            self.assertEqual(out, None)
            self.assertEqual(rc, 2)
            self.assertIn("validation failed", err["error"])

    def test_personal_lookup_refuses_all_entries_tampered(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            self._add(root, "quorum")
            _consistent_tamper(root / "out-liquidity" / "package")
            _consistent_tamper(root / "out-quorum" / "package")
            rc, out, err = self._lookup(
                root, "liquidity range and approval quorum"
            )
            self.assertEqual(rc, 2)
            self.assertIsNone(out)
            self.assertIn("could be revalidated", err["error"])

    def test_personal_lookup_selects_at_most_three_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            self._add(root, "quorum")
            self._add(root, "two-unit")
            rc, out, err = self._lookup(
                root, "liquidity range and approval quorum and pool depth",
                "--max-skills", "3",
            )
            self.assertEqual((rc, err), (0, None))
            self.assertLessEqual(out["selected_skill_count"], 3)
            self.assertLessEqual(
                out["loaded_unit_count"], HARD_MAX_UNITS_PER_TURN
            )

    def test_personal_lookup_parser_bounds_max_skills_and_max_units(self):
        for args in (
            ["personal", "lookup", "q", "--max-skills", "4"],
            ["personal", "lookup", "q", "--max-units", "0"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                main(args)
            self.assertEqual(ctx.exception.code, 2)

    def test_personal_lookup_unit_budget_hard_max_three_across_skills(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            self._add(root, "two-unit")
            rc, out, err = self._lookup(
                root, "liquidity range and pool depth deposit",
                "--max-skills", "2", "--max-units", "3",
            )
            self.assertEqual((rc, err), (0, None))
            self.assertLessEqual(
                len(out["reasoning_context"]), HARD_MAX_UNITS_PER_TURN
            )
            rc, out, err = self._lookup(
                root, "liquidity range and pool depth deposit",
                "--max-skills", "2", "--max-units", "1",
            )
            self.assertEqual((rc, err), (0, None))
            self.assertLessEqual(len(out["reasoning_context"]), 2)

    def test_personal_remove_refuses_unknown_skill_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            rc, out, err = _run([
                "personal", "remove", "test:missing",
                "--library", str(self._library(root)),
            ])
            self.assertEqual(rc, 2)
            self.assertIn("not in the personal library", err["error"])

    def test_personal_remove_leaves_package_files_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = root / "out-liquidity"
            self._add(root, "liquidity")
            rc, out, _ = self._add(root, "quorum")
            self.assertEqual(rc, 0)
            quorum_id = out["skill_id"]
            before = _file_hashes(out_root)
            rc, _, _ = _run([
                "personal", "remove", quorum_id,
                "--library", str(self._library(root)),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(before, _file_hashes(out_root))

    def test_promote_absent_from_read_only_cli_parser(self):
        parser = build_parser()
        top = parser._subparsers._group_actions[0]
        self.assertIn("personal", top.choices)
        self.assertNotIn("promote", top.choices)
        personal = top.choices["personal"]
        sub = personal._subparsers._group_actions[0].choices
        self.assertEqual(sorted(sub), ["add", "list", "lookup", "remove"])

    def test_personal_state_wholly_separate_from_registry_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with _state_home(root):
                out_root = _finalize_skill(root / "work", "liquidity")
                rc, out, err = _run([
                    "personal", "add", str(out_root / "package"),
                    "--validated-lifecycle",
                    str(out_root / "lifecycle.validated.json"),
                    "--added-at", STAMP, "--accept",
                ])
                self.assertEqual((rc, err), (0, None))
                skill_id = out["skill_id"]
                default_path = _default_library(root)
                self.assertTrue(default_path.is_file())
                self.assertEqual(out["library"], str(default_path))
                rc, out, err = _run(["personal", "list"])
                self.assertEqual((rc, err), (0, None))
                self.assertEqual(out["entries"][0]["skill_id"], skill_id)
            self.assertEqual(
                [p.name for p in root.rglob("*") if p.name == "registry.json"],
                [],
            )

    def test_personal_library_serialization_is_canonical_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._add(root, "liquidity")
            self._add(root, "quorum")
            library_path = self._library(root)
            payload = library_path.read_bytes()
            value = json.loads(payload.decode("utf-8"))
            reserialized = (
                json.dumps(
                    value, sort_keys=True, indent=2, ensure_ascii=False
                )
                + "\n"
            ).encode("utf-8")
            self.assertEqual(payload, reserialized)
            expected_digest = evidence_sha256(dict(value["entries"]))
            self.assertEqual(value["entries_sha256"], expected_digest)

    @skip_unless_symlinks
    def test_personal_add_refuses_symlinked_library_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            target = root / "real.json"
            target.write_text("{}", encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle",
                str(out_root / "lifecycle.validated.json"),
                "--library", str(link),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertIn("symlink", err["error"])

    def test_personal_add_refuses_non_regular_library_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            directory = root / "dir.json"
            directory.mkdir()
            rc, out, err = _run([
                "personal", "add", str(out_root / "package"),
                "--validated-lifecycle",
                str(out_root / "lifecycle.validated.json"),
                "--library", str(directory),
                "--added-at", STAMP, "--accept",
            ])
            self.assertEqual(rc, 2)
            self.assertFalse(err["ok"])

class PersonalLibraryReliabilityTests(unittest.TestCase):
    """Review repairs: single-writer library coordination (P1) and bounded
    package/JSON reading (P2). Governed surfaces are not touched here."""

    def _library(self, tmp: Path) -> Path:
        return Path(tmp) / "library.json"

    def _worker_add(
        self,
        out_root: Path,
        library_path: Path,
        results: dict,
        errors: dict,
    ) -> None:
        try:
            results[id(out_root)] = add_personal_entry(
                out_root / "package",
                out_root / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
        except PersonalLibraryError as exc:
            errors[id(out_root)] = str(exc)

    def _run_two(self, target_a, target_b):
        threads = [
            threading.Thread(target=target_a, name="writer-a"),
            threading.Thread(target=target_b, name="writer-b"),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def test_two_concurrent_adds_preserve_both_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            quorum = _finalize_skill(root, "quorum")
            library_path = self._library(root)
            results: dict = {}
            errors: dict = {}
            self._run_two(
                lambda: self._worker_add(liquidity, library_path, results, errors),
                lambda: self._worker_add(quorum, library_path, results, errors),
            )
            self.assertEqual(errors, {})
            self.assertEqual(len(results), 2)
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 2)
            for payload in results.values():
                self.assertIn(payload["skill_id"], library["entries"])
            expected_digest = evidence_sha256(dict(library["entries"]))
            self.assertEqual(library["entries_sha256"], expected_digest)

    def test_concurrent_duplicate_add_exactly_one_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            library_path = self._library(root)
            results: dict = {}
            errors: dict = {}
            self._run_two(
                lambda: self._worker_add(liquidity, library_path, results, errors),
                lambda: self._worker_add(liquidity, library_path, results, errors),
            )
            self.assertEqual(len(results) + len(errors), 2)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("already in the personal library", next(iter(errors.values())))
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 1)

    def test_concurrent_add_and_remove_race_preserves_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            quorum = _finalize_skill(root, "quorum")
            two_unit = _finalize_skill(root, "two-unit")
            library_path = self._library(root)
            add_personal_entry(
                liquidity / "package",
                liquidity / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
            add_personal_entry(
                quorum / "package",
                quorum / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
            results: dict = {}
            errors: dict = {}
            self._run_two(
                lambda: self._worker_add(two_unit, library_path, results, errors),
                lambda: self._worker(
                    lambda: remove_personal_entry(
                        "knowledge-forge:test:quorum@sha256"
                        ":64d250a8b04c35396e2f0ba098ff034333347211d4f136348a1777eae0248cb5",
                        library_path=library_path,
                    ),
                    results,
                    errors,
                ),
            )
            self.assertEqual(errors, {})
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 2)
            self.assertFalse(
                any("quorum" in skill_id for skill_id in library["entries"])
            )
            self.assertTrue(
                any(item["lifecycle_state"] == VALIDATED
                    for item in library["entries"].values())
            )

    def _worker(self, operation, results, errors):
        try:
            results.setdefault("__ops__", []).append(operation())
        except PersonalLibraryError as exc:
            errors.setdefault("__ops__", []).append(str(exc))
            raise AssertionError("remove race must not fail") from exc

    def test_concurrent_failed_add_leaves_library_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            broken = _finalize_skill(root, "quorum")
            manifest = json.loads(
                (broken / "package" / "manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            (broken / "package" / manifest["units"][0]["path"]).unlink()
            library_path = self._library(root)
            results: dict = {}
            errors: dict = {}
            self._run_two(
                lambda: self._worker_add(liquidity, library_path, results, errors),
                self._worker_add(broken, library_path, results, errors),
            )
            self.assertEqual(len(results), 1)
            self.assertEqual(len(errors), 1)
            self.assertIn("validation failed", next(iter(errors.values())))
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 1)
            expected_digest = evidence_sha256(dict(library["entries"]))
            self.assertEqual(library["entries_sha256"], expected_digest)

    def test_write_lock_contention_refuses_without_modifying_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            library_path = self._library(root)
            with mock.patch.object(
                personal_library, "_PERSONAL_LOCK_TIMEOUT_SECONDS", 0.0
            ):
                lock_path = library_path.parent / "personal-library.lock"
                held = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    personal_library._acquire_write_lock(held)
                    with self.assertRaisesRegex(
                        PersonalLibraryError, "another personal library writer"
                    ):
                        add_personal_entry(
                            liquidity / "package",
                            liquidity / "lifecycle.validated.json",
                            library_path=library_path,
                            accept=True,
                            added_at=STAMP,
                        )
                finally:
                    # Windows byte-range locks survive a plain close of the
                    # handle; release through the same production path the
                    # lock holder itself would use.
                    personal_library._release_write_lock(held)
                    os.close(held)
            # Releasing the competing writer lets the operation succeed.
            add_personal_entry(
                liquidity / "package",
                liquidity / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 1)

    # --- P2: bounded package and JSON reading ---

    def test_snapshot_refuses_oversized_artifact_before_reading(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            with mock.patch.object(
                personal_library, "MAX_PACKAGE_BYTES", 64
            ):
                with self.assertRaisesRegex(PersonalLibraryError, "byte limit"):
                    personal_library._snapshot_package(out_root / "package")

    def test_snapshot_refuses_too_many_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            with mock.patch.object(personal_library, "MAX_PACKAGE_FILES", 2):
                with self.assertRaisesRegex(PersonalLibraryError, "file limit"):
                    personal_library._snapshot_package(out_root / "package")

    def test_snapshot_refuses_excessive_directory_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            with mock.patch.object(
                personal_library, "_PERSONAL_SCAN_ENTRY_LIMIT", 2
            ):
                with self.assertRaisesRegex(
                    PersonalLibraryError, "directory entry scan limit"
                ):
                    personal_library._snapshot_package(out_root / "package")

    @skip_unless_symlinks
    def test_snapshot_refuses_symlink_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            package = out_root / "package"
            (package / "linked.json").symlink_to(package / "manifest.json")
            with self.assertRaisesRegex(
                PersonalLibraryError, "must not contain symlinks"
            ):
                personal_library._snapshot_package(package)

    def test_snapshot_refuses_artifact_changed_during_inspection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            original = os.read

            def inflated(descriptor, count):
                return original(descriptor, count) + b"tampered\n"

            with mock.patch.object(personal_library.os, "read", inflated):
                with self.assertRaisesRegex(
                    PersonalLibraryError,
                    "changed while being",
                ):
                    personal_library._snapshot_package(out_root / "package")

    def test_bounded_reader_caps_growth_after_initial_file_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate"
            path.write_bytes(b"abc")
            requests = []

            def simulated_growth(descriptor, count):
                requests.append(count)
                return b"abcde"  # up to limit + 1, never unlimited data

            with mock.patch.object(personal_library.os, "read", simulated_growth):
                with self.assertRaisesRegex(
                    PersonalLibraryError, "exceeds 4 byte limit"
                ):
                    personal_library._read_bounded_bytes(
                        path, 4, "candidate"
                    )
            self.assertEqual(requests, [5])

    def test_add_refuses_oversized_lifecycle_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out_root = _finalize_skill(root, "liquidity")
            with mock.patch.object(
                personal_library, "_MAX_LIFECYCLE_RECORD_BYTES", 8
            ):
                with self.assertRaisesRegex(PersonalLibraryError, "exceeds"):
                    add_personal_entry(
                        out_root / "package",
                        out_root / "lifecycle.validated.json",
                        library_path=self._library(root),
                        accept=True,
                        added_at=STAMP,
                    )
            self.assertFalse(self._library(root).exists())

    def test_add_refuses_oversized_library_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            library_path = self._library(root)
            add_personal_entry(
                liquidity / "package",
                liquidity / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
            quorum = _finalize_skill(root, "quorum")
            with mock.patch.object(personal_library, "_MAX_LIBRARY_BYTES", 256):
                with self.assertRaisesRegex(PersonalLibraryError, "exceeds"):
                    add_personal_entry(
                        quorum / "package",
                        quorum / "lifecycle.validated.json",
                        library_path=library_path,
                        accept=True,
                        added_at=STAMP,
                    )
            library = load_personal_library(library_path)
            self.assertEqual(len(library["entries"]), 1)

    def test_governed_registry_state_untouched_by_concurrency_repair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            liquidity = _finalize_skill(root, "liquidity")
            library_path = self._library(root)
            add_personal_entry(
                liquidity / "package",
                liquidity / "lifecycle.validated.json",
                library_path=library_path,
                accept=True,
                added_at=STAMP,
            )
            remove_personal_entry(
                "knowledge-forge:test:liquidity@sha256"
                ":64d250a8b04c35396e2f0ba098ff034333347211d4f136348a1777eae0248cb5",
                library_path=library_path,
            )
            self.assertEqual(
                [p.name for p in root.rglob("registry.json")],
                [],
            )
