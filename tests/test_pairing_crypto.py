from __future__ import annotations

import os

import pytest

from torexlink.security.pairing_crypto import (
    CPaceError,
    CPacePeer,
    G,
    N,
    _is_on_curve,
    _point_add,
    _point_bytes,
    _point_from_bytes,
    _scalar_mult,
    derive_pairing_keys,
    pairing_proof,
    verify_pairing_proof,
)


def test_p256_base_point_order_and_doubling() -> None:
    assert _is_on_curve(G)
    assert _scalar_mult(N, G) is None
    assert _scalar_mult(2, G) == _point_add(G, G)
    encoded = _point_bytes(G)
    assert len(encoded) == 33
    assert _point_from_bytes(encoded) == G


def test_cpace_peers_agree_and_proofs_verify() -> None:
    sid = os.urandom(32)
    code = b"123456"
    alice = CPacePeer(sid, code)
    bob = CPacePeer(sid, code)
    alice_shared = alice.derive_shared(bob.public_bytes)
    bob_shared = bob.derive_shared(alice.public_bytes)
    assert alice_shared == bob_shared
    keys_a = derive_pairing_keys(alice_shared, sid)
    keys_b = derive_pairing_keys(bob_shared, sid)
    assert keys_a.token == keys_b.token
    digest = os.urandom(32)
    proof = pairing_proof(keys_a.client_confirm_key, digest, b"client")
    assert verify_pairing_proof(keys_b.client_confirm_key, digest, b"client", proof)
    assert not verify_pairing_proof(keys_b.client_confirm_key, digest, b"server", proof)


def test_cpace_wrong_code_does_not_agree() -> None:
    sid = os.urandom(32)
    alice = CPacePeer(sid, b"123456")
    bob = CPacePeer(sid, b"654321")
    assert alice.derive_shared(bob.public_bytes) != bob.derive_shared(alice.public_bytes)


def test_cpace_rejects_invalid_points() -> None:
    peer = CPacePeer(os.urandom(32), b"123456")
    with pytest.raises(CPaceError):
        peer.derive_shared(b"\x04" + b"\x00" * 32)
    with pytest.raises(CPaceError):
        peer.derive_shared(b"\x04" + b"\x01" * 32)


def test_derived_key_is_domain_separated(tmp_path) -> None:
    sid = b"s" * 32
    shared = b"x" * 32
    first = derive_pairing_keys(shared, sid)
    second = derive_pairing_keys(shared, b"t" * 32)
    assert first.token != second.token
    assert len(first.token) == 32
