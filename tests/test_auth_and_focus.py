from __future__ import annotations

import pytest

from torexlink.core.focus import FocusAuthority, FocusError
from torexlink.security.auth import (
    AuthenticationError,
    AuthTranscript,
    client_auth_proof,
    server_auth_proof,
    verify_client_auth,
    verify_server_auth,
)
from torexlink.security.identity import Identity, PeerIdentity


def _transcript() -> AuthTranscript:
    hello = {
        "v": 1,
        "client": {
            "id": "a" * 64,
            "name": "Client",
            "platform": "macos",
            "app_version": "1.0.0",
            "nonce": "bm9uY2U=",
            "cert_pem": "cert",
        },
    }
    challenge = {
        "server": {
            "id": "b" * 64,
            "name": "Server",
            "platform": "windows",
            "app_version": "1.0.0",
            "nonce": "bm9uY2Uy",
            "fingerprint": "AB:CD",
        }
    }
    return AuthTranscript.build(hello, challenge)


def test_auth_transcript_is_deterministic() -> None:
    assert _transcript().digest == _transcript().digest


def test_auth_proofs_and_signatures(tmp_path) -> None:
    client = Identity.load_or_create(tmp_path / "c.pem", tmp_path / "c-key.pem")
    server = Identity.load_or_create(tmp_path / "s.pem", tmp_path / "s-key.pem")
    transcript = _transcript()
    token = b"t" * 32
    client_proof = client_auth_proof(token, client, transcript)
    server_peer = PeerIdentity.from_pem(server.cert_pem)
    client_peer = PeerIdentity.from_pem(client.cert_pem)
    verify_client_auth(token, client_peer, transcript, client_proof)
    with pytest.raises(AuthenticationError):
        verify_client_auth(b"x" * 32, client_peer, transcript, client_proof)
    bad = dict(client_proof)
    bad["signature"] = client_proof["signature"][:-4] + "AAAA"
    with pytest.raises(AuthenticationError):
        verify_client_auth(token, client_peer, transcript, bad)

    proof = server_auth_proof(token, server, transcript)
    verify_server_auth(token, server_peer, transcript, proof)
    with pytest.raises(AuthenticationError):
        verify_server_auth(b"x" * 32, server_peer, transcript, proof)


def test_focus_authority_transitions() -> None:
    host, guest = "a" * 64, "b" * 64
    authority = FocusAuthority(host, guest)
    assert authority.initial().controller == guest
    state = authority.apply(guest, "toggle")
    assert (state.controller, state.target) == (guest, host)
    state = authority.apply(guest, "toggle")
    assert (state.controller, state.target) == (guest, guest)
    state = authority.apply(guest, "edge", edge="right", position=0.3)
    assert (state.controller, state.target) == (guest, host)
    assert state.entry_edge == "left"
    state = authority.apply(host, "takeover")
    assert (state.controller, state.target) == (host, host)
    state = authority.apply(guest, "toggle")
    assert (state.controller, state.target) == (guest, guest)
    with pytest.raises(FocusError):
        authority.apply(host, "edge", edge="right", position=0.5)
    with pytest.raises(FocusError):
        authority.apply("x" * 64, "toggle")
    with pytest.raises(FocusError):
        authority.apply(guest, "return")
