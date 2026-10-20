"""Long-term peer authentication after token pairing.

Every reconnect proves three things over the pinned TLS 1.3 channel:

* the client possesses the 256-bit pairing token;
* the client possesses the private key belonging to its pinned certificate;
* the server possesses the same token and the private key of its pinned cert.

The TLS server certificate is pinned separately by the client, so a network
attacker cannot redirect a known peer.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import Any

from ..constants import (
    AUTH_LABEL_CLIENT,
    AUTH_LABEL_SERVER,
    MAX_CERT_PEM_BYTES,
    TOKEN_BYTES,
)
from ..util import b64decode, b64e, canonical_json_bytes
from .identity import Identity, PeerIdentity


class AuthenticationError(Exception):
    """Generic authentication failure safe to show to a remote peer."""


@dataclass(frozen=True, slots=True)
class AuthTranscript:
    payload: dict[str, Any]
    digest: bytes

    @classmethod
    def build(cls, hello: dict[str, Any], challenge: dict[str, Any]) -> AuthTranscript:
        """Bind both nonces, both identities, certificates and protocol version."""
        client = hello.get("client")
        server = challenge.get("server")
        if not isinstance(client, dict) or not isinstance(server, dict):
            raise AuthenticationError("malformed authentication transcript")
        cert_pem = client.get("cert_pem")
        if not isinstance(cert_pem, str) or len(cert_pem.encode("utf-8")) > MAX_CERT_PEM_BYTES:
            raise AuthenticationError("missing client certificate")
        payload = {
            "domain": "ToRexLink auth v1",
            "protocol": hello.get("v"),
            "client": {
                "id": client.get("id"),
                "name": client.get("name"),
                "platform": client.get("platform"),
                "app_version": client.get("app_version"),
                "nonce": client.get("nonce"),
                "cert_sha256": hashlib.sha256(cert_pem.encode("utf-8")).hexdigest(),
            },
            "server": {
                "id": server.get("id"),
                "name": server.get("name"),
                "platform": server.get("platform"),
                "app_version": server.get("app_version"),
                "nonce": server.get("nonce"),
                "fingerprint": server.get("fingerprint"),
            },
        }
        digest = hashlib.sha256(canonical_json_bytes(payload)).digest()
        return cls(payload=payload, digest=digest)


def _token_proof(token: bytes, label: bytes, digest: bytes) -> bytes:
    if len(token) != TOKEN_BYTES:
        raise AuthenticationError("invalid pairing token length")
    return hmac.new(token, label + b"\x00" + digest, hashlib.sha256).digest()


def client_auth_proof(
    token: bytes,
    identity: Identity,
    transcript: AuthTranscript,
) -> dict[str, str]:
    signature_message = AUTH_LABEL_CLIENT + b"\x00" + transcript.digest
    return {
        "hmac": b64e(_token_proof(token, AUTH_LABEL_CLIENT, transcript.digest)),
        "signature": b64e(identity.sign(signature_message)),
    }


def verify_client_auth(
    token: bytes,
    peer_identity: PeerIdentity,
    transcript: AuthTranscript,
    proof: dict[str, Any],
) -> None:
    if not isinstance(proof, dict):
        raise AuthenticationError("malformed client proof")
    try:
        supplied_hmac = b64decode(proof.get("hmac", ""), expected_length=32)
        signature = b64decode(proof.get("signature", ""), expected_length=384)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("malformed client proof") from exc
    expected = _token_proof(token, AUTH_LABEL_CLIENT, transcript.digest)
    if not hmac.compare_digest(supplied_hmac, expected):
        raise AuthenticationError("invalid credentials")
    signature_message = AUTH_LABEL_CLIENT + b"\x00" + transcript.digest
    if not peer_identity.verify(signature_message, signature):
        raise AuthenticationError("invalid client signature")


def server_auth_proof(
    token: bytes,
    identity: Identity,
    transcript: AuthTranscript,
) -> dict[str, str]:
    signature_message = AUTH_LABEL_SERVER + b"\x00" + transcript.digest
    return {
        "hmac": b64e(_token_proof(token, AUTH_LABEL_SERVER, transcript.digest)),
        "signature": b64e(identity.sign(signature_message)),
    }


def verify_server_auth(
    token: bytes,
    peer_identity: PeerIdentity,
    transcript: AuthTranscript,
    proof: dict[str, Any],
) -> None:
    if not isinstance(proof, dict):
        raise AuthenticationError("malformed server proof")
    try:
        supplied_hmac = b64decode(proof.get("hmac", ""), expected_length=32)
        signature = b64decode(proof.get("signature", ""), expected_length=384)
    except (ValueError, TypeError) as exc:
        raise AuthenticationError("malformed server proof") from exc
    expected = _token_proof(token, AUTH_LABEL_SERVER, transcript.digest)
    if not hmac.compare_digest(supplied_hmac, expected):
        raise AuthenticationError("invalid server authentication")
    signature_message = AUTH_LABEL_SERVER + b"\x00" + transcript.digest
    if not peer_identity.verify(signature_message, signature):
        raise AuthenticationError("invalid server signature")


__all__ = [
    "AuthTranscript",
    "AuthenticationError",
    "client_auth_proof",
    "server_auth_proof",
    "verify_client_auth",
    "verify_server_auth",
]
