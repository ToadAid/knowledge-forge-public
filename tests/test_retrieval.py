from __future__ import annotations

import json
import unittest

from knowledge_forge.claims import unit_id_for
from knowledge_forge.retrieval import RetrievalError, build_retrieval_index, evaluate_fixtures, route_question

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
