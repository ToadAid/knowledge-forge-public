from __future__ import annotations
import unittest
from knowledge_forge.claims import ClaimError, normalize_claim, unit_id_for

def claim(**overrides):
    value = {
        "claim_key":"protocol.swap_fee.standard",
        "subject":"protocol.swap_fee",
        "predicate":"equals",
        "value":"0.30%",
        "qualifiers":{"pair_class":"volatile"},
        "scope":{"chain":"base"},
        "validity":{},
        "executable":False,
    }
    value.update(overrides)
    return value

class ClaimTests(unittest.TestCase):
    def test_identity_ignores_wording_and_value(self):
        a = unit_id_for("protocol:example","protocol.swap_fee.standard")
        b = unit_id_for("protocol:example","protocol.swap_fee.standard")
        self.assertEqual(a,b)
        self.assertNotEqual(a,unit_id_for("protocol:example","protocol.swap_fee.stable"))

    def test_executable_must_be_false(self):
        with self.assertRaisesRegex(ClaimError,"executable"):
            normalize_claim(claim(executable=True))

    def test_scope_qualifiers_validity_survive(self):
        result = normalize_claim(claim(
            value=30,
            qualifiers={"unit":"bps"},
            scope={"chain":"base"},
            validity={"as_of_source":"source-published-at"},
        ))
        self.assertEqual(result["value"],30)
        self.assertEqual(result["qualifiers"]["unit"],"bps")
        self.assertEqual(result["scope"]["chain"],"base")
        self.assertEqual(result["validity"]["as_of_source"],"source-published-at")

    def test_unknown_claim_field_refuses(self):
        with self.assertRaisesRegex(ClaimError, "unsupported fields"):
            normalize_claim(claim(hidden_instruction="do something"))

    def test_unknown_validity_key_refuses(self):
        with self.assertRaisesRegex(ClaimError,"unsupported"):
            normalize_claim(claim(validity={"now":True}))

if __name__ == "__main__":
    unittest.main()
