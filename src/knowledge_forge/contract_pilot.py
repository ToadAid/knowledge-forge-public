"""P3F/P4G synthetic PondReference contract-shaped governed knowledge pilot.

Contract knowledge is not contract authority. This module exposes a bounded
read-only EVM surface: chain identity, exact runtime implementation-code
commitment, and eth_call against allowlisted view plans. No signing,
send-transaction, deploy, or write API exists.

P4G adds fail-closed runtime applicability: implementation-bound knowledge is
computed SUSPENDED when the latest code at the pinned address does not match
the pinned runtime bytecode commitment. This computed suspension does not
mutate governed lifecycle history.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlparse
from urllib.request import Request, urlopen

SCHEMA_VERSION = "0.1"
APPLICABILITY_SCHEMA_VERSION = "0.2"
EXPECTED_CHAIN_ID = 31337
EXPECTED_ADDRESS = "0x000000000000000000000000000000000000f00d"
EXPECTED_RUNTIME_CODE_SHA256 = "33c7e2e6d1b2a61a8f5dd3402d2cd91f02df96959f794ca889fe039a7e4006d1"
EXPECTED_RUNTIME_CODE_BYTES = 255
ALLOWED_RPC_METHODS = frozenset({"eth_chainId", "eth_getCode", "eth_call"})
MAX_RPC_RESPONSE_BYTES = 1024 * 1024
RPC_USER_AGENT = "ToadAid-Knowledge-Forge/0.1 read-only"

APPLICABLE = "APPLICABLE"
CHAIN_MISMATCH = "CHAIN_MISMATCH"
NO_CODE = "NO_CODE"
CODE_HASH_MISMATCH = "CODE_HASH_MISMATCH"
IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
READ_FAILURE = "READ_FAILURE"

RUNTIME_ACTIVE = "ACTIVE"
RUNTIME_SUSPENDED = "SUSPENDED"


class ContractPilotError(ValueError):
    pass


@runtime_checkable
class ReadonlyEvmProvider(Protocol):
    def chain_id(self) -> int: ...
    def get_code(self, address: str) -> str: ...
    def eth_call(self, address: str, data: str, *, block: str = "latest") -> str: ...


def _address(value: Any) -> str:
    if not isinstance(value, str):
        raise ContractPilotError("address must be a string")
    value = value.lower()
    if (
        len(value) != 42
        or not value.startswith("0x")
        or any(ch not in "0123456789abcdef" for ch in value[2:])
    ):
        raise ContractPilotError("address must be exactly 20 bytes")
    return value


def _hex(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ContractPilotError(f"{label} must be 0x-prefixed hex")
    body = value[2:]
    if len(body) % 2 or any(ch not in "0123456789abcdefABCDEF" for ch in body):
        raise ContractPilotError(f"{label} is invalid hex")
    return "0x" + body.lower()


def _runtime_code_observation(code: Any) -> tuple[str, int, str]:
    normalized = _hex(code, "runtime code")
    raw = bytes.fromhex(normalized[2:])
    return normalized, len(raw), sha256(raw).hexdigest()


def _runtime_code_pin(pin: Mapping[str, Any]) -> Mapping[str, Any]:
    binding = pin.get("runtime_code")
    expected = {
        "path": "runtime-code.hex",
        "hash_algorithm": "sha256",
        "byte_length": EXPECTED_RUNTIME_CODE_BYTES,
        "sha256": EXPECTED_RUNTIME_CODE_SHA256,
        "evidence": "Synthetic ToadAid-owned deterministic EVM fixture; NOT deployed",
    }
    if binding != expected:
        raise ContractPilotError("runtime code pin mismatch")
    return binding


def _assert_runtime_code_matches(
    pin: Mapping[str, Any],
    code: Any,
    *,
    context: str,
) -> tuple[int, str]:
    binding = _runtime_code_pin(pin)
    normalized, byte_length, digest = _runtime_code_observation(code)
    if normalized in {"0x", "0x00"}:
        raise ContractPilotError(f"{context} contract code is absent")
    if (
        byte_length != binding["byte_length"]
        or digest != binding["sha256"]
    ):
        raise ContractPilotError(f"{context} runtime code hash mismatch")
    return byte_length, digest


class HttpReadonlyEvmProvider:
    """Minimal JSON-RPC reader with no generic write method."""

    def __init__(self, endpoint: str, *, timeout_seconds: float = 10.0):
        parsed = urlparse(endpoint)
        loopback_http = parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1", "localhost", "::1"
        }
        if parsed.scheme != "https" and not loopback_http:
            raise ContractPilotError("RPC must use HTTPS or loopback HTTP")
        if not parsed.netloc:
            raise ContractPilotError("RPC endpoint is invalid")
        self.endpoint = endpoint
        self.timeout_seconds = float(timeout_seconds)
        self._id = 0

    def _request(self, method: str, params: list[Any]) -> Any:
        if method not in ALLOWED_RPC_METHODS:
            raise ContractPilotError(
                f"RPC method is outside read-only authority: {method}"
            )
        self._id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=payload,
            headers={
                "content-type": "application/json",
                "accept": "application/json",
                "user-agent": RPC_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(MAX_RPC_RESPONSE_BYTES + 1)
        except OSError as exc:
            raise ContractPilotError("read-only RPC request failed") from exc
        if len(raw) > MAX_RPC_RESPONSE_BYTES:
            raise ContractPilotError("RPC response exceeds size limit")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractPilotError("RPC response is not valid JSON") from exc
        if not isinstance(decoded, Mapping):
            raise ContractPilotError("RPC response must be a mapping")
        if decoded.get("error") is not None:
            raise ContractPilotError("RPC returned an error")
        if decoded.get("id") != self._id or "result" not in decoded:
            raise ContractPilotError("RPC response binding is invalid")
        return decoded["result"]

    def chain_id(self) -> int:
        result = self._request("eth_chainId", [])
        if not isinstance(result, str) or not result.startswith("0x"):
            raise ContractPilotError("eth_chainId result is invalid")
        try:
            return int(result, 16)
        except ValueError as exc:
            raise ContractPilotError("eth_chainId result is invalid") from exc

    def get_code(self, address: str) -> str:
        return _hex(
            self._request("eth_getCode", [_address(address), "latest"]),
            "eth_getCode result",
        )

    def eth_call(self, address: str, data: str, *, block: str = "latest") -> str:
        if block != "latest":
            raise ContractPilotError("P3F read plan block must be latest")
        return _hex(
            self._request(
                "eth_call",
                [{"to": _address(address), "data": _hex(data, "eth_call data")}, block],
            ),
            "eth_call result",
        )


def validate_contract_pilot(
    pin: Mapping[str, Any],
    abi_bytes: bytes,
    runtime_code_artifact: bytes | None = None,
) -> list[Any]:
    if pin.get("schema_version") != SCHEMA_VERSION or pin.get("pilot_id") != "pond-reference":
        raise ContractPilotError("contract pilot identity is invalid")
    if pin.get("chain") != {"chain_id": 31337, "name": "Synthetic Pond Chain"}:
        raise ContractPilotError("contract pilot chain identity mismatch")

    contract = pin.get("contract")
    if not isinstance(contract, Mapping):
        raise ContractPilotError("contract binding is missing")
    if (
        _address(contract.get("address")) != EXPECTED_ADDRESS
        or contract.get("name") != "PondReference"
        or contract.get("proxied") is not False
    ):
        raise ContractPilotError("contract binding mismatch")

    source_pin = pin.get("source_pin")
    if not isinstance(source_pin, Mapping):
        raise ContractPilotError("verified source pin is missing")
    expected_source = {
        "verification_provider": "Local Deterministic Build",
        "verification_status": "SYNTHETIC_NOT_DEPLOYED",
        "compiler_version": "ToadAid EVM assembler v1",
        "optimizer_enabled": False,
        "optimizer_runs": 0,
        "official_spec_contract": "PondReference",
        "official_spec_proxied": False,
        "source_document": "scripts/generate_pond_reference_fixture.py",
    }
    for key, expected in expected_source.items():
        if source_pin.get(key) != expected:
            raise ContractPilotError(f"source pin mismatch: {key}")

    runtime_binding = _runtime_code_pin(pin)
    if runtime_code_artifact is not None:
        try:
            baseline = runtime_code_artifact.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise ContractPilotError(
                "runtime code artifact is not UTF-8 hex"
            ) from exc
        normalized, byte_length, digest = _runtime_code_observation(baseline)
        if normalized in {"0x", "0x00"}:
            raise ContractPilotError("runtime code artifact is empty")
        if (
            byte_length != runtime_binding["byte_length"]
            or digest != runtime_binding["sha256"]
        ):
            raise ContractPilotError(
                "runtime code artifact sha256 mismatch"
            )

    abi_binding = pin.get("abi")
    if not isinstance(abi_binding, Mapping) or abi_binding.get("path") != "abi.json":
        raise ContractPilotError("ABI binding is invalid")
    if abi_binding.get("sha256") != sha256(abi_bytes).hexdigest():
        raise ContractPilotError("ABI sha256 mismatch")
    try:
        abi = json.loads(abi_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractPilotError("ABI artifact is invalid") from exc
    if not isinstance(abi, list):
        raise ContractPilotError("ABI artifact must be a list")

    if pin.get("authority") != {
        "knowledge": "KNOWLEDGE_ONLY",
        "read_only": True,
        "write": False,
        "sign": False,
        "send_transaction": False,
    }:
        raise ContractPilotError("authority is not read-only")

    funcs = {
        item["name"]: item
        for item in abi
        if isinstance(item, Mapping)
        and item.get("type") == "function"
        and isinstance(item.get("name"), str)
    }
    seen = set()
    plans = pin.get("read_plans")
    if not isinstance(plans, list) or not plans:
        raise ContractPilotError("read plans are missing")
    for plan in plans:
        if not isinstance(plan, Mapping):
            raise ContractPilotError("read plan is invalid")
        plan_id = plan.get("id")
        if not isinstance(plan_id, str) or not plan_id or plan_id in seen:
            raise ContractPilotError("read plan id is invalid")
        seen.add(plan_id)
        fn = funcs.get(plan.get("function"))
        if fn is None or fn.get("stateMutability") not in {"view", "pure"}:
            raise ContractPilotError("read plan targets non-view function")
        inputs = plan.get("inputs")
        if not isinstance(inputs, list) or any(x != "address" for x in inputs):
            raise ContractPilotError("P3F inputs may only be address")
        if [x.get("type") for x in fn.get("inputs", [])] != inputs:
            raise ContractPilotError("read plan inputs mismatch ABI")

    if pin.get("identity_reads") != {
        "name": "Pond Reference",
        "symbol": "POND",
        "decimals": 18,
    }:
        raise ContractPilotError("identity read pin mismatch")
    return abi


def load_contract_pilot(directory: str | Path) -> tuple[dict[str, Any], list[Any]]:
    root = Path(directory)
    try:
        pin = json.loads((root / "contract-pin.json").read_text(encoding="utf-8"))
        abi_bytes = (root / "abi.json").read_bytes()
        runtime_code_artifact = (root / "runtime-code.hex").read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractPilotError("contract pilot artifacts are unreadable") from exc
    if not isinstance(pin, dict):
        raise ContractPilotError("contract pin must be a mapping")
    return pin, validate_contract_pilot(
        pin,
        abi_bytes,
        runtime_code_artifact,
    )


def _plans(pin: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    raw = pin.get("read_plans")
    if not isinstance(raw, list):
        raise ContractPilotError("read plans missing")
    return {p["id"]: p for p in raw if isinstance(p, Mapping)}


def _encode_address(value: str) -> str:
    return ("0" * 24) + _address(value)[2:]


def build_read_call(
    pin: Mapping[str, Any],
    *,
    plan_id: str,
    arguments: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    plan = _plans(pin).get(plan_id)
    if plan is None:
        raise ContractPilotError(f"unknown read plan: {plan_id}")
    if len(arguments) != len(plan["inputs"]):
        raise ContractPilotError("read plan argument count mismatch")
    data = plan["selector"] + "".join(_encode_address(x) for x in arguments)
    return {
        "plan_id": plan_id,
        "function": plan["function"],
        "to": EXPECTED_ADDRESS,
        "data": data,
        "block": "latest",
        "authority": "READ_ONLY_ETH_CALL",
    }


def _decode_uint(result: str) -> int:
    body = _hex(result, "uint result")[2:]
    if len(body) != 64:
        raise ContractPilotError("uint result must be 32 bytes")
    return int(body, 16)


def _decode_string(result: str) -> str:
    body = bytes.fromhex(_hex(result, "string result")[2:])
    if len(body) < 64 or len(body) % 32:
        raise ContractPilotError("string result has invalid ABI shape")
    offset = int.from_bytes(body[:32], "big")
    length = int.from_bytes(body[offset:offset + 32], "big")
    start = offset + 32
    end = start + length
    if end > len(body):
        raise ContractPilotError("string result is truncated")
    return body[start:end].decode("utf-8")


def execute_read_plan(
    pin: Mapping[str, Any],
    provider: ReadonlyEvmProvider,
    *,
    plan_id: str,
    arguments: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    if provider.chain_id() != EXPECTED_CHAIN_ID:
        raise ContractPilotError("read plan chain mismatch")
    code = _hex(provider.get_code(EXPECTED_ADDRESS), "eth_getCode result")
    code_bytes, code_sha256 = _assert_runtime_code_matches(
        pin,
        code,
        context="read plan",
    )
    call = build_read_call(pin, plan_id=plan_id, arguments=arguments)
    raw = provider.eth_call(call["to"], call["data"], block="latest")
    plan = _plans(pin)[plan_id]
    value = _decode_string(raw) if plan["output"] == "string" else _decode_uint(raw)
    return {
        **call,
        "value": value,
        "knowledge_authority": "KNOWLEDGE_ONLY",
        "runtime_code_sha256": code_sha256,
        "runtime_code_bytes": code_bytes,
    }


def _report(
    pin: Mapping[str, Any],
    condition: str,
    chain_id: int,
    reason: str,
    *,
    code_present: bool = False,
    observed_code_sha256: str | None = None,
    observed_code_bytes: int | None = None,
    observed_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = _runtime_code_pin(pin)
    applicable = condition == APPLICABLE
    return {
        "schema_version": APPLICABILITY_SCHEMA_VERSION,
        "pilot_id": pin["pilot_id"],
        "condition": condition,
        "applicable": applicable,
        "runtime_knowledge_state": (
            RUNTIME_ACTIVE if applicable else RUNTIME_SUSPENDED
        ),
        "implementation_bound_knowledge_usable": applicable,
        "computed_runtime_condition": True,
        "lifecycle_mutated": False,
        "chain_id_expected": EXPECTED_CHAIN_ID,
        "chain_id_observed": chain_id,
        "address": EXPECTED_ADDRESS,
        "code_present": code_present,
        "runtime_code_sha256_expected": runtime["sha256"],
        "runtime_code_sha256_observed": observed_code_sha256,
        "runtime_code_bytes_expected": runtime["byte_length"],
        "runtime_code_bytes_observed": observed_code_bytes,
        "observed_identity": dict(observed_identity or {}),
        "reason": reason,
        "authority": "KNOWLEDGE_ONLY",
    }


def check_live_applicability(
    pin: Mapping[str, Any],
    provider: ReadonlyEvmProvider,
) -> dict[str, Any]:
    """Check provider observations against a synthetic code/identity pin."""
    chain_id = provider.chain_id()
    if chain_id != EXPECTED_CHAIN_ID:
        return _report(pin, CHAIN_MISMATCH, chain_id, "chain id mismatch")

    try:
        code = _hex(provider.get_code(EXPECTED_ADDRESS), "eth_getCode result")
    except Exception:
        return _report(pin, READ_FAILURE, chain_id, "code read failed")
    if code in {"0x", "0x00"}:
        return _report(pin, NO_CODE, chain_id, "no code at pinned address")

    _, code_bytes, code_sha256 = _runtime_code_observation(code)
    runtime = _runtime_code_pin(pin)
    if (
        code_bytes != runtime["byte_length"]
        or code_sha256 != runtime["sha256"]
    ):
        return _report(
            pin,
            CODE_HASH_MISMATCH,
            chain_id,
            "runtime implementation code hash mismatch",
            code_present=True,
            observed_code_sha256=code_sha256,
            observed_code_bytes=code_bytes,
        )

    observed = {}
    try:
        for plan_id in ("name", "symbol", "decimals"):
            call = build_read_call(pin, plan_id=plan_id)
            raw = provider.eth_call(call["to"], call["data"], block="latest")
            observed[plan_id] = (
                _decode_string(raw)
                if _plans(pin)[plan_id]["output"] == "string"
                else _decode_uint(raw)
            )
    except Exception:
        return _report(
            pin,
            READ_FAILURE,
            chain_id,
            "identity read failed",
            code_present=True,
            observed_code_sha256=code_sha256,
            observed_code_bytes=code_bytes,
            observed_identity=observed,
        )

    if observed != pin["identity_reads"]:
        return _report(
            pin,
            IDENTITY_MISMATCH,
            chain_id,
            "identity mismatch",
            code_present=True,
            observed_code_sha256=code_sha256,
            observed_code_bytes=code_bytes,
            observed_identity=observed,
        )
    return _report(
        pin,
        APPLICABLE,
        chain_id,
        "synthetic PondReference implementation and identity match the fixture",
        code_present=True,
        observed_code_sha256=code_sha256,
        observed_code_bytes=code_bytes,
        observed_identity=observed,
    )
