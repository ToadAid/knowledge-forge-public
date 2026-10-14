from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from knowledge_forge.provenance import build_compiler_profile
from knowledge_forge.repository import (
    RepositoryIngestError,
    build_repository_compile_request,
    ingest_repository,
    normalize_repository,
    validate_repository_manifest,
)

STAMP_A = datetime(2026, 9, 24, 11, 0, tzinfo=timezone.utc)
STAMP_B = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
APP_V1 = "def greet(name):\n    return f\"hello {name}\"\n"


def _git(root: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return cp.stdout.strip()


def _init_repo(root: Path) -> str:
    if shutil.which("git") is None:
        raise unittest.SkipTest("git unavailable")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "frog@example.test")
    _git(root, "config", "user.name", "Knowledge Frog")
    (root / "src").mkdir()
    (root / "assets").mkdir()
    (root / "src" / "app.py").write_text(APP_V1, encoding="utf-8")
    (root / "README.md").write_text("# Frog\n\nReference.\n", encoding="utf-8")
    (root / "assets" / "logo.png").write_bytes(b"\x89PNG\r\nfixture")
    _git(root, "add", ".")
    _git(root, "commit", "-q", "-m", "v1")
    return _git(root, "rev-parse", "HEAD")


def _profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


class RepositoryIngestTests(unittest.TestCase):
    def test_pinned_commit_ignores_dirty_and_untracked_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            head = _init_repo(root)
            (root / "src" / "app.py").write_text("DIRTY = True\n", encoding="utf-8")
            (root / "scratch.py").write_text("UNTRACKED = True\n", encoding="utf-8")
            manifest = ingest_repository(root, ingested_at=STAMP_A)
        self.assertEqual(manifest["repository_head"], head)
        self.assertEqual([m["relative_path"] for m in manifest["members"]], ["README.md", "src/app.py"])
        self.assertEqual(manifest["excluded"], [{"relative_path": "assets/logo.png", "reason": "unsupported_source_type"}])
        app = next(m for m in manifest["members"] if m["relative_path"] == "src/app.py")
        digest = sha256(APP_V1.encode("utf-8")).hexdigest()
        self.assertEqual(app["source_manifest"]["source_id"], f"sha256:{digest}")
        self.assertEqual(app["source_manifest"]["source_role"], "ORDINARY_KNOWLEDGE")

    def test_protected_canon_basename_refuses_repository_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "DOCTRINE.md").write_text("# Governing doctrine\n", encoding="utf-8")
            _git(root, "add", "DOCTRINE.md")
            _git(root, "commit", "-q", "-m", "add doctrine")
            with self.assertRaisesRegex(RepositoryIngestError, "CANON != SKILL"):
                ingest_repository(root, ingested_at=STAMP_A)

    def test_protected_canon_path_segment_refuses_repository_ingest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            (root / "constitution").mkdir()
            (root / "constitution" / "reference.md").write_text(
                "# Constitutional reference\n",
                encoding="utf-8",
            )
            _git(root, "add", "constitution/reference.md")
            _git(root, "commit", "-q", "-m", "add constitution")
            with self.assertRaisesRegex(RepositoryIngestError, "CANON != SKILL"):
                ingest_repository(root, ingested_at=STAMP_A)

    def test_manifest_source_role_tamper_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            manifest = ingest_repository(root, ingested_at=STAMP_A)
            manifest["members"][0]["source_manifest"]["source_role"] = "DOCTRINE"
            with self.assertRaisesRegex(RepositoryIngestError, "source_role"):
                validate_repository_manifest(manifest)

    def test_snapshot_id_ignores_ingest_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            a = ingest_repository(root, ingested_at=STAMP_A)
            b = ingest_repository(root, ingested_at=STAMP_B)
        self.assertEqual(a["repository_snapshot_id"], b["repository_snapshot_id"])
        self.assertNotEqual(a["ingested_at"], b["ingested_at"])

    def test_old_snapshot_normalizes_after_head_moves(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_head = _init_repo(root)
            manifest = ingest_repository(root, ingested_at=STAMP_A)
            (root / "src" / "app.py").write_text("def greet(name):\n    return 'v2'\n", encoding="utf-8")
            _git(root, "add", "src/app.py")
            _git(root, "commit", "-q", "-m", "v2")
            self.assertNotEqual(_git(root, "rev-parse", "HEAD"), old_head)
            normalized = normalize_repository(root, manifest)
        app = next(m for m in normalized["members"] if m["relative_path"] == "src/app.py")
        self.assertEqual(app["normalized_source"]["sections"][0]["text"], APP_V1.strip())
        self.assertEqual(app["normalized_source"]["sections"][0]["anchor"]["source_language"], "python")

    def test_compile_request_excludes_repo_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            normalized = normalize_repository(root, ingest_repository(root, ingested_at=STAMP_A))
            request = build_repository_compile_request(
                normalized,
                knowledge_id="topic:repo",
                skill_slug="repo",
                compiler_profile=_profile(),
            )
        self.assertEqual(request["authority"], "KNOWLEDGE_ONLY")
        self.assertNotIn("repository_head", request)
        self.assertNotIn("repository_snapshot_id", request)
        self.assertTrue(all("relative_path" not in source for source in request["sources"]))

    def test_manifest_tamper_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            manifest = ingest_repository(root, ingested_at=STAMP_A)
            manifest["excluded"][0]["reason"] = "symlink"
            with self.assertRaisesRegex(RepositoryIngestError, "repository_snapshot_id"):
                normalize_repository(root, manifest)

    def test_subdirectory_is_not_repository_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_repo(root)
            with self.assertRaisesRegex(RepositoryIngestError, "Git top-level"):
                ingest_repository(root / "src", ingested_at=STAMP_A)


if __name__ == "__main__":
    unittest.main()
