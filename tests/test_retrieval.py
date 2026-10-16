from __future__ import annotations

import json
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.retrieval import (
    RetrievalError,
    build_retrieval_index,
    evaluate_fixtures,
    question_rank_scores,
    route_question,
)

KNOWLEDGE_ID = "handbook:governance"
SOURCE = "sha256:" + ("a" * 64)


def unit(claim_key, kind, title, body, section):
    return {
        "unit_id": unit_id_for(KNOWLEDGE_ID, claim_key),
        "kind": kind,
        "title": title,
        "body": body,
        "claim": {
            "claim_key": claim_key,
            "subject": claim_key.rsplit(".", 1)[0],
            "predicate": "states",
            "value": body,
            "qualifiers": {},
            "scope": {},
            "validity": {},
            "executable": False,
        },
        "source_bindings": [{"source_id": SOURCE, "section_ids": [section]}],
    }


def candidate():
    return {
        "knowledge_id": KNOWLEDGE_ID,
        "units": [
            unit("governance.approval_quorum", "DECISION_RULE", "Approval Quorum", "The seven-person gate is required.", "s001"),
            unit("governance.deployment_window", "PROCEDURE", "Deployment Window", "Deployments happen on Tuesdays.", "s002"),
            unit("tea.grams_per_cup", "EXAMPLE", "Tea Recipe", "Use 3 grams of tea per cup.", "s003"),
        ],
    }


def compound_unit(claim_key, title, knowledge_id="lore:pond"):
    """A lore-native single-token alias reachable only by compound matching
    from a naturally spaced question ("Clair de Lune" -> "clairdelune")."""

    return {
        "unit_id": unit_id_for(knowledge_id, claim_key),
        "kind": "LORE_ENTRY",
        "title": title,
        "body": "The pond remembers what the surface forgets.",
        "claim": {
            "claim_key": claim_key,
            "subject": claim_key.rsplit(".", 1)[0],
            "predicate": "states",
            "value": "The pond remembers what the surface forgets.",
            "qualifiers": {},
            "scope": {},
            "validity": {},
            "executable": False,
        },
        "source_bindings": [{"source_id": "sha256:" + ("b" * 64), "section_ids": ["s001"]}],
    }


def compound_candidate():
    return {
        "knowledge_id": "lore:pond",
        "units": [
            compound_unit("lore.clairdelune", "Clairdelune"),
            compound_unit("lore.immortalcovenant", "Immortal Covenant"),
        ],
    }


COMPOUND_INDEX = None


def compound_index():
    global COMPOUND_INDEX
    if COMPOUND_INDEX is None:
        COMPOUND_INDEX = build_retrieval_index(compound_candidate())
    return COMPOUND_INDEX


CLAIRDELUNE = unit_id_for("lore:pond", "lore.clairdelune")
IMMORTAL_COVENANT = unit_id_for("lore:pond", "lore.immortalcovenant")


class RetrievalCompoundUnicodeTests(unittest.TestCase):
    def test_space_separated_compound_phrase_routes_to_lore_token(self):
        # Before compound matching this exact query was an honest NO MATCH:
        # the question tokens {clair, de, lune} share nothing with the
        # lore-native index token {clairdelune}.
        selected = route_question(compound_index(), "What is the Clair de Lune prophecy?")
        self.assertEqual([entry["unit_id"] for entry in selected], [CLAIRDELUNE])

    def test_exact_alias_still_ranks_above_compound_match(self):
        # "immortal covenant" matches Immortal Covenant exactly; Clairdelune
        # only compound-matches. max_units=1 must return the exact alias.
        selected = route_question(
            compound_index(),
            "What does the Immortal Covenant say about the Clair de Lune pond?",
        )
        self.assertEqual([entry["unit_id"] for entry in selected], [IMMORTAL_COVENANT])

    def test_prefix_word_routes_to_longer_alias_token_from_five_chars(self):
        # The only hook is the prefix: "govern" (6 chars) starts the longer
        # alias token "governance" and no query token appears in the terms.
        index = build_retrieval_index({
            "knowledge_id": "handbook:governance",
            "units": [compound_unit("governance.quorum.approval_count", "Governance Quorum", knowledge_id="handbook:governance")],
        })
        selected = route_question(index, "What does the govern rule say?")
        self.assertEqual(
            [entry["unit_id"] for entry in selected],
            [unit_id_for("handbook:governance", "governance.quorum.approval_count")],
        )

    def test_prefix_shorter_than_five_chars_does_not_route(self):
        index = build_retrieval_index({
            "knowledge_id": "handbook:governance",
            "units": [compound_unit("governance.quorum.approval_count", "Governance Quorum", knowledge_id="handbook:governance")],
        })
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(index, "What does the gove rule say?")

    def test_accented_unicode_word_is_one_token(self):
        index = build_retrieval_index({
            "knowledge_id": "lore:cafe",
            "units": [compound_unit("lore.cafe.ritual", "Café Ritual", knowledge_id="lore:cafe")],
        })
        selected = route_question(index, "What is the café ritual?")
        self.assertEqual(
            [entry["unit_id"] for entry in selected],
            [unit_id_for("lore:cafe", "lore.cafe.ritual")],
        )

    def test_cjk_alias_containment_routes_from_three_or_more_chars(self):
        index = build_retrieval_index({
            "knowledge_id": "lore:cjk",
            "units": [compound_unit("lore.immortalcovenant", "不死盟約", knowledge_id="lore:cjk")],
        })
        selected = route_question(index, "請解釋不死盟約的內容")
        self.assertEqual(
            [entry["unit_id"] for entry in selected],
            [unit_id_for("lore:cjk", "lore.immortalcovenant")],
        )

    def test_short_cjk_bigram_does_not_containment_route(self):
        # Two CJK chars weigh 4 < _MIN_COMPOUND_WEIGHT: bigrams are far too
        # common to containment-match, so the typed refusal must hold.
        index = build_retrieval_index({
            "knowledge_id": "lore:cjk",
            "units": [compound_unit("lore.immortalcovenant", "不死盟約", knowledge_id="lore:cjk")],
        })
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(index, "什麼是不死")

    def test_compound_tier_does_not_widen_a_genuine_miss(self):
        # No alias squashes into any contiguous n-gram of this question and
        # no token overlaps: the refusal must survive every new tier.
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(compound_index(), "How many satellites orbit Mars?")

    def test_body_only_phrase_still_does_not_route(self):
        # Body text is routing metadata in neither the old nor new tiers.
        index = build_retrieval_index({
            "knowledge_id": "lore:pond",
            "units": [compound_unit("lore.clairdelune", "Clairdelune")],
        })
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(index, "What does the pond surface forget?")

    def test_question_over_96_words_refuses_with_typed_bound(self):
        index = compound_index()
        question = " ".join(f"word{i}" for i in range(97))
        with self.assertRaisesRegex(RetrievalError, "deterministic routing bound"):
            route_question(index, question)

    def test_question_rank_scores_matches_route_question_ordering(self):
        units = route_question(
            compound_index(),
            "What does the Immortal Covenant say about the Clair de Lune pond?",
            max_units=2,
        )
        self.assertEqual(
            [entry["unit_id"] for entry in units],
            [IMMORTAL_COVENANT, CLAIRDELUNE],
        )
        scores = question_rank_scores(
            "What does the Immortal Covenant say about the Clair de Lune pond?",
            units,
        )
        exact, compound, prefix, overlap = scores
        self.assertGreater(exact, 0)
        self.assertGreater(compound, 0)
        self.assertIsInstance(overlap, int)

    def test_question_rank_scores_refuses_invalid_routed_entry(self):
        with self.assertRaisesRegex(RetrievalError, "invalid unit entry"):
            question_rank_scores("anything", ["not-a-mapping"])
        with self.assertRaisesRegex(RetrievalError, "routing metadata is invalid"):
            question_rank_scores("anything", [{"aliases": "not-a-list"}])


class RetrievalTests(unittest.TestCase):
    def test_index_is_deterministic_and_body_is_not_routing_metadata(self):
        one = build_retrieval_index(candidate())
        two = build_retrieval_index(candidate())
        self.assertEqual(one, two)
        encoded = json.dumps(one)
        self.assertNotIn("seven-person gate", encoded)
        self.assertNotIn("Deployments happen", encoded)

    def test_claim_alias_routes_to_expected_unit(self):
        index = build_retrieval_index(candidate())
        selected = route_question(index, "What approval quorum is required?")
        self.assertEqual(
            [entry["unit_id"] for entry in selected],
            [unit_id_for(KNOWLEDGE_ID, "governance.approval_quorum")],
        )

    def test_body_only_phrase_does_not_route(self):
        index = build_retrieval_index(candidate())
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(index, "What is the seven-person gate?")

    def test_miss_refuses_instead_of_widening(self):
        index = build_retrieval_index(candidate())
        with self.assertRaisesRegex(RetrievalError, "no relevant knowledge unit"):
            route_question(index, "How many satellites orbit Mars?")

    def test_hard_max_units_cannot_be_widened(self):
        index = build_retrieval_index(candidate())
        with self.assertRaisesRegex(RetrievalError, "between 1 and 3"):
            route_question(index, "governance", max_units=4)

    def test_fixture_eval_enforces_allowed_forbidden_and_refusal(self):
        index = build_retrieval_index(candidate())
        approval = unit_id_for(KNOWLEDGE_ID, "governance.approval_quorum")
        deployment = unit_id_for(KNOWLEDGE_ID, "governance.deployment_window")
        tea = unit_id_for(KNOWLEDGE_ID, "tea.grams_per_cup")
        report = evaluate_fixtures(index, [
            {
                "question": "What approval quorum is required?",
                "allowed_unit_ids": [approval],
                "forbidden_unit_ids": [deployment, tea],
                "max_units": 1,
                "must_refuse": False,
            },
            {
                "question": "How many satellites orbit Mars?",
                "allowed_unit_ids": [],
                "forbidden_unit_ids": [approval, deployment, tea],
                "max_units": 1,
                "must_refuse": True,
            },
        ])
        self.assertTrue(report["passed"])
        self.assertEqual(report["passed_count"], 2)


if __name__ == "__main__":
    unittest.main()
