"""Handshake message validation and transcript construction."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from typing import Any

from ..constants import (
    APP_VERSION,
    MAX_CERT_PEM_BYTES,
    MAX_DEVICE_NAME,
    NONCE_BYTES,
    PROTOCOL_VERSION,
)
from ..security.identity import Identity, PeerIdentity, normalize_fingerprint
from ..util import b64decode, b64e, canonical_json_bytes
from .models import ProtocolError

_HANDSHAKE_TYPES = {
    "hello",
    "pair_challenge",
    "pair_response",
    "pair_confirmed",
    "auth_challenge",
    "auth_response",
    "auth_ok",
    "auth_failed",
    "error",
}
_CODE_RE = re.compile(r"^\d{6}$")
_ID_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(slots=True)
class ClientHello:
    raw: dict[str, Any]
    mode: str
    peer_identity: PeerIdentity
    name: str
    platform: str
    app_version: str
    nonce: bytes

    @property
    def device_id(self) -> str:
        return self.peer_identity.device_id


@dataclass(slots=True)
class ServerDescriptor:
    identity: Identity
    name: str
    platform: str
    nonce: bytes
    app_version: str = APP_VERSION

    def as_dict(self) -> dict[str, str]:
        return {
            "id": self.identity.device_id,
            "name": self.name,
            "platform": self.platform,
            "app_version": self.app_version,
            "nonce": b64e(self.nonce),
            "fingerprint": self.identity.fingerprint,
        }


def clean_device_name(value: object) -> str:
    if not isinstance(value, str):
        return "ToRexLink"
    cleaned = " ".join(value.replace("\x00", " ").split())
    return (cleaned or "ToRexLink")[:MAX_DEVICE_NAME]


def clean_platform(value: object) -> str:
    if not isinstance(value, str):
        return "unknown"
    cleaned = "".join(char for char in value.lower() if char in "abcdefghijklmnopqrstuvwxyz_")
    return cleaned[:32] or "unknown"


def _validate_nonce(value: object, field: str = "nonce") -> bytes:
    if not isinstance(value, str):
        raise ProtocolError(f"missing {field}")
    try:
        nonce = b64decode(value, expected_length=NONCE_BYTES)
    except ValueError as exc:
        raise ProtocolError(f"invalid {field}") from exc
    return nonce


def build_hello(identity: Identity, name: str, platform: str, mode: str) -> dict[str, Any]:
    if mode not in {"pair", "auth"}:
        raise ValueError("invalid hello mode")
    return {
        "v": PROTOCOL_VERSION,
        "type": "hello",
        "mode": mode,
        "client": {
            "id": identity.device_id,
            "name": clean_device_name(name),
            "platform": clean_platform(platform),
            "app_version": APP_VERSION,
            "nonce": b64e(secrets.token_bytes(NONCE_BYTES)),
            "cert_pem": identity.cert_pem,
        },
    }


def parse_hello(message: dict[str, Any], *, expected_mode: str | None = None) -> ClientHello:
    if not isinstance(message, dict):
        raise ProtocolError("hello must be an object")
    if message.get("v") != PROTOCOL_VERSION or message.get("type") != "hello":
        raise ProtocolError("expected hello")
    mode = message.get("mode")
    if mode not in {"pair", "auth"}:
        raise ProtocolError("unsupported hello mode")
    if expected_mode is not None and mode != expected_mode:
        raise ProtocolError("unexpected hello mode")
    client = message.get("client")
    if not isinstance(client, dict):
        raise ProtocolError("missing client descriptor")
    cert_pem = client.get("cert_pem")
    if not isinstance(cert_pem, str):
        raise ProtocolError("missing client certificate")
    try:
        if len(cert_pem.encode("utf-8")) > MAX_CERT_PEM_BYTES:
            raise ProtocolError("client certificate too large")
        peer_identity = PeerIdentity.from_pem(cert_pem)
    except ValueError as exc:
        raise ProtocolError("invalid client certificate") from exc
    claimed_id = client.get("id")
    if not isinstance(claimed_id, str) or not _ID_RE.fullmatch(claimed_id):
        raise ProtocolError("invalid client id")
    if claimed_id != peer_identity.device_id:
        raise ProtocolError("client certificate does not match id")
    app_version = client.get("app_version")
    if not isinstance(app_version, str) or len(app_version) > 32:
        raise ProtocolError("invalid app version")
    return ClientHello(
        raw=message,
        mode=mode,
        peer_identity=peer_identity,
        name=clean_device_name(client.get("name")),
        platform=clean_platform(client.get("platform")),
        app_version=app_version,
        nonce=_validate_nonce(client.get("nonce"), "client nonce"),
    )


def build_pair_challenge(
    descriptor: ServerDescriptor,
    *,
    session_id: bytes,
    cpace_public: bytes,
    expires_at: int,
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "pair_challenge",
        "server": descriptor.as_dict(),
        "sid": b64e(session_id),
        "cpace_pub": b64e(cpace_public),
        "expires_at": expires_at,
        "issued_at": int(__import__("time").time()),
    }


def build_auth_challenge(descriptor: ServerDescriptor) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "auth_challenge",
        "server": descriptor.as_dict(),
        "issued_at": int(__import__("time").time()),
    }


def validate_server_challenge(
    message: dict[str, Any],
    *,
    expected_type: str,
    expected_fingerprint: str | None = None,
    actual_tls_cert_der: bytes,
    expected_device_id: str | None = None,
) -> tuple[dict[str, Any], bytes]:
    """Validate a challenge against the certificate observed on the TLS socket.

    Returns the server descriptor and (for pair challenges) the session id.
    """
    if not isinstance(message, dict):
        raise ProtocolError("challenge must be an object")
    if message.get("v") != PROTOCOL_VERSION or message.get("type") != expected_type:
        raise ProtocolError("unexpected challenge")
    server = message.get("server")
    if not isinstance(server, dict):
        raise ProtocolError("missing server descriptor")
    actual_hash = hashlib.sha256(actual_tls_cert_der).hexdigest()
    claimed = server.get("fingerprint")
    try:
        claimed_norm = normalize_fingerprint(claimed) if isinstance(claimed, str) else ""
    except ValueError as exc:
        raise ProtocolError("invalid server fingerprint") from exc
    if claimed_norm.replace(":", "") != actual_hash:
        raise ProtocolError("server certificate fingerprint mismatch")
    if expected_fingerprint is not None:
        pinned = normalize_fingerprint(expected_fingerprint)
        if not hmac_compare_hex(pinned, actual_hash):
            raise ProtocolError("server certificate does not match pinned QR fingerprint")
    claimed_id = server.get("id")
    if claimed_id != actual_hash:
        raise ProtocolError("server id does not match certificate")
    if expected_device_id and claimed_id != expected_device_id:
        raise ProtocolError("unexpected server device")
    app_version = server.get("app_version")
    if not isinstance(app_version, str) or len(app_version) > 32:
        raise ProtocolError("invalid server app version")
    _validate_nonce(server.get("nonce"), "server nonce")
    session_id = b""
    if expected_type == "pair_challenge":
        if not isinstance(message.get("sid"), str):
            raise ProtocolError("missing pairing session id")
        try:
            session_id = b64decode(message["sid"])
        except ValueError as exc:
            raise ProtocolError("invalid pairing session id") from exc
        if len(session_id) != 32:
            raise ProtocolError("invalid pairing session id length")
        if not isinstance(message.get("cpace_pub"), str):
            raise ProtocolError("missing CPace public key")
        try:
            cpace_public = b64decode(message["cpace_pub"])
        except ValueError as exc:
            raise ProtocolError("invalid CPace public key") from exc
        if len(cpace_public) != 33:
            raise ProtocolError("invalid CPace public key length")
        expires_at = message.get("expires_at")
        if isinstance(expires_at, bool) or not isinstance(expires_at, int):
            raise ProtocolError("invalid expiry")
        server["cpace_pub_bytes"] = cpace_public
    return server, session_id


def hmac_compare_hex(left_hex: str, right_hex: str) -> bool:
    import hmac

    return hmac.compare_digest(left_hex.lower().replace(":", ""), right_hex.lower())


def pair_transcript_payload(
    hello: ClientHello,
    server: dict[str, Any],
    *,
    session_id: bytes,
    client_cpace_public: bytes,
    server_cpace_public: bytes,
) -> dict[str, Any]:
    return {
        "domain": "ToRexLink pairing v1",
        "v": PROTOCOL_VERSION,
        "sid": b64e(session_id),
        "client": {
            "id": hello.device_id,
            "name": hello.name,
            "platform": hello.platform,
            "app_version": hello.app_version,
            "nonce": b64e(hello.nonce),
            "cert_sha256": hashlib.sha256(hello.peer_identity.cert_pem.encode()).hexdigest(),
        },
        "server": {
            "id": server.get("id"),
            "name": server.get("name"),
            "platform": server.get("platform"),
            "app_version": server.get("app_version"),
            "nonce": server.get("nonce"),
            "fingerprint": server.get("fingerprint"),
        },
        "client_cpace_pub": b64e(client_cpace_public),
        "server_cpace_pub": b64e(server_cpace_public),
    }


def pair_transcript_digest(payload: dict[str, Any]) -> bytes:
    return hashlib.sha256(canonical_json_bytes(payload)).digest()


def pairing_session_id(server_nonce: bytes, client_nonce: bytes, client_id: str) -> bytes:
    if len(server_nonce) != NONCE_BYTES or len(client_nonce) != NONCE_BYTES:
        raise ValueError("invalid nonce")
    material = (
        b"ToRexLink pairing sid v1\x00"
        + server_nonce
        + client_nonce
        + bytes.fromhex(client_id)
    )
    return hashlib.sha256(material).digest()


def build_pair_response(
    *,
    cpace_public: bytes,
    proof: bytes,
    signature: bytes,
) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "pair_response",
        "cpace_pub": b64e(cpace_public),
        "proof": b64e(proof),
        "signature": b64e(signature),
    }


def build_auth_response(proof: dict[str, str]) -> dict[str, Any]:
    return {
        "v": PROTOCOL_VERSION,
        "type": "auth_response",
        "proof": proof,
    }


def protocol_error(code: str) -> dict[str, Any]:
    return {"v": PROTOCOL_VERSION, "type": "error", "code": code}


__all__ = [
    "ClientHello",
    "ServerDescriptor",
    "build_auth_challenge",
    "build_auth_response",
    "build_hello",
    "build_pair_challenge",
    "build_pair_response",
    "clean_device_name",
    "clean_platform",
    "hmac_compare_hex",
    "pair_transcript_digest",
    "pair_transcript_payload",
    "pairing_session_id",
    "parse_hello",
    "protocol_error",
    "validate_server_challenge",
]
