"""CPace over NIST P-256 for short-code pairing.

A six digit code is *not* sent on the wire. Both parties derive a generator from
the code and a unique session id, exchange ephemeral curve points, and prove
knowledge of the resulting shared secret. An active network attacker observing
the exchange cannot perform an efficient offline dictionary attack, and failed
attempts are rate-limited by the server.

P-256 point arithmetic is implemented here intentionally: the standard library
and the `cryptography` package do not expose point addition/hash-to-curve. The
implementation is test-vector checked and used only for ephemeral pairing.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..constants import CPACE_DOMAIN, CPACE_KEY_INFO, PAIR_TOKEN_INFO

# NIST P-256 / secp256r1
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = P - 3
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
G = (GX, GY)

Point = tuple[int, int] | None


class CPaceError(ValueError):
    """Malformed or invalid CPace message."""


def _is_on_curve(point: Point) -> bool:
    if point is None:
        return True
    x, y = point
    if not (0 <= x < P and 0 <= y < P):
        return False
    return (y * y - (x * x * x + A * x + B)) % P == 0


def _point_add(left: Point, right: Point) -> Point:
    if left is None:
        return right
    if right is None:
        return left
    x1, y1 = left
    x2, y2 = right
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        slope = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        slope = (y2 - y1) * pow((x2 - x1) % P, -1, P) % P
    x3 = (slope * slope - x1 - x2) % P
    y3 = (slope * (x1 - x3) - y1) % P
    return (x3, y3)


def _scalar_mult(scalar: int, point: Point) -> Point:
    """Fixed-iteration double-and-add.

    Python's big integers are not constant-time, but the scalar is ephemeral,
    never reused, and protects a six-digit one-shot pairing secret. Network
    attempts are capped independently.
    """
    scalar %= N
    if scalar == 0 or point is None:
        return None
    result: Point = None
    addend = point
    for _ in range(256):
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _point_bytes(point: Point) -> bytes:
    if point is None:
        raise CPaceError("point at infinity is not allowed")
    x, y = point
    prefix = b"\x03" if y & 1 else b"\x02"
    return prefix + x.to_bytes(32, "big")


def _point_from_bytes(raw: bytes) -> Point:
    if not isinstance(raw, bytes) or len(raw) != 33 or raw[0] not in (2, 3):
        raise CPaceError("invalid compressed P-256 point")
    x = int.from_bytes(raw[1:], "big")
    rhs = (pow(x, 3, P) + A * x + B) % P
    y = pow(rhs, (P + 1) // 4, P)
    if (y * y) % P != rhs:
        raise CPaceError("point is not on curve")
    if (y & 1) != (raw[0] & 1):
        y = P - y
    point = (x, y)
    if not _is_on_curve(point):
        raise CPaceError("point is not on curve")
    return point


def _hash_to_curve(session_id: bytes, password: bytes) -> Point:
    """Domain-separated try-and-increment map to P-256."""
    if not session_id or not password:
        raise CPaceError("session id and password are required")
    prefix = (
        CPACE_DOMAIN
        + len(session_id).to_bytes(2, "big")
        + session_id
        + len(password).to_bytes(2, "big")
        + password
    )
    for counter in range(1, 256):
        digest = hashlib.sha512(prefix + counter.to_bytes(2, "big")).digest()
        x = int.from_bytes(digest[:32], "big") % P
        rhs = (pow(x, 3, P) + A * x + B) % P
        y = pow(rhs, (P + 1) // 4, P)
        if (y * y) % P != rhs:
            continue
        if digest[32] & 1:
            y = P - y
        point = (x, y)
        if point != (0, 0) and _is_on_curve(point):
            return point
    raise CPaceError("hash-to-curve failed")  # pragma: no cover - cryptographically unreachable


class CPacePeer:
    """One side of a CPace exchange."""

    def __init__(
        self,
        session_id: bytes,
        password: bytes,
        *,
        private_scalar: int | None = None,
        generator: Point | None = None,
    ) -> None:
        if not session_id:
            raise CPaceError("missing session id")
        if not password:
            raise CPaceError("missing pairing code")
        self._session_id = session_id
        self._password = password
        self._generator = generator or _hash_to_curve(session_id, password)
        if not _is_on_curve(self._generator):
            raise CPaceError("invalid generator")
        self._scalar = private_scalar or secrets.randbelow(N - 1) + 1
        if not 0 < self._scalar < N:
            raise CPaceError("invalid ephemeral scalar")
        self._public_point = _scalar_mult(self._scalar, self._generator)
        if self._public_point is None:
            raise CPaceError("invalid public point")

    @property
    def public_bytes(self) -> bytes:
        return _point_bytes(self._public_point)

    def derive_shared(self, peer_public: bytes) -> bytes:
        peer_point = _point_from_bytes(peer_public)
        if peer_point is None:
            raise CPaceError("invalid peer public point")
        shared = _scalar_mult(self._scalar, peer_point)
        if shared is None:
            raise CPaceError("degenerate shared secret")
        return hashlib.sha256(b"CPace-P256-shared\x00" + _point_bytes(shared)).digest()


@dataclass(frozen=True, slots=True)
class PairingKeys:
    token: bytes
    client_confirm_key: bytes
    server_confirm_key: bytes


def derive_pairing_keys(shared_secret: bytes, session_id: bytes) -> PairingKeys:
    if len(shared_secret) != 32 or not session_id:
        raise CPaceError("invalid CPace secret")
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32 * 3,
        salt=session_id,
        info=CPACE_KEY_INFO,
    )
    material = hkdf.derive(shared_secret)
    # The token intentionally uses a second extract/expand context so that even
    # a confirmation-key misuse cannot reveal the long-term pairing token.
    token = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=session_id,
        info=PAIR_TOKEN_INFO,
    ).derive(material[:32])
    return PairingKeys(
        token=token,
        client_confirm_key=material[32:64],
        server_confirm_key=material[64:96],
    )


def pairing_proof(key: bytes, transcript_digest: bytes, role: bytes) -> bytes:
    return hmac.new(key, role + b"\x00" + transcript_digest, hashlib.sha256).digest()


def verify_pairing_proof(
    key: bytes,
    transcript_digest: bytes,
    role: bytes,
    proof: bytes,
) -> bool:
    return hmac.compare_digest(proof, pairing_proof(key, transcript_digest, role))


__all__ = [
    "CPaceError",
    "CPacePeer",
    "PairingKeys",
    "derive_pairing_keys",
    "pairing_proof",
    "verify_pairing_proof",
]
