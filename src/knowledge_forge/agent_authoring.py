"""Agent-authored draft -> governed VALIDATED package materialization.

This module deliberately stops at VALIDATED.  Semantic models may select source
support and judge entailment, but promotion remains an external human-authorized
operator action.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .claims import normalize_claim
from .emit import build_skill_package
from .faithfulness import validate_candidate_faithfulness
from .ingest import ingest_source
from .knowledge import UNIT_TYPES, canonicalize_compiler_result
from .lifecycle import (
    VALIDATED,
    create_lifecycle_record,
    evidence_sha256,
    transition_lifecycle,
    validate_lifecycle_record,
)
from .normalize import normalize_source
from .provenance import build_compiler_profile
from .validate import validate_skill_package

AUTHORING_SCHEMA_VERSION = "0.1"
PLAN_SCHEMA_VERSION = "0.1"
IMPORTER_VERSION = "agent0-authored-markdown-import-v1"
MAX_UNITS = 64
MAX_SUPPORT_SECTIONS_PER_UNIT = 3


class AgentAuthoringError(ValueError):
    """Raised when draft materialization cannot preserve Forge invariants."""


def _sha256_ref(payload: bytes) -> str:
    return "sha256:" + sha256(payload).hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(dict(value), sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    ).encode("utf-8")


def _rfc3339_to_datetime(value: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AgentAuthoringError("occurred_at must be a non-empty RFC3339 timestamp")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AgentAuthoringError("occurred_at must be RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AgentAuthoringError("occurred_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _normalize_authored_claim(raw_claim: Any, *, title: str) -> tuple[dict[str, Any], list[str]]:
    # Narrow legacy compatibility only; canonical claim schema is unchanged.
    if not isinstance(raw_claim, Mapping):
        raise AgentAuthoringError(f"structured claim must be a mapping: {title}")

    migrated = deepcopy(dict(raw_claim))
    migrations: list[str] = []

    validity = migrated.get("validity")
    if isinstance(validity, Mapping) and "note" in validity:
        validity_copy = deepcopy(dict(validity))
        note = validity_copy.pop("note")
        if not isinstance(note, str) or not note.strip():
            raise AgentAuthoringError(
                f"legacy validity.note must be a non-empty string: {title}"
            )

        qualifiers = migrated.get("qualifiers")
        if not isinstance(qualifiers, Mapping):
            raise AgentAuthoringError(
                f"claim qualifiers must be a mapping before validity.note migration: {title}"
            )
        qualifiers_copy = deepcopy(dict(qualifiers))
        existing = qualifiers_copy.get("validity_note")
        if existing is not None and existing != note:
            raise AgentAuthoringError(
                f"legacy validity.note conflicts with qualifiers.validity_note: {title}"
            )

        qualifiers_copy["validity_note"] = note
        migrated["qualifiers"] = qualifiers_copy
        migrated["validity"] = validity_copy
        migrations.append("validity.note->qualifiers.validity_note")

    try:
        return normalize_claim(migrated), migrations
    except ValueError as exc:
        raise AgentAuthoringError(f"invalid structured claim: {title}: {exc}") from exc


def _draft_units(draft_text: str) -> list[dict[str, Any]]:
    headings = list(re.finditer(r"(?m)^# (?!#)(.+?)\s*$", draft_text))
    units: list[dict[str, Any]] = []
    seen_claims: set[str] = set()

    for index, match in enumerate(headings):
        title = match.group(1).strip()
        start = match.end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(draft_text)
        block = draft_text[start:end]
        marker = "## Structured claim"
        marker_pos = block.find(marker)
        if marker_pos < 0:
            continue

        tail = block[marker_pos + len(marker) :]
        fence = re.search(r"```(?:json|JSON)\s*\n(.*?)\n```", tail, re.S)
        if fence is None:
            raise AgentAuthoringError(f"Structured claim has no JSON fence: {title}")
        try:
            raw_claim = json.loads(fence.group(1))
        except json.JSONDecodeError as exc:
            raise AgentAuthoringError(f"invalid structured claim JSON: {title}: {exc}") from exc
        claim, compatibility_migrations = _normalize_authored_claim(
            raw_claim,
            title=title,
        )

        claim_key = claim["claim_key"]
        if claim_key in seen_claims:
            raise AgentAuthoringError(f"duplicate draft claim_key: {claim_key}")
        seen_claims.add(claim_key)

        body = block[:marker_pos].strip()
        if not body:
            body = title
        units.append(
            {
                "title": title,
                "body": body,
                "claim": claim,
                "compatibility_migrations": compatibility_migrations,
            }
        )

    if not units:
        raise AgentAuthoringError(
            "draft contains no Structured claim blocks"
            " (each claim needs its own single-# heading section containing"
            " '## Structured claim' followed by a ```json fence)"
        )
    if len(units) > MAX_UNITS:
        raise AgentAuthoringError(f"draft exceeds {MAX_UNITS} knowledge-unit limit")
    return units


def prepare_agent_draft(
    source_path: str | Path,
    draft_path: str | Path,
    *,
    knowledge_id: str,
    skill_slug: str,
    title: str,
    default_kind: str = "CONCEPT",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Prepare exact source truth plus authored claims for a model support pass."""

    source = Path(source_path)
    draft = Path(draft_path)
    if source.is_symlink() or not source.is_file():
        raise AgentAuthoringError("source must be a regular non-symlink file")
    if draft.is_symlink() or not draft.is_file():
        raise AgentAuthoringError("draft must be a regular non-symlink file")
    if default_kind not in UNIT_TYPES:
        raise AgentAuthoringError(f"unsupported default_kind: {default_kind}")
    if not isinstance(title, str) or not title.strip():
        raise AgentAuthoringError("title must be non-empty")

    stamp = _rfc3339_to_datetime(occurred_at) if occurred_at else datetime.now(timezone.utc)
    manifest = ingest_source(source, ingested_at=stamp)
    normalized = normalize_source(source, manifest)
    draft_bytes = draft.read_bytes()
    try:
        draft_text = draft_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentAuthoringError("draft must be UTF-8") from exc

    # The legacy Agent0 draft must at least bind itself to the exact supplied
    # source bytes.  Its old s001/§ citations are treated as hints only and are
    # intentionally NOT trusted as normalized section identities.
    source_digest = manifest["byte_sha256"]
    if source_digest not in draft_text and manifest["source_id"] not in draft_text:
        raise AgentAuthoringError(
            "draft does not bind the exact supplied source sha256"
            " (the draft text must contain the source's sha256:... digest —"
            " e.g. '# source_sha256: <hex>'; re-bind after any source change)"
        )

    units = _draft_units(draft_text)
    return {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "operation": "PREPARE_AGENT_AUTHORED_DRAFT",
        "knowledge_id": knowledge_id,
        "skill_slug": skill_slug,
        "title": title.strip(),
        "purpose": "reference",
        "default_kind": default_kind,
        "draft": {
            "path": str(draft),
            "sha256": _sha256_ref(draft_bytes),
            "legacy_source_binding_hints_authoritative": False,
        },
        "source_manifest": manifest,
        "normalized_source": normalized,
        "units": units,
        "authority": "KNOWLEDGE_ONLY",
        "execution_authority": "NONE",
        "promotion_authorized": False,
    }


def _section_evidence(normalized: Mapping[str, Any], section_id: str) -> dict[str, Any]:
    matches = [
        section
        for section in normalized.get("sections", [])
        if isinstance(section, Mapping) and section.get("section_id") == section_id
    ]
    if len(matches) != 1:
        raise AgentAuthoringError(
            f"support section_id must resolve exactly once: {section_id}"
        )
    section = matches[0]
    text = section.get("text")
    if not isinstance(text, str) or not text:
        raise AgentAuthoringError(f"support section is empty: {section_id}")
    payload = text.encode("utf-8")
    section_sha = sha256(payload).hexdigest()
    if section.get("section_sha256") != section_sha:
        raise AgentAuthoringError(f"normalized section hash mismatch: {section_id}")
    return {
        "source_id": normalized["source_id"],
        "section_id": section_id,
        "section_sha256": section_sha,
        "coordinate_space": "normalized_section_utf8_bytes",
        "start_byte": 0,
        "end_byte": len(payload),
        "span_sha256": sha256(payload).hexdigest(),
    }


def _validated_plan(plan: Mapping[str, Any], claim_keys: list[str]) -> dict[str, dict[str, Any]]:
    if not isinstance(plan, Mapping):
        raise AgentAuthoringError("support plan must be a mapping")
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise AgentAuthoringError("support plan schema_version mismatch")
    judge_id = plan.get("judge_id")
    if not isinstance(judge_id, str) or not judge_id.strip():
        raise AgentAuthoringError("support plan requires judge_id")
    raw_units = plan.get("units")
    if not isinstance(raw_units, list):
        raise AgentAuthoringError("support plan units must be a list")

    by_key: dict[str, dict[str, Any]] = {}
    for raw in raw_units:
        if not isinstance(raw, Mapping):
            raise AgentAuthoringError("support plan unit must be a mapping")
        expected = {
            "claim_key",
            "section_ids",
            "entailment",
            "qualifiers_preserved",
        }
        if set(raw) != expected:
            raise AgentAuthoringError("support plan unit fields must match exact schema")
        key = raw.get("claim_key")
        if not isinstance(key, str) or key not in claim_keys:
            raise AgentAuthoringError(f"support plan contains unknown claim_key: {key}")
        if key in by_key:
            raise AgentAuthoringError(f"support plan duplicates claim_key: {key}")
        section_ids = raw.get("section_ids")
        if (
            not isinstance(section_ids, list)
            or not section_ids
            or len(section_ids) > MAX_SUPPORT_SECTIONS_PER_UNIT
            or any(not isinstance(item, str) or not item for item in section_ids)
        ):
            raise AgentAuthoringError(
                f"support plan requires 1..{MAX_SUPPORT_SECTIONS_PER_UNIT} section_ids: {key}"
            )
        if len(set(section_ids)) != len(section_ids):
            raise AgentAuthoringError(f"support plan section_ids must be unique: {key}")
        entailment = raw.get("entailment")
        if entailment not in {"SUPPORTED", "UNSUPPORTED", "UNCERTAIN"}:
            raise AgentAuthoringError(f"support plan entailment invalid: {key}")
        if not isinstance(raw.get("qualifiers_preserved"), bool):
            raise AgentAuthoringError(
                f"support plan qualifiers_preserved must be boolean: {key}"
            )
        by_key[key] = {
            "section_ids": list(section_ids),
            "entailment": entailment,
            "qualifiers_preserved": raw["qualifiers_preserved"],
        }

    if set(by_key) != set(claim_keys):
        missing = sorted(set(claim_keys) - set(by_key))
        extra = sorted(set(by_key) - set(claim_keys))
        raise AgentAuthoringError(
            f"support plan must cover exact claim set; missing={missing} extra={extra}"
        )
    return by_key


class _PlanJudge:
    def __init__(self, judge_id: str, plan_by_key: Mapping[str, Mapping[str, Any]]):
        self.judge_id = judge_id
        self._plan_by_key = plan_by_key

    def evaluate(self, *, claim, support_spans):
        key = claim.get("claim_key")
        verdict = self._plan_by_key.get(key)
        if not isinstance(verdict, Mapping):
            raise AgentAuthoringError(f"judge plan missing claim_key: {key}")
        return {
            "entailment": verdict["entailment"],
            "qualifiers_preserved": verdict["qualifiers_preserved"],
        }


def _compiler_profile(default_kind: str) -> dict[str, Any]:
    contract = (
        "agent0-authored-markdown-import-v1;"
        "preserve-title-body-and-structured-claim;"
        "source-support-added-from-exact-normalized-sections;"
        "no-semantic-claim-rewrite"
    )
    prompt = (
        "Import Agent0-authored governed knowledge without rewriting structured "
        "claims. Add deterministic Forge envelopes and exact source evidence only."
    )
    return build_compiler_profile(
        compiler_digest=_sha256_ref(contract.encode("utf-8")),
        compiler_version=IMPORTER_VERSION,
        model_identity="agent0-draft-author:unrecorded-original-model",
        prompt_template_hash=_sha256_ref(prompt.encode("utf-8")),
        parameters={
            "semantic_claim_rewrite": False,
            "legacy_default_kind": default_kind,
            "legacy_source_binding_hints_authoritative": False,
        },
    )


def finalize_agent_draft(
    source_path: str | Path,
    draft_path: str | Path,
    plan: Mapping[str, Any],
    output_root: str | Path,
    *,
    knowledge_id: str,
    skill_slug: str,
    title: str,
    default_kind: str,
    occurred_at: str,
    validator_id: str,
) -> dict[str, Any]:
    """Materialize one exact draft into a new-only VALIDATED Forge package."""

    if not isinstance(validator_id, str) or not validator_id.strip():
        raise AgentAuthoringError("validator_id must be non-empty")
    prepared = prepare_agent_draft(
        source_path,
        draft_path,
        knowledge_id=knowledge_id,
        skill_slug=skill_slug,
        title=title,
        default_kind=default_kind,
        occurred_at=occurred_at,
    )
    normalized = prepared["normalized_source"]
    source_id = normalized["source_id"]
    claim_keys = [unit["claim"]["claim_key"] for unit in prepared["units"]]
    plan_by_key = _validated_plan(plan, claim_keys)
    judge_id = str(plan["judge_id"]).strip()

    raw_units = []
    for authored in prepared["units"]:
        claim_key = authored["claim"]["claim_key"]
        decision = plan_by_key[claim_key]
        section_ids = decision["section_ids"]
        support = [_section_evidence(normalized, section_id) for section_id in section_ids]
        raw_units.append(
            {
                "kind": default_kind,
                "title": authored["title"],
                "body": authored["body"],
                "claim": deepcopy(authored["claim"]),
                "source_bindings": [
                    {
                        "source_id": source_id,
                        "section_ids": list(section_ids),
                    }
                ],
                "support": support,
            }
        )

    profile = _compiler_profile(default_kind)
    compiler_result = {
        "contract_version": "0.2",
        "adapter_id": IMPORTER_VERSION,
        "knowledge_id": knowledge_id,
        "compiler_profile": profile,
        "request_source_ids": [source_id],
        "authority": "KNOWLEDGE_ONLY",
        "candidate": {
            "title": title,
            "units": raw_units,
        },
    }
    candidate = canonicalize_compiler_result(
        compiler_result,
        skill_slug=skill_slug,
        purpose="reference",
    )

    faithfulness = validate_candidate_faithfulness(
        candidate,
        [normalized],
        _PlanJudge(judge_id, plan_by_key),
    )
    if faithfulness.get("valid") is not True:
        raise AgentAuthoringError(
            "automatic faithfulness validation refused: "
            + "; ".join(faithfulness.get("errors", []))
        )

    package_files = build_skill_package(candidate, compiled_at=occurred_at)
    validation = validate_skill_package(package_files)
    if validation.get("valid") is not True:
        raise AgentAuthoringError(
            "emitted package validation refused: "
            + "; ".join(validation.get("errors", []))
        )

    manifest = json.loads(package_files["manifest.json"].decode("utf-8"))
    receipt = json.loads(package_files["SOURCE_RECEIPT.json"].decode("utf-8"))
    lifecycle = create_lifecycle_record(manifest, receipt, created_at=occurred_at)
    validated = transition_lifecycle(
        lifecycle,
        VALIDATED,
        occurred_at=occurred_at,
        actor=validator_id.strip(),
        evidence={
            "package_receipt_sha256": lifecycle["package_receipt_sha256"],
            "validation_report": deepcopy(validation),
            "validation_report_sha256": evidence_sha256(validation),
            "faithfulness_report": deepcopy(faithfulness),
            "faithfulness_report_sha256": evidence_sha256(faithfulness),
            "validator_id": validator_id.strip(),
        },
    )
    lifecycle_report = validate_lifecycle_record(validated)
    if lifecycle_report.get("valid") is not True or validated.get("state") != VALIDATED:
        raise AgentAuthoringError(
            "VALIDATED lifecycle construction refused: "
            + "; ".join(lifecycle_report.get("errors", []))
        )

    root = Path(output_root)
    if root.exists():
        raise AgentAuthoringError("output_root already exists; overwrite is forbidden")
    package_root = root / "package"
    evidence_root = root / "evidence"
    lifecycle_path = root / "lifecycle.validated.json"
    package_root.mkdir(parents=True, mode=0o700)
    evidence_root.mkdir(parents=True, mode=0o700)

    try:
        for relative, payload in sorted(package_files.items()):
            target = package_root / relative
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.write_bytes(payload)

        (evidence_root / "candidate.json").write_bytes(_json_bytes(candidate))
        (evidence_root / "validation-report.json").write_bytes(_json_bytes(validation))
        (evidence_root / "faithfulness-report.json").write_bytes(_json_bytes(faithfulness))
        (evidence_root / "support-plan.json").write_bytes(_json_bytes(plan))
        lifecycle_path.write_bytes(_json_bytes(validated))
        materialization_receipt = {
            "schema_version": AUTHORING_SCHEMA_VERSION,
            "operation": "AGENT_DRAFT_TO_VALIDATED_PACKAGE",
            "occurred_at": occurred_at,
            "canonical_skill_id": manifest["skill_id"],
            "skill_version": manifest["skill_version"],
            "knowledge_id": manifest["knowledge_id"],
            "compiler_profile_id": profile["compiler_profile_id"],
            "judge_id": judge_id,
            "draft_sha256": prepared["draft"]["sha256"],
            "source_id": source_id,
            "package_receipt_sha256": validated["package_receipt_sha256"],
            "package_root": str(package_root),
            "validated_lifecycle": str(lifecycle_path),
            "authority": "KNOWLEDGE_ONLY",
            "execution_authority": "NONE",
            "promotion_performed": False,
            "human_authorization_consumed": False,
        }
        (root / "materialization-receipt.json").write_bytes(
            _json_bytes(materialization_receipt)
        )
    except Exception:
        # Partial files remain visible for diagnosis; registry truth was never touched.
        raise

    disk_files = {
        path.relative_to(package_root).as_posix(): path.read_bytes()
        for path in sorted(package_root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    }
    disk_report = validate_skill_package(disk_files)
    if disk_report.get("valid") is not True:
        raise AgentAuthoringError("persisted package failed revalidation")

    return {
        "schema_version": AUTHORING_SCHEMA_VERSION,
        "operation": "FINALIZE_AGENT_AUTHORED_DRAFT",
        "canonical_skill_id": manifest["skill_id"],
        "skill_version": manifest["skill_version"],
        "knowledge_id": manifest["knowledge_id"],
        "compiler_profile_id": profile["compiler_profile_id"],
        "judge_id": judge_id,
        "package_root": str(package_root),
        "validated_lifecycle": str(lifecycle_path),
        "materialization_receipt": str(root / "materialization-receipt.json"),
        "package_valid": True,
        "faithfulness_valid": True,
        "lifecycle_state": VALIDATED,
        "authority": "KNOWLEDGE_ONLY",
        "execution_authority": "NONE",
        "promotion_performed": False,
        "human_authorization_consumed": False,
    }
