"""TLS 1.3 transport and bounded JSON stream."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import ssl
from dataclasses import dataclass, field
from typing import Any

from ..constants import CONNECT_TIMEOUT, HANDSHAKE_TIMEOUT, MAX_FRAME_BYTES, TLS_MIN_VERSION
from ..protocol.framing import read_frame_sized, write_frame, write_frames_at_once
from ..security.identity import Identity

log = logging.getLogger(__name__)
ALPN = "torexlink/1"


class TransportError(Exception):
    """Connection failed before a valid application session existed."""


def _base_context(protocol: ssl._SSLMethod = ssl.PROTOCOL_TLS_CLIENT) -> ssl.SSLContext:
    context = ssl.SSLContext(protocol)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    with contextlib.suppress(AttributeError):
        context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_RENEGOTIATION
    return context


def server_ssl_context(identity: Identity) -> ssl.SSLContext:
    context = _base_context(ssl.PROTOCOL_TLS_SERVER)
    try:
        context.load_cert_chain(
            certfile=str(identity.cert_path),
            keyfile=str(identity.key_path),
            password=None,
        )
    except (OSError, ssl.SSLError) as exc:
        raise TransportError(f"cannot load device certificate: {exc}") from exc
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols([ALPN])
    return context


def pairing_client_ssl_context() -> ssl.SSLContext:
    """Encrypted but not yet authenticated transport used only for CPace pairing."""
    context = _base_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols([ALPN])
    return context


def pinned_client_ssl_context(peer_cert_pem: str) -> ssl.SSLContext:
    """TLS context that trusts exactly one pinned self-signed peer certificate."""
    context = _base_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_verify_locations(cadata=peer_cert_pem)
    except ssl.SSLError as exc:
        raise TransportError("stored peer certificate is unusable") from exc
    context.set_alpn_protocols([ALPN])
    return context


def peer_certificate_der(writer: asyncio.StreamWriter) -> bytes:
    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        raise TransportError("connection is not protected by TLS")
    der = ssl_object.getpeercert(binary_form=True)
    if not der:
        raise TransportError("peer did not present a TLS certificate")
    return der


def verify_tls13(writer: asyncio.StreamWriter) -> None:
    ssl_object = writer.get_extra_info("ssl_object")
    if ssl_object is None:
        raise TransportError("TLS is required")
    if ssl_object.version() != TLS_MIN_VERSION:
        raise TransportError("TLS 1.3 is required")
    selected = ssl_object.selected_alpn_protocol()
    if selected != ALPN:
        raise TransportError("protocol negotiation failed")


@dataclass(slots=True, eq=False)
class SecureStream:
    """One authenticated or in-progress TLS session with framed JSON I/O."""

    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    _write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    last_frame_size: int = 0

    @property
    def peername(self) -> tuple[str, int]:
        peername = self.writer.get_extra_info("peername")
        if isinstance(peername, tuple) and len(peername) >= 2:
            return str(peername[0]), int(peername[1])
        return "unknown", 0

    @property
    def ssl_version(self) -> str:
        ssl_object = self.writer.get_extra_info("ssl_object")
        return ssl_object.version() if ssl_object is not None else "none"

    async def recv(self) -> dict[str, Any]:
        message, size = await read_frame_sized(self.reader, max_bytes=MAX_FRAME_BYTES)
        self.last_frame_size = size
        return message

    async def send(self, payload: dict[str, Any]) -> int:
        if self.closed:
            raise TransportError("connection is closed")
        return await write_frame(self.writer, payload, lock=self._write_lock)

    async def send_batch(self, payloads: list[dict[str, Any]]) -> int:
        if self.closed:
            raise TransportError("connection is closed")
        if payloads:
            return await write_frames_at_once(self.writer, payloads, lock=self._write_lock)
        return 0

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.writer.close()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self.writer.wait_closed(), timeout=2.0)

    async def abort(self) -> None:
        self.closed = True
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()


async def open_secure_stream(
    host: str,
    port: int,
    *,
    context: ssl.SSLContext,
    timeout: float = CONNECT_TIMEOUT,
) -> SecureStream:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=host,
                port=port,
                ssl=context,
                server_hostname="torexlink.local",
                limit=MAX_FRAME_BYTES + 64,
            ),
            timeout=timeout,
        )
    except (TimeoutError, OSError, ssl.SSLError) as exc:
        raise TransportError(f"cannot reach peer: {exc}") from exc
    verify_tls13(writer)
    return SecureStream(reader=reader, writer=writer)


async def receive_hello(
    stream: SecureStream,
    *,
    timeout: float = HANDSHAKE_TIMEOUT,
) -> dict[str, Any]:
    try:
        async with asyncio.timeout(timeout):
            return await stream.recv()
    except (TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
        raise TransportError("handshake timed out") from exc


async def close_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(writer.wait_closed(), timeout=1.0)


def certificate_sha256(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


__all__ = [
    "ALPN",
    "SecureStream",
    "TransportError",
    "peer_certificate_der",
    "pinned_client_ssl_context",
    "pairing_client_ssl_context",
    "receive_hello",
    "server_ssl_context",
    "verify_tls13",
]
