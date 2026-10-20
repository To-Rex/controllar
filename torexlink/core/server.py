"""TLS listener lifecycle with bounded concurrency."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..constants import DEFAULT_PORT, MAX_FRAME_BYTES, PORT_SCAN_COUNT
from ..security.identity import Identity
from .transport import (
    ALPN,
    SecureStream,
    TransportError,
    server_ssl_context,
    verify_tls13,
)

log = logging.getLogger(__name__)

ConnectionHandler = Callable[[SecureStream], Awaitable[None]]


class ServerError(RuntimeError):
    pass


@dataclass(slots=True)
class PeerServer:
    identity: Identity
    handler: ConnectionHandler
    max_connections: int = 8
    _server: asyncio.AbstractServer | None = None
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)
    _streams: set[SecureStream] = field(default_factory=set)
    _semaphore: asyncio.Semaphore | None = None
    port: int | None = None
    running: bool = False

    async def start(
        self,
        *,
        preferred_port: int = DEFAULT_PORT,
        attempts: int = PORT_SCAN_COUNT,
    ) -> int:
        if self.running:
            assert self.port is not None
            return self.port
        context = server_ssl_context(self.identity)
        candidates = list(range(preferred_port, preferred_port + attempts))
        last_error: Exception | None = None
        for port in candidates:
            try:
                self._server = await asyncio.start_server(
                    self._client_connected,
                    host="0.0.0.0",
                    port=port,
                    ssl=context,
                    ssl_handshake_timeout=8.0,
                    backlog=16,
                    limit=MAX_FRAME_BYTES + 64,
                    start_serving=True,
                )
            except OSError as exc:
                last_error = exc
                continue
            sockets = self._server.sockets or []
            if sockets:
                self.port = int(sockets[0].getsockname()[1])
            else:
                self.port = port
            break
        if self._server is None:
            raise ServerError(f"no free listening port: {last_error}")
        self._semaphore = asyncio.Semaphore(self.max_connections)
        self.running = True
        log.info("Encrypted listener active on TCP %s (TLS 1.3)", self.port)
        return self.port

    async def _client_connected(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        stream: SecureStream | None = None
        acquired = False
        try:
            assert self._semaphore is not None
            try:
                await asyncio.wait_for(self._semaphore.acquire(), timeout=5.0)
                acquired = True
            except TimeoutError:
                writer.close()
                return
            peer = writer.get_extra_info("peername")
            log.info("Incoming TLS request from %s", peer[0] if isinstance(peer, tuple) else peer)
            verify_tls13(writer)
            ssl_object = writer.get_extra_info("ssl_object")
            if ssl_object is not None and ssl_object.selected_alpn_protocol() != ALPN:
                raise TransportError("ALPN mismatch")
            stream = SecureStream(reader=reader, writer=writer)
            self._streams.add(stream)
            await self.handler(stream)
        except (TransportError, ConnectionError, asyncio.IncompleteReadError, ValueError) as exc:
            log.info("Connection closed during handshake: %s", exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the listener alive
            log.exception("Unhandled inbound connection error")
        finally:
            if acquired and self._semaphore is not None:
                self._semaphore.release()
            if stream is not None:
                self._streams.discard(stream)
                await stream.close()
            else:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            if task is not None:
                self._tasks.discard(task)

    async def stop(self) -> None:
        if not self.running and self._server is None:
            return
        self.running = False
        server = self._server
        self._server = None
        if server is not None:
            server.close()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(server.wait_closed(), timeout=2.0)
        for stream in list(self._streams):
            with contextlib.suppress(Exception):
                await stream.abort()
        self._streams.clear()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self.port = None
        log.info("Listener stopped")


__all__ = ["ConnectionHandler", "PeerServer", "ServerError"]
