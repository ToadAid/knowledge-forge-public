"""Deterministic claim-index retrieval and fixture-question evaluation."""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping, NamedTuple, Sequence

RETRIEVAL_INDEX_VERSION = "0.1"
HARD_MAX_UNITS_PER_TURN = 3
# Unicode-aware word tokens: any letter or digit run (ASCII, CJK ideographs,
# kana, or other scripts). Underscore is deliberately not a word character.
_TOKEN_RE = re.compile(r"[^\W_]+")
_SPACE_RE = re.compile(r"\s+")
# Whitespace/punctuation-split words that keep stopwords: compound matching
# needs "Clair de Lune" and "Gather the tribes" to stay contiguous.
_WORD_RE = re.compile(r"\w+")
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "how", "in", "is", "it", "of", "on", "or", "the", "to", "what",
    "when", "where", "which", "who", "why", "with",
})


class RetrievalError(ValueError):
    """Raised when deterministic retrieval cannot produce a safe route."""


# Deterministic matching bounds. Match tiers compare index metadata (aliases,
# terms) against question form only — never unit bodies — and every rule is an
# anchored containment or equality, never similarity scoring or widening when
# nothing matches: insufficient evidence keeps the typed NO MATCH refusal.
_MAX_QUERY_WORDS = 96
_MAX_COMPOUND_WORDS = 8
# Alias must squash to at least this weight to compound-match: 6 latin/other
# characters, or 3 CJK ideographs (a CJK character weighs 2; short CJK
# bigrams are far too common to containment-match safely).
_MIN_COMPOUND_WEIGHT = 6
# A query word routes by prefix only from this length, only at the start of
# an alias token, and only to a strictly longer compound token.
_MIN_PREFIX_CHARS = 5

# CJK ideographs (incl. extensions), kana, and CJK compatibility forms.
_CJK_RANGES = (
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
)


class _QueryPlan(NamedTuple):
    phrase: str
    tokens: frozenset[str]
    words: tuple[str, ...]
    compound_forms: frozenset[str]
    squashed: str


def _is_cjk_char(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def _normalized_phrase(value: str) -> str:
    return _SPACE_RE.sub(" ", value.strip().casefold())


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        token for token in _TOKEN_RE.findall(value.casefold())
        if token not in _STOPWORDS
    )


def _squashed(value: str) -> str:
    return "".join(ch for ch in value.casefold() if ch.isalnum())


def _pure_cjk(squashed: str) -> bool:
    return bool(squashed) and all(_is_cjk_char(ch) for ch in squashed)


def _match_weight(squashed: str) -> int:
    return sum(2 if _is_cjk_char(ch) else 1 for ch in squashed)


def _query_plan(question: str) -> _QueryPlan:
    phrase = _normalized_phrase(question)
    words = tuple(_WORD_RE.findall(phrase))
    if len(words) > _MAX_QUERY_WORDS:
        raise RetrievalError(
            f"question exceeds the deterministic routing bound of "
            f"{_MAX_QUERY_WORDS} words"
        )
    compound_forms: set[str] = set()
    for width in range(1, min(_MAX_COMPOUND_WORDS, len(words)) + 1):
        for start in range(len(words) - width + 1):
            form = _squashed(" ".join(words[start:start + width]))
            if _match_weight(form) >= _MIN_COMPOUND_WEIGHT:
                compound_forms.add(form)
    return _QueryPlan(
        phrase,
        frozenset(_tokens(question)),
        words,
        frozenset(compound_forms),
        _squashed(phrase),
    )


def _alias_rank(
    plan: _QueryPlan,
    aliases: Sequence[Any],
) -> tuple[int, int, int]:
    """Rank (exact_alias, compound, prefix) for one unit's index aliases."""

    exact = 0
    compound = 0
    prefix = 0
    for alias in aliases:
        if not isinstance(alias, str) or not alias:
            continue
        if _normalized_phrase(alias) in plan.phrase:
            exact = max(exact, len(_tokens(alias)))
        squashed = _squashed(alias)
        weight = _match_weight(squashed)
        if weight >= _MIN_COMPOUND_WEIGHT and (
            squashed in plan.compound_forms
            or (_pure_cjk(squashed) and squashed in plan.squashed)
        ):
            compound = max(compound, weight)
        for token in _tokens(alias):
            for word in plan.words:
                if (
                    len(word) >= _MIN_PREFIX_CHARS
                    and len(word) < len(token)
                    and token.startswith(word)
                ):
                    prefix = max(prefix, len(word))
    return exact, compound, prefix


def question_rank_scores(
    question: str,
    routed_units: Sequence[Mapping[str, Any]],
) -> tuple[int, int, int, int]:
    """Deterministic (exact_alias, compound, prefix, overlap) for routed units.

    Shared ranking for every consumption surface (governed and personal) so
    they route and rank identically. Index metadata only; unit bodies never
    participate. Returns the same components `route_question` ranked with.
    """

    plan = _query_plan(question)
    exact = 0
    compound = 0
    prefix = 0
    terms: set[str] = set()
    for entry in routed_units:
        if not isinstance(entry, Mapping):
            raise RetrievalError("retrieval index contains invalid unit entry")
        aliases = entry.get("aliases")
        if not isinstance(aliases, list):
            raise RetrievalError("retrieval index unit routing metadata is invalid")
        entry_exact, entry_compound, entry_prefix = _alias_rank(plan, aliases)
        exact = max(exact, entry_exact)
        compound = max(compound, entry_compound)
        prefix = max(prefix, entry_prefix)
        for term in entry.get("terms", []):
            if isinstance(term, str):
                terms.add(term)
    return exact, compound, prefix, len(plan.tokens & terms)


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

    plan = _query_plan(question)
    if not plan.tokens:
        raise RetrievalError("question contains no routing terms")

    units = index.get("units")
    if not isinstance(units, list) or not units:
        raise RetrievalError("retrieval index requires units")

    scored: list[tuple[int, int, int, int, str, dict[str, Any]]] = []
    for raw_entry in units:
        if not isinstance(raw_entry, Mapping):
            raise RetrievalError("retrieval index contains invalid unit entry")
        aliases = raw_entry.get("aliases")
        terms = raw_entry.get("terms")
        unit_id = raw_entry.get("unit_id")
        if not isinstance(aliases, list) or not isinstance(terms, list) or not isinstance(unit_id, str):
            raise RetrievalError("retrieval index unit routing metadata is invalid")

        exact_alias, compound, prefix = _alias_rank(plan, aliases)
        overlap = len(plan.tokens & set(terms))
        if exact_alias or compound or prefix or overlap:
            scored.append(
                (exact_alias, compound, prefix, overlap, unit_id,
                 deepcopy(dict(raw_entry)))
            )

    if not scored:
        raise RetrievalError("no relevant knowledge unit found from retrieval index")

    scored.sort(
        key=lambda item: (-item[0], -item[1], -item[2], -item[3], item[4])
    )
    return [entry for *_, entry in scored[:max_units]]


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
