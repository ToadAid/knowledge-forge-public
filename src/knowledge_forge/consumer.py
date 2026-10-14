"""Selective skill-package consumer proof for Stage 1."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from .retrieval import RetrievalError, route_question, validate_retrieval_index

AUTHORITY = "KNOWLEDGE_ONLY"
CANDIDATE_STATE = "COMPILED_CANDIDATE"
_INDEX_RE = re.compile(
    r"^- \[(?P<kind>[A-Z_]+)\] \[(?P<title>.+?)\]\((?P<path>units/[^)]+\.md)\) "
    r"\(`(?P<unit_id>[a-z0-9][a-z0-9._-]{0,127})`\)$"
)
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "who", "why", "with",
})


class SkillConsumerError(ValueError):
    """Raised when a package cannot be consumed selectively and safely."""


@runtime_checkable
class SkillAnswerer(Protocol):
    """Provider-neutral answerer that receives only selected skill context."""

    answerer_id: str

    def answer(self, *, question: str, context: list[Mapping[str, Any]]) -> str:
        """Answer from the selected knowledge-unit context only."""


def _json_object(payload: bytes, *, name: str) -> dict[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillConsumerError(f"{name} is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise SkillConsumerError(f"{name} must be a JSON object")
    return value


def _digest(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def _safe_unit_path(path: str) -> bool:
    if not isinstance(path, str) or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return (
        not pure.is_absolute()
        and len(pure.parts) == 2
        and pure.parts[0] == "units"
        and pure.parts[1] not in {"", ".", ".."}
        and pure.suffix == ".md"
    )


def _read(root: Path, relative: str) -> bytes:
    if relative.startswith(("/", "\\")) or "\\" in relative:
        raise SkillConsumerError("unsafe artifact path")
    target = (root / relative).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError as exc:
        raise SkillConsumerError("artifact path escapes skill root") from exc
    if not target.is_file():
        raise SkillConsumerError(f"missing skill artifact: {relative}")
    return target.read_bytes()


def _verify_hash(receipt: Mapping[str, Any], path: str, payload: bytes) -> None:
    hashes = receipt.get("artifact_hashes")
    if not isinstance(hashes, Mapping) or hashes.get(path) != _digest(payload):
        raise SkillConsumerError(f"artifact hash mismatch: {path}")


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(text.lower()) if token not in _STOPWORDS}


def _parse_index(skill_markdown: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line in skill_markdown.splitlines():
        match = _INDEX_RE.match(line.strip())
        if match:
            entry = match.groupdict()
            if not _safe_unit_path(entry["path"]):
                raise SkillConsumerError("SKILL.md contains unsafe unit path")
            entries.append(entry)
    if not entries:
        raise SkillConsumerError("SKILL.md contains no knowledge index entries")
    return entries


def _select(entries: list[dict[str, str]], question: str, *, max_units: int) -> list[dict[str, str]]:
    if not isinstance(max_units, int) or max_units < 1:
        raise SkillConsumerError("max_units must be a positive integer")
    query = _tokens(question)
    if not query:
        raise SkillConsumerError("question contains no selectable terms")

    scored: list[tuple[int, str, dict[str, str]]] = []
    for entry in entries:
        haystack = _tokens(
            f"{entry['title']} {entry['unit_id'].replace('-', ' ')} {entry['kind'].replace('_', ' ')}"
        )
        score = len(query & haystack)
        if score:
            scored.append((score, entry["unit_id"], entry))
    if not scored:
        raise SkillConsumerError("no relevant knowledge unit found from skill index")
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [entry for _, _, entry in scored[:max_units]]


def answer_from_candidate_skill(
    skill_root: str | Path,
    question: str,
    answerer: SkillAnswerer,
    *,
    max_units: int = 1,
) -> dict[str, Any]:
    """Answer using only package metadata, SKILL.md, and selected unit artifacts.

    This is the Stage 1 consumer proof and intentionally accepts only
    COMPILED_CANDIDATE packages. Governed promotion is introduced in Stage 2.
    """

    root = Path(skill_root)
    if not root.is_dir():
        raise SkillConsumerError("skill_root must be a directory")
    if not isinstance(question, str) or not question.strip():
        raise SkillConsumerError("question must be non-empty")

    answerer_id = getattr(answerer, "answerer_id", None)
    if not isinstance(answerer_id, str) or not answerer_id.strip():
        raise SkillConsumerError("answerer requires a stable answerer_id")

    receipt_bytes = _read(root, "SOURCE_RECEIPT.json")
    receipt = _json_object(receipt_bytes, name="SOURCE_RECEIPT.json")
    manifest_bytes = _read(root, "manifest.json")
    manifest = _json_object(manifest_bytes, name="manifest.json")
    skill_bytes = _read(root, "SKILL.md")
    retrieval_bytes = _read(root, "retrieval-index.json")
    retrieval_index = _json_object(retrieval_bytes, name="retrieval-index.json")

    if manifest.get("state") != CANDIDATE_STATE:
        raise SkillConsumerError("Stage 1 consumer accepts COMPILED_CANDIDATE only")
    if manifest.get("authority") != AUTHORITY:
        raise SkillConsumerError("skill authority must remain KNOWLEDGE_ONLY")
    if receipt.get("skill_id") != manifest.get("skill_id"):
        raise SkillConsumerError("receipt skill_id does not match manifest")
    if receipt.get("skill_version") != manifest.get("skill_version"):
        raise SkillConsumerError("receipt skill_version does not match manifest")

    knowledge_id = manifest.get("knowledge_id")
    if not isinstance(knowledge_id, str) or not knowledge_id:
        raise SkillConsumerError("manifest requires knowledge_id")
    if receipt.get("knowledge_id") != knowledge_id:
        raise SkillConsumerError("receipt knowledge_id does not match manifest")

    compiler = manifest.get("compiler")
    if not isinstance(compiler, dict):
        raise SkillConsumerError("manifest requires compiler provenance")
    if receipt.get("compiler") != compiler:
        raise SkillConsumerError("receipt compiler provenance does not match manifest")

    if receipt.get("source_ids") != manifest.get("source_ids"):
        raise SkillConsumerError("receipt source_ids do not match manifest")

    _verify_hash(receipt, "manifest.json", manifest_bytes)
    _verify_hash(receipt, "SKILL.md", skill_bytes)
    _verify_hash(receipt, "retrieval-index.json", retrieval_bytes)

    try:
        skill_markdown = skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillConsumerError("SKILL.md is not UTF-8") from exc
    entries = _parse_index(skill_markdown)

    manifest_units = manifest.get("units")
    if not isinstance(manifest_units, list) or not manifest_units:
        raise SkillConsumerError("manifest contains no knowledge units")
    declared: dict[str, dict[str, Any]] = {}
    for unit in manifest_units:
        if not isinstance(unit, dict):
            raise SkillConsumerError("manifest contains invalid unit entry")
        unit_id = unit.get("unit_id")
        path = unit.get("path")
        kind = unit.get("kind")
        if not isinstance(unit_id, str) or not _safe_unit_path(path):
            raise SkillConsumerError("manifest contains invalid unit entry")
        declared[unit_id] = {"path": path, "kind": kind}

    if len(entries) != len(declared):
        raise SkillConsumerError("SKILL.md index does not match manifest unit set")
    for entry in entries:
        bound = declared.get(entry["unit_id"])
        if bound is None or bound["path"] != entry["path"] or bound["kind"] != entry["kind"]:
            raise SkillConsumerError("SKILL.md index does not match manifest unit set")

    try:
        validated_index = validate_retrieval_index(retrieval_index, manifest)
        selected = route_question(validated_index, question, max_units=max_units)
    except RetrievalError as exc:
        raise SkillConsumerError(str(exc)) from exc

    context: list[dict[str, Any]] = []
    loaded_bytes = len(receipt_bytes) + len(manifest_bytes) + len(skill_bytes) + len(retrieval_bytes)
    loaded_paths = ["SOURCE_RECEIPT.json", "manifest.json", "SKILL.md", "retrieval-index.json"]

    for entry in selected:
        payload = _read(root, entry["path"])
        _verify_hash(receipt, entry["path"], payload)
        try:
            content = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillConsumerError(f"{entry['path']} is not UTF-8") from exc
        loaded_bytes += len(payload)
        loaded_paths.append(entry["path"])
        context.append({
            "unit_id": entry["unit_id"],
            "claim_key": entry["claim_key"],
            "kind": entry["kind"],
            "title": entry["title"],
            "source_bindings": deepcopy(entry["source_bindings"]),
            "content": content,
        })

    answer = answerer.answer(question=question, context=deepcopy(context))
    if not isinstance(answer, str) or not answer.strip():
        raise SkillConsumerError("answerer must return a non-empty string")

    return {
        "answer": answer.strip(),
        "receipt": {
            "consumer_contract_version": "0.2",
            "retrieval_contract_version": validated_index["schema_version"],
            "answerer_id": answerer_id,
            "skill_id": manifest.get("skill_id"),
            "skill_version": manifest.get("skill_version"),
            "knowledge_id": knowledge_id,
            "compiler_profile_id": compiler.get("compiler_profile_id"),
            "source_ids": list(manifest.get("source_ids", [])),
            "question_sha256": f"sha256:{sha256(question.encode('utf-8')).hexdigest()}",
            "loaded_artifacts": loaded_paths,
            "loaded_unit_ids": [entry["unit_id"] for entry in selected],
            "loaded_units": [
                {
                    "unit_id": entry["unit_id"],
                    "claim_key": entry["claim_key"],
                    "source_bindings": deepcopy(entry["source_bindings"]),
                }
                for entry in selected
            ],
            "max_units": max_units,
            "loaded_bytes": loaded_bytes,
            "package_unit_count": len(declared),
            "loaded_unit_count": len(selected),
            "unloaded_unit_count": len(declared) - len(selected),
            "authority": AUTHORITY,
        },
    }
