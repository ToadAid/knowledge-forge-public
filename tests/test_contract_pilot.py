from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from knowledge_forge.contract_pilot import (
    APPLICABLE,
    CHAIN_MISMATCH,
    CODE_HASH_MISMATCH,
    EXPECTED_RUNTIME_CODE_BYTES,
    EXPECTED_RUNTIME_CODE_SHA256,
    IDENTITY_MISMATCH,
    NO_CODE,
    RUNTIME_ACTIVE,
    RUNTIME_SUSPENDED,
    RPC_USER_AGENT,
    ContractPilotError,
    HttpReadonlyEvmProvider,
    build_read_call,
    check_live_applicability,
    execute_read_plan,
    load_contract_pilot,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "contracts" / "pond-reference"
ADDRESS = "0x000000000000000000000000000000000000f00d"
RUNTIME_CODE = (FIXTURE / "runtime-code.hex").read_text(
    encoding="utf-8"
).strip()


def abi_string(value: str) -> str:
    raw = value.encode("utf-8")
    padded = raw + b"\x00" * ((32 - len(raw) % 32) % 32)
    return "0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + padded.hex()


def abi_uint(value: int) -> str:
    return "0x" + value.to_bytes(32, "big").hex()


def mismatched_runtime_code() -> str:
    first = "61" if RUNTIME_CODE[2:4] != "61" else "60"
    return "0x" + first + RUNTIME_CODE[4:]


class FakeProvider:
    def __init__(
        self,
        chain_id=31337,
        code=RUNTIME_CODE,
        symbol="POND",
        default_uint=77,
    ):
        self._chain_id = chain_id
        self._code = code
        self._symbol = symbol
        self._default_uint = default_uint
        self.calls = []

    def chain_id(self):
        self.calls.append(("chain_id",))
        return self._chain_id

    def get_code(self, address):
        self.calls.append(("get_code", address))
        return self._code

    def eth_call(self, address, data, *, block="latest"):
        self.calls.append(("eth_call", address, data, block))
        selector = data[:10]
        if selector == "0x06fdde03":
            return abi_string("Pond Reference")
        if selector == "0x95d89b41":
            return abi_string(self._symbol)
        if selector == "0x313ce567":
            return abi_uint(18)
        return abi_uint(self._default_uint)


class ContractPilotTests(unittest.TestCase):
    def setUp(self):
        self.pin, self.abi = load_contract_pilot(FIXTURE)

    def test_http_provider_sends_bounded_json_headers(self):
        captured = {}

        class FakeResponse:
            status = 200
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, limit):
                captured["limit"] = limit
                return b'{"jsonrpc":"2.0","id":1,"result":"0x7a69"}'

        def fake_urlopen(request, timeout):
            captured["request"] = request
            captured["timeout"] = timeout
            return FakeResponse()

        with patch(
            "knowledge_forge.contract_pilot.urlopen",
            side_effect=fake_urlopen,
        ):
            provider = HttpReadonlyEvmProvider(
                "https://rpc.synthetic.example",
                timeout_seconds=3.5,
            )
            self.assertEqual(provider.chain_id(), 31337)

        request = captured["request"]
        headers = {key.lower(): value for key, value in request.header_items()}
        self.assertEqual(headers["content-type"], "application/json")
        self.assertEqual(headers["accept"], "application/json")
        self.assertEqual(headers["user-agent"], RPC_USER_AGENT)
        self.assertEqual(
            RPC_USER_AGENT,
            "ToadAid-Knowledge-Forge/0.1 read-only",
        )
        self.assertEqual(captured["timeout"], 3.5)
        self.assertEqual(captured["limit"], 1024 * 1024 + 1)

    def test_pin_binds_synthetic_pond_reference_and_zero_write_authority(self):
        self.assertEqual(self.pin["chain"]["chain_id"], 31337)
        self.assertEqual(self.pin["contract"]["address"], ADDRESS)
        self.assertFalse(self.pin["contract"]["proxied"])
        self.assertEqual(self.pin["source_pin"]["verification_provider"], "Local Deterministic Build")
        self.assertEqual(
            self.pin["authority"],
            {
                "knowledge": "KNOWLEDGE_ONLY",
                "read_only": True,
                "write": False,
                "sign": False,
                "send_transaction": False,
            },
        )

    def test_pin_binds_exact_runtime_code_commitment(self):
        binding = self.pin["runtime_code"]
        self.assertEqual(binding["path"], "runtime-code.hex")
        self.assertEqual(binding["hash_algorithm"], "sha256")
        self.assertEqual(
            binding["byte_length"],
            EXPECTED_RUNTIME_CODE_BYTES,
        )
        self.assertEqual(
            binding["sha256"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        raw = bytes.fromhex(RUNTIME_CODE[2:])
        self.assertEqual(len(raw), 255)
        self.assertEqual(
            sha256(raw).hexdigest(),
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        # Independently assembled synthetic EVM runtime, not Solidity metadata.
        self.assertGreater(len(raw), 100)
        self.assertLess(len(raw), 1024)
        self.assertNotEqual(raw, b"" )
        self.assertEqual(self.pin["source_pin"]["verification_status"], "SYNTHETIC_NOT_DEPLOYED")

    def test_abi_can_describe_writes_but_read_plans_do_not_expose_them(self):
        writes = {
            item["name"]
            for item in self.abi
            if item.get("type") == "function"
            and item.get("stateMutability") not in {"view", "pure"}
        }
        self.assertEqual(writes, {"transfer"})
        planned = {p["function"] for p in self.pin["read_plans"]}
        self.assertTrue(planned.isdisjoint(writes))
        with self.assertRaisesRegex(ContractPilotError, "unknown read plan"):
            build_read_call(self.pin, plan_id="transfer")

    def test_balance_of_call_is_read_only_and_encoded(self):
        account = "0x1111111111111111111111111111111111111111"
        call = build_read_call(self.pin, plan_id="balance_of", arguments=[account])
        self.assertEqual(call["authority"], "READ_ONLY_ETH_CALL")
        self.assertEqual(call["to"], ADDRESS)
        self.assertEqual(call["data"], "0x70a08231" + ("0" * 24) + account[2:])

    def test_execute_read_plan_uses_chain_codehash_then_call(self):
        provider = FakeProvider(default_uint=123)
        result = execute_read_plan(
            self.pin,
            provider,
            plan_id="balance_of",
            arguments=["0x1111111111111111111111111111111111111111"],
        )
        self.assertEqual(result["value"], 123)
        self.assertEqual(
            result["runtime_code_sha256"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        self.assertEqual(
            [x[0] for x in provider.calls],
            ["chain_id", "get_code", "eth_call"],
        )

    def test_execute_read_plan_refuses_codehash_mismatch_before_eth_call(self):
        provider = FakeProvider(code=mismatched_runtime_code())
        with self.assertRaisesRegex(
            ContractPilotError,
            "runtime code hash mismatch",
        ):
            execute_read_plan(
                self.pin,
                provider,
                plan_id="balance_of",
                arguments=[
                    "0x1111111111111111111111111111111111111111"
                ],
            )
        self.assertEqual(
            [x[0] for x in provider.calls],
            ["chain_id", "get_code"],
        )

    def test_live_applicability_checks_chain_codehash_identity(self):
        provider = FakeProvider()
        report = check_live_applicability(self.pin, provider)
        self.assertEqual(report["condition"], APPLICABLE)
        self.assertTrue(report["applicable"])
        self.assertEqual(
            report["runtime_knowledge_state"],
            RUNTIME_ACTIVE,
        )
        self.assertTrue(
            report["implementation_bound_knowledge_usable"]
        )
        self.assertFalse(report["lifecycle_mutated"])
        self.assertEqual(
            report["runtime_code_sha256_observed"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        self.assertEqual(
            report["observed_identity"],
            {"name": "Pond Reference", "symbol": "POND", "decimals": 18},
        )

    def test_codehash_mismatch_suspends_before_identity_reads(self):
        provider = FakeProvider(code=mismatched_runtime_code())
        report = check_live_applicability(self.pin, provider)
        self.assertEqual(report["condition"], CODE_HASH_MISMATCH)
        self.assertFalse(report["applicable"])
        self.assertEqual(
            report["runtime_knowledge_state"],
            RUNTIME_SUSPENDED,
        )
        self.assertFalse(
            report["implementation_bound_knowledge_usable"]
        )
        self.assertFalse(report["lifecycle_mutated"])
        self.assertEqual(
            report["runtime_code_sha256_expected"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        self.assertNotEqual(
            report["runtime_code_sha256_observed"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )
        self.assertEqual(
            [x[0] for x in provider.calls],
            ["chain_id", "get_code"],
        )

    def test_wrong_chain_refuses_before_contract_reads(self):
        provider = FakeProvider(chain_id=1)
        report = check_live_applicability(self.pin, provider)
        self.assertEqual(report["condition"], CHAIN_MISMATCH)
        self.assertEqual(provider.calls, [("chain_id",)])

    def test_no_code_refuses_before_identity_reads(self):
        provider = FakeProvider(code="0x")
        report = check_live_applicability(self.pin, provider)
        self.assertEqual(report["condition"], NO_CODE)
        self.assertEqual([x[0] for x in provider.calls], ["chain_id", "get_code"])

    def test_identity_mismatch_refuses_after_codehash_match(self):
        provider = FakeProvider(symbol="FAKE")
        report = check_live_applicability(self.pin, provider)
        self.assertEqual(report["condition"], IDENTITY_MISMATCH)
        self.assertFalse(report["applicable"])
        self.assertEqual(
            report["runtime_code_sha256_observed"],
            EXPECTED_RUNTIME_CODE_SHA256,
        )

    def test_abi_tamper_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FIXTURE / "contract-pin.json", root / "contract-pin.json")
            shutil.copy(FIXTURE / "runtime-code.hex", root / "runtime-code.hex")
            data = (FIXTURE / "abi.json").read_text(encoding="utf-8")
            (root / "abi.json").write_text(data + " ", encoding="utf-8")
            with self.assertRaisesRegex(ContractPilotError, "ABI sha256 mismatch"):
                load_contract_pilot(root)

    def test_runtime_code_artifact_tamper_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(FIXTURE / "contract-pin.json", root / "contract-pin.json")
            shutil.copy(FIXTURE / "abi.json", root / "abi.json")
            (root / "runtime-code.hex").write_text(
                mismatched_runtime_code() + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                ContractPilotError,
                "runtime code artifact sha256 mismatch",
            ):
                load_contract_pilot(root)


    def test_original_synthetic_fixture_is_exactly_reproducible(self):
        builder = ROOT / "scripts" / "generate_pond_reference_fixture.py"
        with tempfile.TemporaryDirectory() as tmp:
            local_root = Path(tmp)
            (local_root / "scripts").mkdir()
            local_builder = local_root / "scripts" / builder.name
            shutil.copyfile(builder, local_builder)
            run = subprocess.run(
                [sys.executable, str(local_builder)],
                cwd=local_root,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            reproduced = local_root / "fixtures" / "contracts" / "pond-reference"
            for filename in ("abi.json", "contract-pin.json", "runtime-code.hex"):
                self.assertEqual(
                    (reproduced / filename).read_bytes(),
                    (FIXTURE / filename).read_bytes(),
                    filename,
                )


if __name__ == "__main__":
    unittest.main()
