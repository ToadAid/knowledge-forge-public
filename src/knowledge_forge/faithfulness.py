'''Exact source-span verification plus provider-neutral semantic faithfulness.'''

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any, Mapping, Protocol, runtime_checkable

from .conflicts import claim_sha256

FAITHFULNESS_SCHEMA_VERSION = "0.1"
VALIDATED = "VALIDATED"
FAILED_VALIDATION = "FAILED_VALIDATION"
ENTAILMENT_VERDICTS = frozenset({"SUPPORTED", "UNSUPPORTED", "UNCERTAIN"})


class FaithfulnessError(ValueError):
    pass


@runtime_checkable
class FaithfulnessJudge(Protocol):
    judge_id: str

    def evaluate(
        self,
        *,
        claim: Mapping[str, Any],
        support_spans: list[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        ...


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FaithfulnessError("value is not canonical JSON data") from exc


def candidate_sha256(candidate: Mapping[str, Any]) -> str:
    if not isinstance(candidate, Mapping):
        raise FaithfulnessError("candidate must be a mapping")
    return f"sha256:{sha256(_canonical_bytes(dict(candidate))).hexdigest()}"


def _section(source: Mapping[str, Any], section_id: str) -> Mapping[str, Any]:
    matches = [
        section
        for section in source.get("sections", [])
        if isinstance(section, Mapping) and section.get("section_id") == section_id
    ]
    if len(matches) != 1:
        raise FaithfulnessError("support section_id must resolve exactly once")
    return matches[0]


def support_evidence_for_exact_text(
    normalized_source: Mapping[str, Any],
    *,
    section_id: str,
    exact_text: str,
) -> dict[str, Any]:
    if not isinstance(exact_text, str) or not exact_text:
        raise FaithfulnessError("exact_text must be non-empty")
    source_id = normalized_source.get("source_id")
    if not isinstance(source_id, str) or not source_id.startswith("sha256:"):
        raise FaithfulnessError("normalized source requires source_id")
    section = _section(normalized_source, section_id)
    section_text = section.get("text")
    if not isinstance(section_text, str):
        raise FaithfulnessError("normalized section requires text")
    section_bytes = section_text.encode("utf-8")
    section_hash = sha256(section_bytes).hexdigest()
    if section.get("section_sha256") != section_hash:
        raise FaithfulnessError("normalized section hash mismatch")

    needle = exact_text.encode("utf-8")
    starts = []
    cursor = 0
    while True:
        index = section_bytes.find(needle, cursor)
        if index < 0:
            break
        starts.append(index)
        cursor = index + 1
    if len(starts) != 1:
        raise FaithfulnessError("exact support text must occur exactly once in section")
    start = starts[0]
    end = start + len(needle)
    return {
        "source_id": source_id,
        "section_id": section_id,
        "section_sha256": section_hash,
        "coordinate_space": "normalized_section_utf8_bytes",
        "start_byte": start,
        "end_byte": end,
        "span_sha256": sha256(needle).hexdigest(),
    }


def _source_map(normalized_sources: list[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    if not isinstance(normalized_sources, list) or not normalized_sources:
        raise FaithfulnessError("normalized_sources must be non-empty")
    result = {}
    for source in normalized_sources:
        if not isinstance(source, Mapping):
            raise FaithfulnessError("normalized source must be a mapping")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id.startswith("sha256:"):
            raise FaithfulnessError("normalized source requires source_id")
        if source_id in result:
            raise FaithfulnessError("duplicate normalized source_id")
        result[source_id] = source
    return result


def _verified_span(
    evidence: Mapping[str, Any],
    sources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    source_id = evidence.get("source_id")
    section_id = evidence.get("section_id")
    if source_id not in sources:
        raise FaithfulnessError("support source_id is not present in normalized sources")
    if evidence.get("coordinate_space") != "normalized_section_utf8_bytes":
        raise FaithfulnessError("unsupported support coordinate_space")
    section = _section(sources[source_id], section_id)
    text = section.get("text")
    if not isinstance(text, str):
        raise FaithfulnessError("normalized section requires text")
    payload = text.encode("utf-8")
    section_hash = sha256(payload).hexdigest()
    if section.get("section_sha256") != section_hash:
        raise FaithfulnessError("normalized section hash mismatch")
    if evidence.get("section_sha256") != section_hash:
        raise FaithfulnessError("support section_sha256 mismatch")
    start = evidence.get("start_byte")
    end = evidence.get("end_byte")
    if (
        not isinstance(start, int)
        or isinstance(start, bool)
        or not isinstance(end, int)
        or isinstance(end, bool)
        or start < 0
        or end <= start
        or end > len(payload)
    ):
        raise FaithfulnessError("support byte span is invalid")
    span = payload[start:end]
    span_hash = sha256(span).hexdigest()
    if evidence.get("span_sha256") != span_hash:
        raise FaithfulnessError("support span_sha256 mismatch")
    try:
        span_text = span.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FaithfulnessError("support span splits a UTF-8 code point") from exc
    return {
        "source_id": source_id,
        "section_id": section_id,
        "section_sha256": section_hash,
        "start_byte": start,
        "end_byte": end,
        "span_sha256": span_hash,
        "text": span_text,
    }


def _binding_allows(unit: Mapping[str, Any], evidence: Mapping[str, Any]) -> bool:
    for binding in unit.get("source_bindings", []):
        if (
            isinstance(binding, Mapping)
            and binding.get("source_id") == evidence.get("source_id")
            and evidence.get("section_id") in binding.get("section_ids", [])
        ):
            return True
    return False


def validate_candidate_faithfulness(
    candidate: Mapping[str, Any],
    normalized_sources: list[Mapping[str, Any]],
    judge: FaithfulnessJudge,
) -> dict[str, Any]:
    if not isinstance(candidate, Mapping):
        raise FaithfulnessError("candidate must be a mapping")
    judge_id = getattr(judge, "judge_id", None)
    if not isinstance(judge_id, str) or not judge_id.strip():
        raise FaithfulnessError("faithfulness judge requires stable judge_id")

    sources = _source_map(normalized_sources)
    candidate_sources = candidate.get("source_ids")
    units = candidate.get("units")
    if not isinstance(candidate_sources, list) or set(candidate_sources) != set(sources):
        raise FaithfulnessError("normalized source set must exactly match candidate source_ids")
    if not isinstance(units, list) or not units:
        raise FaithfulnessError("candidate requires units")

    errors: list[str] = []
    results: list[dict[str, Any]] = []

    for unit in units:
        if not isinstance(unit, Mapping):
            raise FaithfulnessError("candidate unit must be a mapping")
        unit_id = unit.get("unit_id")
        claim = unit.get("claim")
        support = unit.get("support")
        if not isinstance(unit_id, str) or not isinstance(claim, Mapping):
            raise FaithfulnessError("candidate unit identity/claim is invalid")
        semantic_claim_sha256 = claim_sha256(claim)
        if not isinstance(support, list) or not support:
            errors.append(f"{unit_id}: missing exact support evidence")
            results.append({
                "unit_id": unit_id,
                "claim_sha256": semantic_claim_sha256,
                "entailment": "UNSUPPORTED",
                "qualifiers_preserved": False,
                "support": [],
            })
            continue

        verified_for_judge = []
        public_support = []
        mechanical_failed = False
        for evidence in support:
            try:
                verified = _verified_span(evidence, sources)
            except FaithfulnessError as exc:
                errors.append(f"{unit_id}: {exc}")
                mechanical_failed = True
                continue
            if not _binding_allows(unit, evidence):
                errors.append(f"{unit_id}: support span is outside declared source_bindings")
                mechanical_failed = True
                continue
            verified_for_judge.append(verified)
            public_support.append({
                key: verified[key]
                for key in (
                    "source_id",
                    "section_id",
                    "section_sha256",
                    "start_byte",
                    "end_byte",
                    "span_sha256",
                )
            })

        if mechanical_failed or not verified_for_judge:
            results.append({
                "unit_id": unit_id,
                "claim_sha256": semantic_claim_sha256,
                "entailment": "UNSUPPORTED",
                "qualifiers_preserved": False,
                "support": public_support,
            })
            continue

        verdict = judge.evaluate(
            claim=deepcopy(dict(claim)),
            support_spans=deepcopy(verified_for_judge),
        )
        if not isinstance(verdict, Mapping):
            raise FaithfulnessError("faithfulness judge must return a mapping")
        entailment = verdict.get("entailment")
        qualifiers_preserved = verdict.get("qualifiers_preserved")
        if entailment not in ENTAILMENT_VERDICTS:
            raise FaithfulnessError("judge returned unknown entailment verdict")
        if not isinstance(qualifiers_preserved, bool):
            raise FaithfulnessError("judge must return qualifiers_preserved boolean")
        if entailment != "SUPPORTED":
            errors.append(f"{unit_id}: semantic support is {entailment}")
        if not qualifiers_preserved:
            errors.append(f"{unit_id}: claim qualifiers were not preserved")
        results.append({
            "unit_id": unit_id,
            "claim_sha256": semantic_claim_sha256,
            "entailment": entailment,
            "qualifiers_preserved": qualifiers_preserved,
            "support": public_support,
        })

    valid = not errors
    return {
        "schema_version": FAITHFULNESS_SCHEMA_VERSION,
        "state": VALIDATED if valid else FAILED_VALIDATION,
        "valid": valid,
        "candidate_sha256": candidate_sha256(candidate),
        "knowledge_id": candidate.get("knowledge_id"),
        "source_ids": list(candidate_sources),
        "judge_id": judge_id.strip(),
        "unit_results": results,
        "errors": errors,
    }
