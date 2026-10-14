from __future__ import annotations

import unittest

from knowledge_forge.compiler import (
    CompilerContractError,
    build_compile_request,
    build_compile_request_from_sources,
    invoke_compiler,
)
from knowledge_forge.provenance import build_compiler_profile


SOURCE_ID = "sha256:" + ("a" * 64)


def profile():
    return build_compiler_profile(
        compiler_digest="sha256:" + ("c" * 64),
        compiler_version="1.0.0",
        model_identity="test/model-v1",
        prompt_template_hash="sha256:" + ("d" * 64),
        parameters={"temperature": 0},
    )


class EchoAdapter:
    adapter_id = "test.echo.v1"

    def compile(self, request):
        request["sources"][0]["sections"][0]["text"] = "mutated inside adapter"
        request["compiler_profile"]["model_identity"] = "mutated/model"
        return {"summary": "candidate", "seen_authority": request["authority"]}


class BadAdapter:
    adapter_id = "test.bad.v1"

    def compile(self, request):
        return "not-a-mapping"


class CompilerContractTests(unittest.TestCase):
    def test_build_request_is_provider_neutral_knowledge_only_and_profile_bound(self) -> None:
        normalized = {
            "source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE",
            "sections": [
                {
                    "section_id": "s001",
                    "heading": "Alpha",
                    "ordinal": 1,
                    "text": "original",
                    "anchor": {"kind": "line_range", "start_line": 1, "end_line": 2},
                }
            ],
        }
        expected_profile = profile()
        request = build_compile_request(
            normalized,
            knowledge_id="topic:alpha",
            skill_slug="alpha",
            compiler_profile=expected_profile,
            purpose="study",
        )

        self.assertEqual(request["authority"], "KNOWLEDGE_ONLY")
        self.assertNotIn("provider", request)
        self.assertEqual(request["knowledge_id"], "topic:alpha")
        self.assertEqual(request["compiler_profile"], expected_profile)
        self.assertEqual(request["sources"][0]["source_id"], SOURCE_ID)

    def test_multi_source_request_preserves_order_and_exact_source_bindings(self) -> None:
        other_source = "sha256:" + ("b" * 64)
        request = build_compile_request_from_sources(
            [
                {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": [{"section_id": "s001", "text": "a"}]},
                {"source_id": other_source, "source_role": "ORDINARY_KNOWLEDGE", "sections": [{"section_id": "s001", "text": "b"}]},
            ],
            knowledge_id="topic:multi",
            skill_slug="multi",
            compiler_profile=profile(),
        )
        self.assertEqual(
            [source["source_id"] for source in request["sources"]],
            [SOURCE_ID, other_source],
        )
        self.assertEqual(request["authority"], "KNOWLEDGE_ONLY")

    def test_multi_source_builder_deduplicates_identical_source_identity_only_when_sections_match(self) -> None:
        same = {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": [{"section_id": "s001", "text": "same"}]}
        request = build_compile_request_from_sources(
            [same, same],
            knowledge_id="topic:dedupe",
            skill_slug="dedupe",
            compiler_profile=profile(),
        )
        self.assertEqual(len(request["sources"]), 1)

        with self.assertRaisesRegex(CompilerContractError, "conflicting normalized sections"):
            build_compile_request_from_sources(
                [same, {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": [{"section_id": "s001", "text": "different"}]}],
                knowledge_id="topic:dedupe",
                skill_slug="dedupe",
                compiler_profile=profile(),
            )

    def test_adapter_cannot_mutate_caller_request_or_bound_profile(self) -> None:
        normalized = {
            "source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE",
            "sections": [{"section_id": "s001", "text": "original"}],
        }
        expected_profile = profile()
        request = build_compile_request(
            normalized,
            knowledge_id="topic:alpha",
            skill_slug="alpha",
            compiler_profile=expected_profile,
        )
        result = invoke_compiler(EchoAdapter(), request)

        self.assertEqual(request["sources"][0]["sections"][0]["text"], "original")
        self.assertEqual(request["compiler_profile"], expected_profile)
        self.assertEqual(result["adapter_id"], "test.echo.v1")
        self.assertEqual(result["knowledge_id"], "topic:alpha")
        self.assertEqual(result["compiler_profile"], expected_profile)
        self.assertEqual(result["request_source_ids"], [SOURCE_ID])
        self.assertEqual(result["authority"], "KNOWLEDGE_ONLY")
        self.assertEqual(result["candidate"]["summary"], "candidate")

    def test_compile_refuses_missing_source_role(self) -> None:
        normalized = {"source_id": SOURCE_ID, "sections": []}
        with self.assertRaisesRegex(CompilerContractError, "explicit source_role"):
            build_compile_request(
                normalized,
                knowledge_id="topic:alpha",
                skill_slug="alpha",
                compiler_profile=profile(),
            )

    def test_compile_refuses_protected_canon_role(self) -> None:
        normalized = {
            "source_id": SOURCE_ID,
            "source_role": "DOCTRINE",
            "sections": [],
        }
        with self.assertRaisesRegex(CompilerContractError, "CANON != SKILL.*DOCTRINE"):
            build_compile_request(
                normalized,
                knowledge_id="topic:alpha",
                skill_slug="alpha",
                compiler_profile=profile(),
            )

    def test_adapter_must_return_mapping(self) -> None:
        normalized = {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": []}
        request = build_compile_request(
            normalized,
            knowledge_id="topic:alpha",
            skill_slug="alpha",
            compiler_profile=profile(),
        )
        with self.assertRaisesRegex(CompilerContractError, "must return a mapping"):
            invoke_compiler(BadAdapter(), request)

    def test_slug_cannot_escape_skill_root(self) -> None:
        normalized = {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": []}
        with self.assertRaisesRegex(CompilerContractError, "single path component"):
            build_compile_request(
                normalized,
                knowledge_id="topic:alpha",
                skill_slug="../escape",
                compiler_profile=profile(),
            )

    def test_profile_id_cannot_lie_about_profile_content(self) -> None:
        bad = profile()
        bad["model_identity"] = "other/model"
        normalized = {"source_id": SOURCE_ID, "source_role": "ORDINARY_KNOWLEDGE", "sections": []}
        with self.assertRaisesRegex(CompilerContractError, "profile_id"):
            build_compile_request(
                normalized,
                knowledge_id="topic:alpha",
                skill_slug="alpha",
                compiler_profile=bad,
            )


if __name__ == "__main__":
    unittest.main()
