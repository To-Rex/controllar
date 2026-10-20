"""Cryptographic identity, pairing and authentication primitives."""

from .auth import (
    AuthTranscript,
    client_auth_proof,
    server_auth_proof,
    verify_client_auth,
    verify_server_auth,
)
from .identity import Identity, PeerIdentity, fingerprint_from_der, normalize_fingerprint
from .pairing_crypto import CPacePeer, PairingKeys, derive_pairing_keys

__all__ = [
    "Identity",
    "PeerIdentity",
    "fingerprint_from_der",
    "normalize_fingerprint",
    "CPacePeer",
    "PairingKeys",
    "derive_pairing_keys",
    "AuthTranscript",
    "client_auth_proof",
    "server_auth_proof",
    "verify_client_auth",
    "verify_server_auth",
]
