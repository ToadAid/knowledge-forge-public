#!/usr/bin/env python3
"""SPDX-License-Identifier: Apache-2.0

Original deterministic fixture generator for ToadAid's synthetic EVM pilot.
No WETH9 bytecode, Solidity code, deployment artifacts, or externally built code.
The runtime is deliberately small, hand-assembled, and not a production token.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys

ADDRESS = "0x000000000000000000000000000000000000f00d"
CHAIN_ID = 31337
CHAIN_NAME = "Synthetic Pond Chain"
NAME = "Pond Reference"
SYMBOL = "POND"
SELECTORS = {
    # Standard ABI function-signature selectors, not copied contract bytecode.
    "name": "06fdde03",
    "symbol": "95d89b41",
    "decimals": "313ce567",
    "totalSupply": "18160ddd",
    "balanceOf": "70a08231",
    "allowance": "dd62ed3e",
    "transfer": "a9059cbb",  # transfer(address,uint256), intentionally always false
}

class Assembler:
    def __init__(self):
        self.code = bytearray()
        self.labels = {}
        self.fixups = []

    def op(self, val):
        self.code.append(val)

    def push(self, value, *, size=None):
        if isinstance(value, bytes):
            raw = value
        elif size is not None:
            raw = int(value).to_bytes(size, "big")
        else:
            i = int(value)
            raw = i.to_bytes(max(1, (i.bit_length() + 7) // 8), "big")
        assert 1 <= len(raw) <= 32
        self.op(0x5f + len(raw))
        self.code.extend(raw)

    def ref(self, label):
        self.op(0x61)  # PUSH2 destination, patched after labels are assigned
        pos = len(self.code)
        self.code.extend(b"\x00\x00")
        self.fixups.append((pos, label))

    def label(self, name):
        assert name not in self.labels
        self.labels[name] = len(self.code)
        self.op(0x5b)  # JUMPDEST

    def store_word(self, val, offset):
        self.push(val)
        self.push(offset)
        self.op(0x52)  # MSTORE

    def return_memory(self, count):
        self.push(count)
        self.push(0)
        self.op(0xf3)

    def string_result(self, value):
        encoded = value.encode("utf-8")
        assert len(encoded) <= 32
        self.store_word(32, 0)
        self.store_word(len(encoded), 32)
        self.store_word(encoded.ljust(32, b"\x00"), 64)
        self.return_memory(96)

    def bytes(self):
        for pos, name in self.fixups:
            assert name in self.labels
            target = self.labels[name]
            assert target < 65536
            self.code[pos:pos+2] = target.to_bytes(2, "big")
        return bytes(self.code)


def abi_fn(name, inputs, output_type, mutability="view"):
    return {
        "type": "function",
        "name": name,
        "inputs": [{"name": f"a{i}", "type": t} for i, t in enumerate(inputs)],
        "outputs": [] if output_type is None else [{"name": "", "type": output_type}],
        "stateMutability": mutability,
    }


def build_runtime(selectors):
    a = Assembler()
    a.push(0); a.op(0x35)   # CALLDATALOAD(0)
    a.push(224); a.op(0x1c) # SHR 224 => selector
    for name, value in selectors.items():
        a.op(0x80)          # DUP1
        a.push(bytes.fromhex(value))
        a.op(0x14)          # EQ
        a.ref(name)
        a.op(0x57)          # JUMPI
    a.push(0); a.push(0); a.op(0xfd) # REVERT for unknown selector
    for name in selectors:
        a.label(name)
        a.op(0x50)  # POP selector
        if name == "name":
            a.string_result(NAME)
        elif name == "symbol":
            a.string_result(SYMBOL)
        elif name == "decimals":
            a.store_word(18, 0); a.return_memory(32)
        elif name == "totalSupply":
            a.store_word(1000, 0); a.return_memory(32)
        elif name == "balanceOf":
            a.store_word(77, 0); a.return_memory(32)
        elif name == "allowance":
            a.store_word(0, 0); a.return_memory(32)
        elif name == "transfer":
            # Intentional non-view method stub: always returns false and never writes.
            a.store_word(0, 0); a.return_memory(32)
        else:
            raise AssertionError(name)
    return a.bytes()


def main():
    # All selectors are established ABI-signature selectors, no external compiler.
    selectors = dict(SELECTORS)
    runtime = build_runtime(selectors)
    root = Path(__file__).resolve().parents[1] / "fixtures" / "contracts" / "pond-reference"
    root.mkdir(parents=True, exist_ok=True)
    abi = [
        abi_fn("name", [], "string"),
        abi_fn("symbol", [], "string"),
        abi_fn("decimals", [], "uint8"),
        abi_fn("totalSupply", [], "uint256"),
        abi_fn("balanceOf", ["address"], "uint256"),
        abi_fn("allowance", ["address", "address"], "uint256"),
        # Non-view stub for access-control testing. It always returns false.
        abi_fn("transfer", ["address", "uint256"], "bool", mutability="nonpayable"),
    ]
    abi_bytes = (json.dumps(abi, indent=2) + "\n").encode()
    code_text = "0x" + runtime.hex() + "\n"
    sha = hashlib.sha256(runtime).hexdigest()
    pin = {
        "schema_version": "0.1",
        "pilot_id": "pond-reference",
        "chain": {"chain_id": CHAIN_ID, "name": CHAIN_NAME},
        "contract": {"address": ADDRESS, "name": "PondReference", "proxied": False},
        "source_pin": {
            "verification_provider": "Local Deterministic Build",
            "verification_status": "SYNTHETIC_NOT_DEPLOYED",
            "compiler_version": "ToadAid EVM assembler v1",
            "optimizer_enabled": False,
            "optimizer_runs": 0,
            "official_spec_contract": "PondReference",
            "official_spec_proxied": False,
            "source_document": "scripts/generate_pond_reference_fixture.py",
        },
        "identity_reads": {"name": NAME, "symbol": SYMBOL, "decimals": 18},
        "authority": {
            "knowledge": "KNOWLEDGE_ONLY", "read_only": True,
            "write": False, "sign": False, "send_transaction": False,
        },
        "read_plans": [
            {"id": "name", "function": "name", "inputs": [], "output": "string", "selector": "0x"+selectors["name"]},
            {"id": "symbol", "function": "symbol", "inputs": [], "output": "string", "selector": "0x"+selectors["symbol"]},
            {"id": "decimals", "function": "decimals", "inputs": [], "output": "uint256", "selector": "0x"+selectors["decimals"]},
            {"id": "total_supply", "function": "totalSupply", "inputs": [], "output": "uint256", "selector": "0x"+selectors["totalSupply"]},
            {"id": "balance_of", "function": "balanceOf", "inputs": ["address"], "output": "uint256", "selector": "0x"+selectors["balanceOf"]},
            {"id": "allowance", "function": "allowance", "inputs": ["address","address"], "output": "uint256", "selector": "0x"+selectors["allowance"]},
        ],
        "runtime_code": {
            "path": "runtime-code.hex", "hash_algorithm": "sha256",
            "byte_length": len(runtime), "sha256": sha,
            "evidence": "Synthetic ToadAid-owned deterministic EVM fixture; NOT deployed",
        },
        "abi": {"path": "abi.json", "sha256": hashlib.sha256(abi_bytes).hexdigest()},
    }
    # newline="\n": byte-identical fixtures on every OS (Windows would write CRLF).
    (root / "runtime-code.hex").write_text(code_text, encoding="utf-8", newline="\n")
    (root / "abi.json").write_bytes(abi_bytes)
    (root / "contract-pin.json").write_text(json.dumps(pin, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"runtime_code_bytes": len(runtime), "runtime_sha256": sha, "abi_sha256": pin["abi"]["sha256"],"fixture":str(root)},sort_keys=True))

if __name__ == "__main__":
    main()
