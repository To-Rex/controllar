"""Post-pairing authenticated reconnect over pinned TLS."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass

from ..constants import APP_VERSION, PROTOCOL_VERSION
from ..peers import PeerRecord
from ..protocol.handshake import (
    ClientHello,
    ServerDescriptor,
    build_auth_challenge,
    build_auth_response,
    build_hello,
    validate_server_challenge,
)
from ..security.auth import (
    AuthenticationError,
    AuthTranscript,
    client_auth_proof,
    server_auth_proof,
    verify_client_auth,
    verify_server_auth,
)
from ..security.identity import Identity, PeerIdentity
from .transport import (
    SecureStream,
    TransportError,
    open_secure_stream,
    peer_certificate_der,
    pinned_client_ssl_context,
    receive_hello,
)

log = logging.getLogger(__name__)


class ReconnectAuthError(Exception):
    """Credentials or peer identity were rejected.

    ``permanent`` means retrying the same credentials is unlikely to help
    (wrong token, changed certificate, explicit rejection by the host).
    """

    def __init__(self, message: str, *, permanent: bool = False) -> None:
        super().__init__(message)
        self.permanent = permanent


@dataclass(slots=True)
class AuthenticatedLink:
    stream: SecureStream
    peer_identity: PeerIdentity
    peer_name: str
    peer_platform: str
    peer_app_version: str
    address: str
    port: int


async def server_auth_exchange(
    stream: SecureStream,
    hello: ClientHello,
    record: PeerRecord,
    descriptor: ServerDescriptor,
) -> AuthenticatedLink:
    if hello.device_id != record.peer_id:
        raise ReconnectAuthError("unexpected device")
    try:
        stored_identity = record.identity
        if hello.peer_identity.cert_pem != stored_identity.cert_pem:
            raise ReconnectAuthError("client certificate changed", permanent=True)
        token = record.token
    except ValueError as exc:
        raise ReconnectAuthError("stored credentials are invalid", permanent=True) from exc
    await stream.send(build_auth_challenge(descriptor))
    try:
        async with asyncio.timeout(8.0):
            response = await stream.recv()
    except (TimeoutError, TransportError, ValueError) as exc:
        raise ReconnectAuthError("authentication timeout") from exc
    if response.get("v") != PROTOCOL_VERSION or response.get("type") != "auth_response":
        raise ReconnectAuthError("invalid authentication response")
    proof = response.get("proof")
    if not isinstance(proof, dict):
        raise ReconnectAuthError("missing authentication proof")
    try:
        transcript = AuthTranscript.build(hello.raw, build_auth_challenge(descriptor))
        verify_client_auth(token, stored_identity, transcript, proof)
    except (AuthenticationError, ValueError) as exc:
        raise ReconnectAuthError("authentication failed", permanent=True) from exc
    server_proof = server_auth_proof(token, descriptor.identity, transcript)
    await stream.send(
        {
            "v": PROTOCOL_VERSION,
            "type": "auth_ok",
            "proof": server_proof,
        }
    )
    return AuthenticatedLink(
        stream=stream,
        peer_identity=stored_identity,
        peer_name=hello.name,
        peer_platform=hello.platform,
        peer_app_version=hello.app_version,
        address=stream.peername[0],
        port=stream.peername[1],
    )


async def client_auth_exchange(
    peer: PeerRecord,
    identity: Identity,
    *,
    name: str,
    platform: str,
    host: str | None = None,
    port: int | None = None,
) -> AuthenticatedLink:
    if not peer.outbound:
        raise ReconnectAuthError("peer is not configured for outbound connection", permanent=True)
    address = host or peer.last_address
    target_port = port or peer.last_port
    if not address or not target_port:
        raise ReconnectAuthError("saved peer address is missing", permanent=True)
    try:
        stored_identity = peer.identity
        token = peer.token
    except ValueError as exc:
        raise ReconnectAuthError("stored credentials are invalid", permanent=True) from exc
    try:
        context = pinned_client_ssl_context(peer.cert_pem)
        stream = await open_secure_stream(address, target_port, context=context)
    except TransportError as exc:
        raise ReconnectAuthError("host is unreachable") from exc
    try:
        actual_der = peer_certificate_der(stream.writer)
        actual_id = hashlib.sha256(actual_der).hexdigest()
        if actual_id != peer.peer_id:
            raise ReconnectAuthError("pinned certificate mismatch", permanent=True)
        hello = build_hello(identity, name, platform, "auth")
        await stream.send(hello)
        challenge = await receive_hello(stream)
        if challenge.get("type") == "error":
            raise ReconnectAuthError("host rejected authentication", permanent=True)
        server, _ = validate_server_challenge(
            challenge,
            expected_type="auth_challenge",
            expected_fingerprint=peer.fingerprint,
            actual_tls_cert_der=actual_der,
            expected_device_id=peer.peer_id,
        )
        transcript = AuthTranscript.build(hello, challenge)
        proof = client_auth_proof(token, identity, transcript)
        await stream.send(build_auth_response(proof))
        try:
            async with asyncio.timeout(8.0):
                ok = await stream.recv()
        except (TimeoutError, TransportError, ValueError) as exc:
            raise ReconnectAuthError("authentication confirmation timeout") from exc
        if ok.get("type") == "error":
            raise ReconnectAuthError("host rejected credentials", permanent=True)
        if ok.get("v") != PROTOCOL_VERSION or ok.get("type") != "auth_ok":
            raise ReconnectAuthError("unexpected authentication response", permanent=True)
        server_proof = ok.get("proof")
        if not isinstance(server_proof, dict):
            raise ReconnectAuthError("missing host authentication proof", permanent=True)
        verify_server_auth(token, stored_identity, transcript, server_proof)
        return AuthenticatedLink(
            stream=stream,
            peer_identity=stored_identity,
            peer_name=peer.name,
            peer_platform=peer.platform,
            # Use the LIVE server hello version, not the stale value stored
            # when the peer was paired. The stored record is only a fallback.
            peer_app_version=str(server.get("app_version") or peer.app_version or APP_VERSION),
            address=address,
            port=target_port,
        )
    except ReconnectAuthError:
        await stream.close()
        raise
    except asyncio.CancelledError:
        await stream.close()
        raise
    except (AuthenticationError, TransportError, ValueError, OSError) as exc:
        await stream.close()
        raise ReconnectAuthError("secure authentication failed") from exc


def auth_failure_throttle_key(peer_name: str) -> str:
    """Deliberately opaque key for rate-limit logs/metrics."""
    return hashlib.sha256(peer_name.encode("utf-8", "replace")).hexdigest()[:12]


def unix_time() -> int:
    return int(time.time())


__all__ = [
    "AuthenticatedLink",
    "ReconnectAuthError",
    "client_auth_exchange",
    "server_auth_exchange",
]
