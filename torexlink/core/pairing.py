"""Pairing offer lifecycle and the CPace-over-TLS exchange."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cryptography import x509

from ..constants import (
    APP_VERSION,
    PAIRING_FAILURE_DELAY,
    PAIRING_MAX_ATTEMPTS,
    PAIRING_TTL_SECONDS,
    PROTOCOL_VERSION,
)
from ..pairing_payload import PairingPayload
from ..protocol.handshake import (
    ClientHello,
    ServerDescriptor,
    build_hello,
    build_pair_challenge,
    build_pair_response,
    pair_transcript_digest,
    pair_transcript_payload,
    pairing_session_id,
    parse_hello,
    protocol_error,
    validate_server_challenge,
)
from ..security.identity import Identity, PeerIdentity, certificate_to_pem
from ..security.pairing_crypto import (
    CPaceError,
    CPacePeer,
    derive_pairing_keys,
    pairing_proof,
    verify_pairing_proof,
)
from ..util import b64decode, b64e
from .transport import (
    SecureStream,
    TransportError,
    open_secure_stream,
    pairing_client_ssl_context,
    peer_certificate_der,
    receive_hello,
)

log = logging.getLogger(__name__)

PAIR_CLIENT_SIGNATURE_LABEL = b"ToRexLink-Pair-Client-Signature-v1"
PAIR_SERVER_PROOF_ROLE = b"server"
PAIR_CLIENT_PROOF_ROLE = b"client"


class PairingError(Exception):
    """Pairing failed. Messages are safe for UI display.

    ``retryable`` is limited to failures that happened before a pairing
    challenge was consumed, where trying the host's next address is useful.
    """

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(slots=True)
class PairingOffer:
    code: str
    device_id: str
    fingerprint: str
    name: str
    addresses: tuple[str, ...]
    port: int
    created_at: float
    expires_at: float
    attempts_remaining: int = PAIRING_MAX_ATTEMPTS
    attempt_active: bool = False
    completed: bool = False
    failed: bool = False

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self.expires_at or self.failed

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    def payload(self) -> PairingPayload:
        return PairingPayload(
            code=self.code,
            fingerprint=self.fingerprint,
            device_id=self.device_id,
            name=self.name,
            port=self.port,
            addresses=self.addresses,
        )


@dataclass(slots=True)
class PairResult:
    identity: PeerIdentity
    name: str
    platform: str
    token: bytes
    app_version: str | None
    address: str
    port: int


@dataclass(slots=True)
class PairingOfferManager:
    """Short-lived, single-use code protected by attempt and concurrency limits."""

    _offer: PairingOffer | None = None
    _on_change: Callable[[PairingOffer | None], None] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def offer(self) -> PairingOffer | None:
        if self._offer is not None and self._offer.expired:
            return None
        return self._offer

    def set_listener(self, callback: Callable[[PairingOffer | None], None]) -> None:
        self._on_change = callback

    def create(
        self,
        *,
        device_id: str,
        fingerprint: str,
        name: str,
        addresses: tuple[str, ...] | list[str],
        port: int,
    ) -> PairingOffer:
        code = f"{secrets.randbelow(10**6):06d}"
        now = time.monotonic()
        offer = PairingOffer(
            code=code,
            device_id=device_id,
            fingerprint=fingerprint,
            name=name,
            addresses=tuple(addresses),
            port=port,
            created_at=now,
            expires_at=now + PAIRING_TTL_SECONDS,
        )
        self._offer = offer
        self._notify()
        return offer

    def cancel(self) -> None:
        if self._offer is not None:
            self._offer.failed = True
        self._offer = None
        self._notify()

    async def begin_attempt(self) -> PairingOffer | None:
        async with self._lock:
            offer = self.offer
            if offer is None or offer.attempt_active or offer.completed:
                return None
            if offer.attempts_remaining <= 0:
                return None
            offer.attempt_active = True
            return offer

    async def fail_attempt(self) -> None:
        async with self._lock:
            offer = self._offer
            if offer is None:
                return
            offer.attempt_active = False
            offer.attempts_remaining = max(0, offer.attempts_remaining - 1)
            if offer.attempts_remaining <= 0:
                offer.failed = True
                self._offer = None
        self._notify()

    async def complete_attempt(self) -> None:
        async with self._lock:
            if self._offer is not None:
                self._offer.attempt_active = False
                self._offer.completed = True
            self._offer = None
        self._notify()

    async def release_attempt(self) -> None:
        async with self._lock:
            if self._offer is not None:
                self._offer.attempt_active = False

    def _notify(self) -> None:
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change(self.offer)


def _signature_message(digest: bytes) -> bytes:
    return PAIR_CLIENT_SIGNATURE_LABEL + b"\x00" + digest


async def _safe_send(stream: SecureStream, payload: dict[str, Any]) -> None:
    with contextlib.suppress(Exception):
        await stream.send(payload)


async def server_pair_exchange(
    stream: SecureStream,
    hello: ClientHello,
    offer_manager: PairingOfferManager,
    descriptor: ServerDescriptor,
    *,
    on_paired: Callable[[PairResult], None],
) -> PairResult | None:
    """Run the server half of one pairing attempt.

    Returns a result only on success. Persistent storage is performed through
    ``on_paired`` before the server confirms, so both peers cannot disagree
    about whether pairing succeeded barring a network failure at the last
    instant (in which case the host retains a removable pairing).
    """
    offer = await offer_manager.begin_attempt()
    if offer is None:
        await _safe_send(stream, protocol_error("pair_unavailable"))
        return None
    try:
        server_nonce = descriptor.nonce
        sid = pairing_session_id(server_nonce, hello.nonce, hello.device_id)
        cpace = CPacePeer(sid, offer.code.encode("ascii"))
        expires_at = int(time.time() + PAIRING_TTL_SECONDS)
        await stream.send(
            build_pair_challenge(
                descriptor,
                session_id=sid,
                cpace_public=cpace.public_bytes,
                expires_at=expires_at,
            )
        )
        try:
            async with asyncio.timeout(8.0):
                response = await stream.recv()
        except (TimeoutError, TransportError, ValueError) as exc:
            raise PairingError("pairing response timeout") from exc
        if response.get("v") != PROTOCOL_VERSION or response.get("type") != "pair_response":
            raise PairingError("invalid pairing response")
        try:
            client_public = b64decode(response.get("cpace_pub", ""), expected_length=33)
            proof = b64decode(response.get("proof", ""), expected_length=32)
            signature = b64decode(response.get("signature", ""), expected_length=384)
        except (ValueError, TypeError) as exc:
            raise PairingError("malformed pairing proof") from exc
        try:
            shared = cpace.derive_shared(client_public)
        except CPaceError as exc:
            raise PairingError("invalid CPace key") from exc
        keys = derive_pairing_keys(shared, sid)
        transcript = pair_transcript_payload(
            hello,
            descriptor.as_dict(),
            session_id=sid,
            client_cpace_public=client_public,
            server_cpace_public=cpace.public_bytes,
        )
        digest = pair_transcript_digest(transcript)
        if not verify_pairing_proof(
            keys.client_confirm_key,
            digest,
            PAIR_CLIENT_PROOF_ROLE,
            proof,
        ):
            raise PairingError("incorrect pairing code")
        if not hello.peer_identity.verify(_signature_message(digest), signature):
            raise PairingError("invalid client identity signature")
        result = PairResult(
            identity=hello.peer_identity,
            name=hello.name,
            platform=hello.platform,
            token=keys.token,
            app_version=hello.app_version,
            address=stream.peername[0],
            port=stream.peername[1],
        )
        on_paired(result)
        server_proof = pairing_proof(keys.server_confirm_key, digest, PAIR_SERVER_PROOF_ROLE)
        await stream.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "pair_confirmed",
                "proof": b64e(server_proof),
            }
        )
        await offer_manager.complete_attempt()
        return result
    except PairingError:
        await offer_manager.fail_attempt()
        await _safe_send(stream, protocol_error("pair_denied"))
        await asyncio.sleep(PAIRING_FAILURE_DELAY)
        raise
    except (OSError, ConnectionError, TransportError, ValueError, EOFError):
        await offer_manager.fail_attempt()
        raise
    finally:
        if offer_manager.offer is not None and offer_manager.offer.attempt_active:
            await offer_manager.release_attempt()


async def client_pair_exchange(
    host: str,
    port: int,
    code: str,
    *,
    identity: Identity,
    name: str,
    platform: str,
    expected_fingerprint: str | None = None,
    expected_device_id: str | None = None,
) -> PairResult:
    if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
        raise PairingError("pairing code must contain 6 digits")
    context = pairing_client_ssl_context()
    try:
        stream = await open_secure_stream(host, port, context=context)
    except TransportError as exc:
        raise PairingError("host is unreachable", retryable=True) from exc
    try:
        actual_der = peer_certificate_der(stream.writer)
        hello = build_hello(identity, name, platform, "pair")
        parsed_hello = parse_hello(hello, expected_mode="pair")
        await stream.send(hello)
        challenge = await receive_hello(stream)
        if challenge.get("type") == "error":
            raise PairingError("pairing is not available on the host")
        server, sid = validate_server_challenge(
            challenge,
            expected_type="pair_challenge",
            expected_fingerprint=expected_fingerprint,
            actual_tls_cert_der=actual_der,
            expected_device_id=expected_device_id,
        )
        server_public = server.get("cpace_pub_bytes")
        if not isinstance(server_public, bytes) or len(server_public) != 33:
            raise PairingError("invalid host CPace key")
        cpace = CPacePeer(sid, code.encode("ascii"))
        try:
            shared = cpace.derive_shared(server_public)
        except CPaceError as exc:
            raise PairingError("invalid host CPace key") from exc
        keys = derive_pairing_keys(shared, sid)
        transcript = pair_transcript_payload(
            parsed_hello,
            server,
            session_id=sid,
            client_cpace_public=cpace.public_bytes,
            server_cpace_public=server_public,
        )
        digest = pair_transcript_digest(transcript)
        signature = identity.sign(_signature_message(digest))
        proof = pairing_proof(keys.client_confirm_key, digest, PAIR_CLIENT_PROOF_ROLE)
        await stream.send(
            build_pair_response(
                cpace_public=cpace.public_bytes,
                proof=proof,
                signature=signature,
            )
        )
        try:
            async with asyncio.timeout(8.0):
                confirmed = await stream.recv()
        except (TimeoutError, TransportError, ValueError) as exc:
            raise PairingError("pairing confirmation timeout") from exc
        if confirmed.get("type") == "error":
            raise PairingError("incorrect pairing code or expired pairing")
        if confirmed.get("v") != PROTOCOL_VERSION or confirmed.get("type") != "pair_confirmed":
            raise PairingError("unexpected pairing response")
        try:
            server_proof = b64decode(confirmed.get("proof", ""), expected_length=32)
        except (ValueError, TypeError) as exc:
            raise PairingError("malformed server confirmation") from exc
        if not verify_pairing_proof(
            keys.server_confirm_key,
            digest,
            PAIR_SERVER_PROOF_ROLE,
            server_proof,
        ):
            raise PairingError("host could not prove the pairing code")
        server_cert_pem = certificate_to_pem(x509.load_der_x509_certificate(actual_der))
        server_identity = PeerIdentity.from_pem(server_cert_pem)
        return PairResult(
            identity=server_identity,
            name=str(server.get("name") or "ToRexLink")[:64],
            platform=str(server.get("platform") or "unknown")[:32],
            token=keys.token,
            app_version=str(server.get("app_version") or APP_VERSION)[:32],
            address=host,
            port=port,
        )
    except PairingError:
        raise
    except (TransportError, ValueError, OSError) as exc:
        raise PairingError("secure pairing handshake failed") from exc
    finally:
        await stream.close()


__all__ = [
    "PairingError",
    "PairingOffer",
    "PairingOfferManager",
    "PairResult",
    "client_pair_exchange",
    "server_pair_exchange",
]
