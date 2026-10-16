"""P3A: validated candidate preview must remain read-only/non-authorizing."""

from __future__ import annotations

from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from _platform_support import skip_unless_symlinks

from knowledge_forge.approval import ApprovalContractError
from knowledge_forge.approval_tui import main
from knowledge_forge.candidate_preview import preview_validated_candidate
from tests.test_trust_wall import _governed_package

CLOCK = "2026-10-14T05:20:00Z"


def fixture():
    sandbox = tempfile.TemporaryDirectory()
    root = Path(sandbox.name)
    skill_root, _registry, manifest, promoted = _governed_package(root)
    validated = deepcopy(promoted)
    validated["events"] = validated["events"][:-1]
    validated["state"] = "VALIDATED"
    files = {p.relative_to(skill_root).as_posix(): p.read_bytes()
             for p in skill_root.rglob("*") if p.is_file()}
    lifecycle_path = root / "lifecycle.validated.json"
    lifecycle_path.write_text(json.dumps(validated), encoding="utf-8")
    return sandbox, skill_root, lifecycle_path, files, validated, manifest


def run_cli(*args):
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        result = main(list(args))
    return result, json.loads(out.getvalue()) if out.getvalue() else None, (
        json.loads(err.getvalue()) if err.getvalue() else None
    )


class CandidatePreviewTests(unittest.TestCase):
    def test_existing_contract_accepts_real_forge_sha256_identity(self):
        from knowledge_forge.approval import (
            build_approval_evidence, build_approval_request,
            build_current_candidate_binding, require_current_candidate,
            project_approval_request,
        )
        sid = "knowledge-forge:tobyworld:toadgod-lore@sha256:" + "a" * 64
        evidence = build_approval_evidence(
            request_id="review.real.skill.0001", target_skill_id=sid,
            target_skill_version=1, package_receipt_sha256="sha256:" + "b" * 64,
            validated_lifecycle_head_sha256="sha256:" + "c" * 64,
            items=[{"label":"manifest", "sha256":"sha256:" + "d" * 64}],
        )
        request = build_approval_request(
            request_id=evidence["request_id"], requesting_agent="Test reader",
            requesting_principal="agent-test-session", target_skill_id=sid,
            target_skill_version=1,
            package_receipt_sha256=evidence["package_receipt_sha256"],
            validated_lifecycle_head_sha256=evidence["validated_lifecycle_head_sha256"],
            requested_at=CLOCK, expires_at="2026-10-14T05:50:00Z",
            evidence_digest=evidence["evidence_digest"],
        )
        binding = build_current_candidate_binding(request, observed_at=CLOCK)
        require_current_candidate(request, binding)
        proj = project_approval_request(request, evidence,
                                        surface="TERMINAL_TUI", as_of=CLOCK)
        self.assertEqual(proj["target_skill_id"], sid)
        self.assertFalse(proj["authorization_granted"])
        self.assertFalse(proj["promotion_authorized"])
        self.assertTrue(proj["review_only"])

    def test_malformed_real_forge_digest_refused(self):
        from knowledge_forge.approval import build_approval_evidence
        with self.assertRaisesRegex(ApprovalContractError, "canonical Forge skill identity"):
            build_approval_evidence(
                request_id="review.real.skill.0002",
                target_skill_id="knowledge-forge:tobyworld:lore@sha256:" + "G" * 64,
                target_skill_version=1,
                package_receipt_sha256="sha256:" + "b" * 64,
                validated_lifecycle_head_sha256="sha256:" + "c" * 64,
                items=[{"label":"manifest", "sha256":"sha256:" + "d" * 64}],
            )

    def test_source_corpus_coverage_is_not_assumed(self):
        sandbox, _root, _life, files, validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        value = preview_validated_candidate(files, validated, observed_at=CLOCK)
        self.assertEqual(value["source_count"], 1)
        self.assertEqual(value["unit_count"], 3)
        self.assertEqual(
            value["source_corpus_coverage"],
            "UNKNOWN_FROM_PACKAGE_DO_NOT_CLAIM_COMPLETE_CORPUS",
        )
        self.assertIn("do not prove full source-corpus coverage", value["warning"])
        self.assertFalse(value["review_request_written"])
        self.assertFalse(value["operator_cli_invoked"])

    def test_preview_exact_validated_candidate_is_non_authorizing(self):
        sandbox, _root, _life, files, validated, manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        result = preview_validated_candidate(files, validated, observed_at=CLOCK)
        self.assertEqual(result["skill_id"], manifest["skill_id"])
        self.assertEqual(result["unit_count"], 3)
        self.assertEqual(result["lifecycle_state"], "VALIDATED")
        self.assertEqual(result["artifact_count"], len(files))
        self.assertFalse(result["promotion_authorized"])
        self.assertFalse(result["operator_cli_invoked"])
        self.assertFalse(result["review_receipt_written"])
        self.assertFalse(result["production_registry_mutated"])
        project = result["review_contract_projection_demo_only"]
        self.assertTrue(project["review_only"])
        self.assertFalse(project["authorization_granted"])
        self.assertFalse(project["promotion_authorized"])
        self.assertEqual(project["evidence_items"][0]["label"], "claim-index")
        rendered = json.dumps(result)
        self.assertNotIn("The approval quorum is exactly 7", rendered)
        self.assertNotIn("wallet_send", rendered)

    def test_malformed_canonical_skill_id_refused(self):
        from knowledge_forge.approval import build_approval_evidence
        with self.assertRaisesRegex(ApprovalContractError, "canonical Forge skill identity"):
            build_approval_evidence(
                request_id="review.example.0001",
                target_skill_id="knowledge-forge:tobyworld:lore@sha256:INVALID",
                target_skill_version=1,
                package_receipt_sha256="sha256:" + "a" * 64,
                validated_lifecycle_head_sha256="sha256:" + "b" * 64,
                items=[{"label": "manifest", "sha256": "sha256:" + "c" * 64}],
            )

    def test_cli_text_output_is_human_readable_and_non_authorizing(self):
        sandbox, root, life, _files, _validated, manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["preview-validated", "--package-root", str(root),
                       "--validated-lifecycle", str(life)])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertIn("READ ONLY", out.getvalue())
        self.assertIn(manifest["skill_id"], out.getvalue())
        self.assertIn("NOT authenticated, NOT authorized, NOT promoted", out.getvalue())
        self.assertNotIn("The approval quorum is exactly 7", out.getvalue())

    def test_tampered_artifact_refused(self):
        sandbox, _root, _life, files, validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        broken = dict(files)
        broken["manifest.json"] = files["manifest.json"] + b" "
        with self.assertRaisesRegex(ApprovalContractError, "package validation"):
            preview_validated_candidate(broken, validated, observed_at=CLOCK)

    def test_mismatched_lifecycle_refused(self):
        sandbox, _root, _life, files, validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        broken = deepcopy(validated)
        broken["skill_id"] = "skill.fake.other"
        with self.assertRaisesRegex(ApprovalContractError, "lifecycle validation"):
            preview_validated_candidate(files, broken, observed_at=CLOCK)

    def test_promoted_lifecycle_not_accepted(self):
        sandbox, _root, _life, files, validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        broken = deepcopy(validated)
        broken["state"] = "PROMOTED"
        with self.assertRaisesRegex(ApprovalContractError, "lifecycle"):
            preview_validated_candidate(files, broken, observed_at=CLOCK)

    def test_invalid_clock_refused(self):
        sandbox, _root, _life, files, validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        with self.assertRaisesRegex(ApprovalContractError, "timezone"):
            preview_validated_candidate(files, validated, observed_at="2026-10-14T05:20:00")

    def test_cli_returns_read_only_projection_without_mutations(self):
        sandbox, root, life, _files, _validated, manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        before = sorted(p.relative_to(Path(sandbox.name)).as_posix()
                        for p in Path(sandbox.name).rglob("*"))
        rc, out, err = run_cli("preview-validated", "--format", "json", "--package-root", str(root),
                               "--validated-lifecycle", str(life))
        self.assertEqual(rc, 0, err)
        self.assertIsNone(err)
        self.assertEqual(out["skill_id"], manifest["skill_id"])
        self.assertFalse(out["operator_cli_invoked"])
        self.assertFalse(out["review_request_written"])
        after = sorted(p.relative_to(Path(sandbox.name)).as_posix()
                       for p in Path(sandbox.name).rglob("*"))
        self.assertEqual(before, after)

    @skip_unless_symlinks
    def test_cli_symlink_artifact_refused(self):
        sandbox, root, life, _files, _validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        (root / "linked.json").symlink_to("manifest.json")
        rc, out, err = run_cli("preview-validated", "--format", "json", "--package-root", str(root),
                               "--validated-lifecycle", str(life))
        self.assertEqual(rc, 2)
        self.assertIsNone(out)
        self.assertFalse(err["authorization_granted"])
        self.assertIn("symlink", err["error"])

    def test_cli_missing_lifecycle_refused(self):
        sandbox, root, _life, _files, _validated, _manifest = fixture()
        self.addCleanup(sandbox.cleanup)
        rc, out, err = run_cli("preview-validated", "--format", "json", "--package-root", str(root),
                               "--validated-lifecycle", str(root / "missing.json"))
        self.assertEqual(rc, 2)
        self.assertIsNone(out)
        self.assertFalse(err["promotion_authorized"])
        self.assertIn("regular non-symlink", err["error"])


if __name__ == "__main__":
    unittest.main()
