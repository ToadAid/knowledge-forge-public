"""Deterministic claim-index retrieval and fixture-question evaluation."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

RETRIEVAL_INDEX_VERSION = "0.1"
HARD_MAX_UNITS_PER_TURN = 3
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_SPACE_RE = re.compile(r"\s+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "who", "why", "with",
})


class RetrievalError(ValueError):
    """Raised when deterministic retrieval cannot produce a safe route."""


def _normalized_phrase(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip().lower())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token for token in _TOKEN_RE.findall(value.lower())
        if token not in _STOPWORDS
    )


def _claim_aliases(unit: Mapping[str, Any]) -> list[str]:
    claim = unit.get("claim")
    if not isinstance(claim, Mapping):
        raise RetrievalError("knowledge unit requires structured claim metadata")

    claim_key = claim.get("claim_key")
    subject = claim.get("subject")
    title = unit.get("title")
    kind = unit.get("kind")
    values: list[str] = []
    for value in (title, claim_key, subject, kind):
        if isinstance(value, str) and value.strip():
            values.append(_normalized_phrase(value.replace("_", " ").replace(".", " ")))

    if isinstance(claim_key, str):
        parts = [part for part in re.split(r"[._:-]+", claim_key.lower()) if part]
        for start in range(len(parts)):
            values.append(" ".join(parts[start:]))

    aliases: list[str] = []
    for value in values:
        if value and value not in aliases:
            aliases.append(value)
    return aliases


def build_retrieval_index(candidate: Mapping[str, Any]) -> dict[str, Any]:
    """Build router metadata without copying unit body/value into the index."""

    knowledge_id = candidate.get("knowledge_id")
    units = candidate.get("units")
    if not isinstance(knowledge_id, str) or not knowledge_id:
        raise RetrievalError("candidate requires knowledge_id")
    if not isinstance(units, list) or not units:
        raise RetrievalError("candidate requires units")

    entries: list[dict[str, Any]] = []
    for unit in units:
        if not isinstance(unit, Mapping):
            raise RetrievalError("candidate units must be mappings")
        claim = unit.get("claim")
        if not isinstance(claim, Mapping):
            raise RetrievalError("knowledge unit requires structured claim metadata")
        unit_id = unit.get("unit_id")
        claim_key = claim.get("claim_key")
        kind = unit.get("kind")
        title = unit.get("title")
        bindings = unit.get("source_bindings")
        if not all(isinstance(value, str) and value for value in (unit_id, claim_key, kind, title)):
            raise RetrievalError("retrieval unit identity is incomplete")
        if claim.get("executable") is not False:
            raise RetrievalError("retrieval unit executable must be false")
        if not isinstance(bindings, list) or not bindings:
            raise RetrievalError("retrieval unit requires source_bindings")

        aliases = _claim_aliases(unit)
        terms = sorted({token for alias in aliases for token in _tokens(alias)})
        if not terms:
            raise RetrievalError("retrieval unit has no deterministic routing terms")

        entries.append({
            "unit_id": unit_id,
            "claim_key": claim_key,
            "kind": kind,
            "title": title.strip(),
            "path": f"units/{unit_id}.md",
            "aliases": aliases,
            "terms": terms,
            "source_bindings": deepcopy(bindings),
            "executable": False,
        })

    entries.sort(key=lambda entry: entry["unit_id"])
    return {
        "schema_version": RETRIEVAL_INDEX_VERSION,
        "knowledge_id": knowledge_id,
        "max_units_per_turn": HARD_MAX_UNITS_PER_TURN,
        "units": entries,
    }


def validate_retrieval_index(
    index: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a hash-bound router against package manifest identity."""

    if not isinstance(index, Mapping):
        raise RetrievalError("retrieval index must be a mapping")
    if index.get("schema_version") != RETRIEVAL_INDEX_VERSION:
        raise RetrievalError("retrieval index schema_version mismatch")
    if index.get("knowledge_id") != manifest.get("knowledge_id"):
        raise RetrievalError("retrieval index knowledge_id mismatch")
    if index.get("max_units_per_turn") != HARD_MAX_UNITS_PER_TURN:
        raise RetrievalError("retrieval index max_units_per_turn mismatch")

    index_units = index.get("units")
    manifest_units = manifest.get("units")
    if not isinstance(index_units, list) or not index_units:
        raise RetrievalError("retrieval index requires units")
    if not isinstance(manifest_units, list) or not manifest_units:
        raise RetrievalError("manifest requires units")

    manifest_by_id: dict[str, Mapping[str, Any]] = {}
    for unit in manifest_units:
        if not isinstance(unit, Mapping):
            raise RetrievalError("manifest contains invalid unit entry")
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise RetrievalError("manifest contains invalid unit identity")
        manifest_by_id[unit_id] = unit

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    source_ids = set(manifest.get("source_ids", []))

    for entry in index_units:
        if not isinstance(entry, Mapping):
            raise RetrievalError("retrieval index contains invalid unit entry")
        unit_id = entry.get("unit_id")
        bound = manifest_by_id.get(unit_id)
        if bound is None or unit_id in seen:
            raise RetrievalError("retrieval index unit set does not match manifest")
        seen.add(unit_id)

        for key in ("claim_key", "kind", "path", "executable"):
            if entry.get(key) != bound.get(key):
                raise RetrievalError(f"retrieval index {key} does not match manifest")

        aliases = entry.get("aliases")
        terms = entry.get("terms")
        bindings = entry.get("source_bindings")
        title = entry.get("title")
        if not isinstance(title, str) or not title.strip():
            raise RetrievalError("retrieval index unit requires title")
        if not isinstance(aliases, list) or not aliases or any(
            not isinstance(alias, str) or not alias for alias in aliases
        ):
            raise RetrievalError("retrieval aliases are invalid")
        if not isinstance(terms, list) or not terms or terms != sorted(set(terms)) or any(
            not isinstance(term, str) or not term for term in terms
        ):
            raise RetrievalError("retrieval terms are invalid")
        if not isinstance(bindings, list) or not bindings:
            raise RetrievalError("retrieval source_bindings are invalid")
        for binding in bindings:
            if not isinstance(binding, Mapping) or binding.get("source_id") not in source_ids:
                raise RetrievalError("retrieval source binding is outside manifest sources")

        normalized.append(deepcopy(dict(entry)))

    if seen != set(manifest_by_id):
        raise RetrievalError("retrieval index unit set does not match manifest")

    return {
        "schema_version": RETRIEVAL_INDEX_VERSION,
        "knowledge_id": index["knowledge_id"],
        "max_units_per_turn": HARD_MAX_UNITS_PER_TURN,
        "units": normalized,
    }


def route_question(
    index: Mapping[str, Any],
    question: str,
    *,
    max_units: int = 1,
) -> list[dict[str, Any]]:
    """Route from index metadata only. Unit body/value never participates."""

    if not isinstance(question, str) or not question.strip():
        raise RetrievalError("question must be non-empty")
    if not isinstance(max_units, int) or not 1 <= max_units <= HARD_MAX_UNITS_PER_TURN:
        raise RetrievalError(f"max_units must be between 1 and {HARD_MAX_UNITS_PER_TURN}")
    if index.get("schema_version") != RETRIEVAL_INDEX_VERSION:
        raise RetrievalError("retrieval index schema_version mismatch")

    query_phrase = _normalized_phrase(question)
    query_tokens = set(_tokens(question))
    if not query_tokens:
        raise RetrievalError("question contains no routing terms")

    units = index.get("units")
    if not isinstance(units, list) or not units:
        raise RetrievalError("retrieval index requires units")

    scored: list[tuple[int, int, str, dict[str, Any]]] = []
    for raw_entry in units:
        if not isinstance(raw_entry, Mapping):
            raise RetrievalError("retrieval index contains invalid unit entry")
        aliases = raw_entry.get("aliases")
        terms = raw_entry.get("terms")
        unit_id = raw_entry.get("unit_id")
        if not isinstance(aliases, list) or not isinstance(terms, list) or not isinstance(unit_id, str):
            raise RetrievalError("retrieval index unit routing metadata is invalid")

        overlap = len(query_tokens & set(terms))
        exact_alias = max(
            (
                len(_tokens(alias))
                for alias in aliases
                if isinstance(alias, str)
                and alias
                and _normalized_phrase(alias) in query_phrase
            ),
            default=0,
        )
        if overlap or exact_alias:
            scored.append((exact_alias, overlap, unit_id, deepcopy(dict(raw_entry))))

    if not scored:
        raise RetrievalError("no relevant knowledge unit found from retrieval index")

    scored.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [entry for _, _, _, entry in scored[:max_units]]


def evaluate_fixture(index: Mapping[str, Any], fixture: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one routing fixture without answering the question."""

    if not isinstance(fixture, Mapping):
        raise RetrievalError("fixture must be a mapping")
    question = fixture.get("question")
    allowed = fixture.get("allowed_unit_ids")
    forbidden = fixture.get("forbidden_unit_ids")
    max_units = fixture.get("max_units")
    must_refuse = fixture.get("must_refuse")
    if not isinstance(question, str) or not question.strip():
        raise RetrievalError("fixture question must be non-empty")
    if not isinstance(allowed, list) or any(not isinstance(value, str) for value in allowed):
        raise RetrievalError("fixture allowed_unit_ids must be strings")
    if not isinstance(forbidden, list) or any(not isinstance(value, str) for value in forbidden):
        raise RetrievalError("fixture forbidden_unit_ids must be strings")
    if not isinstance(max_units, int):
        raise RetrievalError("fixture max_units must be an integer")
    if not isinstance(must_refuse, bool):
        raise RetrievalError("fixture must_refuse must be boolean")

    selected_ids: list[str] = []
    refused = False
    refusal_reason = None
    try:
        selected = route_question(index, question, max_units=max_units)
        selected_ids = [entry["unit_id"] for entry in selected]
    except RetrievalError as exc:
        refused = True
        refusal_reason = str(exc)

    if must_refuse:
        passed = refused
    else:
        passed = (
            not refused
            and bool(selected_ids)
            and set(selected_ids).issubset(set(allowed))
            and not (set(selected_ids) & set(forbidden))
            and len(selected_ids) <= max_units
        )

    return {
        "passed": passed,
        "question": question,
        "selected_unit_ids": selected_ids,
        "refused": refused,
        "refusal_reason": refusal_reason,
        "max_units": max_units,
        "must_refuse": must_refuse,
    }


def evaluate_fixtures(index: Mapping[str, Any], fixtures: list[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(fixtures, list) or not fixtures:
        raise RetrievalError("fixtures must be a non-empty list")
    results = [evaluate_fixture(index, fixture) for fixture in fixtures]
    return {
        "passed": all(result["passed"] for result in results),
        "fixture_count": len(results),
        "passed_count": sum(1 for result in results if result["passed"]),
        "results": results,
    }
