"""Strict, dependency-free P-256 verification for Codex device proofs."""

from __future__ import annotations

import base64
from hashlib import sha256
import re


class CodexP256Error(ValueError):
    """The credential or proof is malformed or does not verify."""


_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
)
_P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
_A = _P - 3
_B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_G = (
    0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296,
    0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5,
)


def _decode(value: str, label: str) -> bytes:
    if not isinstance(value, str) or not value or not _B64URL.fullmatch(value):
        raise CodexP256Error(f"{label} is not unpadded base64url")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise CodexP256Error(f"{label} is not unpadded base64url") from exc
    encoded = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if encoded != value:
        raise CodexP256Error(f"{label} is not canonical base64url")
    return decoded


def _integer(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 2 > len(data) or data[offset] != 0x02:
        raise CodexP256Error("signature is not canonical DER")
    length = data[offset + 1]
    start = offset + 2
    end = start + length
    if length == 0 or end > len(data):
        raise CodexP256Error("signature is not canonical DER")
    raw = data[start:end]
    if raw[0] & 0x80 or (len(raw) > 1 and raw[0] == 0 and not raw[1] & 0x80):
        raise CodexP256Error("signature is not canonical DER")
    return int.from_bytes(raw, "big"), end


def _signature(value: str) -> tuple[int, int]:
    data = _decode(value, "signature")
    if len(data) < 8 or data[0] != 0x30 or data[1] != len(data) - 2:
        raise CodexP256Error("signature is not canonical DER")
    r, offset = _integer(data, 2)
    s, offset = _integer(data, offset)
    if offset != len(data) or not (1 <= r < _N and 1 <= s < _N):
        raise CodexP256Error("signature values are outside P-256 range")
    return r, s


def _public_key(value: str) -> tuple[int, int]:
    data = _decode(value, "public key")
    if len(data) != len(_SPKI_PREFIX) + 64 or not data.startswith(_SPKI_PREFIX):
        raise CodexP256Error("public key is not P-256 SubjectPublicKeyInfo DER")
    point = (
        int.from_bytes(data[-64:-32], "big"),
        int.from_bytes(data[-32:], "big"),
    )
    x, y = point
    if x >= _P or y >= _P or (y * y - (x * x * x + _A * x + _B)) % _P:
        raise CodexP256Error("public key point is not on P-256")
    return point


def _add(
    left: tuple[int, int] | None, right: tuple[int, int] | None
) -> tuple[int, int] | None:
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return None
        slope = (3 * x1 * x1 + _A) * pow(2 * y1, -1, _P) % _P
    else:
        slope = (y2 - y1) * pow(x2 - x1, -1, _P) % _P
    x3 = (slope * slope - x1 - x2) % _P
    return x3, (slope * (x1 - x3) - y1) % _P


def _multiply(scalar: int, point: tuple[int, int]) -> tuple[int, int] | None:
    result = None
    addend: tuple[int, int] | None = point
    while scalar:
        if scalar & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        scalar >>= 1
    return result


def verify_p256_sha256(
    challenge_base64url: str,
    signature_base64url: str,
    public_key_spki_base64url: str,
) -> None:
    """Verify the Codex P-256/SHA-256 proof or raise ``CodexP256Error``."""

    challenge = _decode(challenge_base64url, "challenge")
    if not 1 <= len(challenge) <= 4096:
        raise CodexP256Error("challenge length is outside the Codex contract")
    r, s = _signature(signature_base64url)
    public_key = _public_key(public_key_spki_base64url)
    digest = int.from_bytes(sha256(challenge).digest(), "big")
    inverse = pow(s, -1, _N)
    point = _add(
        _multiply(digest * inverse % _N, _G),
        _multiply(r * inverse % _N, public_key),
    )
    if point is None or point[0] % _N != r:
        raise CodexP256Error("Codex device proof signature did not verify")
