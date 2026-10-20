"""Authenticated peer session: heartbeat, bounded input delivery, dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..constants import (
    INPUT_QUEUE_MAX,
    PING_INTERVAL,
    PING_TIMEOUT,
    PROTOCOL_VERSION,
)
from ..protocol.framing import FrameError
from ..protocol.models import ProtocolError, validate_peer_message
from .transport import SecureStream, TransportError

log = logging.getLogger(__name__)


FocusCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass(slots=True)
class SessionCallbacks:
    on_input: Callable[[dict[str, Any]], None]
    on_clipboard: Callable[[str, str, str], None]
    on_focus_request: FocusCallback
    on_focus_state: FocusCallback
    on_log_request: FocusCallback
    on_log_response: FocusCallback
    on_closed: Callable[[PeerSession, str], None]


@dataclass(slots=True)
class _QueuedInput:
    payload: dict[str, Any]
    movement: bool


class PeerSession:
    """One live link. All writes are serialized by SecureStream's lock."""

    def __init__(
        self,
        stream: SecureStream,
        *,
        peer_id: str,
        role: str,
        callbacks: SessionCallbacks,
    ) -> None:
        self.stream = stream
        self.peer_id = peer_id
        self.role = role
        self.callbacks = callbacks
        self.loop = asyncio.get_running_loop()
        self._closed = False
        self._finalized = False
        self._close_reason = ""
        self._closed_event = asyncio.Event()
        self._read_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._input_flush_task: asyncio.Task[None] | None = None
        self._input_queue: deque[_QueuedInput] = deque()
        self._native_events: deque[dict[str, Any]] = deque()
        self._native_lock = threading.Lock()
        self._native_flush_scheduled = False
        self._input_seq = 0
        self._last_received = time.monotonic()
        self._ping_id = 0
        self._pending_pings: dict[int, float] = {}
        self._latency_ms = 0
        self._bytes_sent = 0
        self._bytes_received = 0
        self._input_tokens = 6000.0
        self._input_refill_at = time.monotonic()
        self._clipboard_tokens = 8.0
        self._clipboard_refill_at = time.monotonic()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def latency_ms(self) -> int:
        return self._latency_ms

    @property
    def bytes_sent(self) -> int:
        return self._bytes_sent

    @property
    def bytes_received(self) -> int:
        return self._bytes_received

    def start(self) -> None:
        if self._closed:
            return
        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"ToRexLink-Read-{self.peer_id[:8]}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"ToRexLink-Ping-{self.peer_id[:8]}"
        )

    async def wait_closed(self) -> None:
        await self._closed_event.wait()

    @property
    def close_reason(self) -> str:
        return self._close_reason

    # ------------------------------------------------------------------- sends
    async def send(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise TransportError("session is closed")
        try:
            self._bytes_sent += await self.stream.send(payload)
        except (OSError, ConnectionError, TransportError) as exc:
            await self._terminate("write_error")
            raise TransportError("connection write failed") from exc

    async def send_clipboard(self, text: str, digest: str, origin: str) -> None:
        await self.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "clipboard",
                "text": text,
                "origin": origin,
                "sha256": digest,
            }
        )

    async def send_focus_request(
        self,
        *,
        action: str,
        edge: str | None,
        position: float | None,
        request_id: str,
    ) -> None:
        await self.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "focus_request",
                "action": action,
                "edge": edge,
                "position": position,
                "request_id": request_id,
            }
        )

    async def send_focus_state(self, state: dict[str, Any]) -> None:
        await self.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "focus_state",
                "epoch": state["epoch"],
                "controller": state["controller"],
                "target": state["target"],
                "reason": state["reason"],
                "entry_edge": state["entry_edge"],
                "position": state["position"],
            }
        )

    async def send_log_request(self, request_id: str, max_bytes: int) -> None:
        await self.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "log_request",
                "request_id": request_id,
                "max_bytes": max_bytes,
            }
        )

    async def send_log_response(
        self,
        request_id: str,
        text: str,
        *,
        truncated: bool,
        device: str,
        version: str,
    ) -> None:
        await self.send(
            {
                "v": PROTOCOL_VERSION,
                "type": "log_response",
                "request_id": request_id,
                "text": text,
                "truncated": truncated,
                "device": device,
                "version": version,
            }
        )

    def post_input_threadsafe(self, event: dict[str, Any]) -> None:
        """Queue a native event without a loop callback for every single event.

        At 1000 Hz, call_soon_threadsafe() itself becomes measurable overhead.
        A short thread-safe burst buffer lets the loop drain many hardware
        events in one callback while preserving order and low latency.
        """
        if self._closed:
            return
        with self._native_lock:
            if len(self._native_events) >= 16384:
                self._native_events.popleft()
            self._native_events.append(event)
            if self._native_flush_scheduled:
                return
            self._native_flush_scheduled = True
        try:
            self.loop.call_soon_threadsafe(self._drain_native_events)
        except RuntimeError:
            with self._native_lock:
                self._native_flush_scheduled = False
                self._native_events.clear()

    def _drain_native_events(self) -> None:
        with self._native_lock:
            events = list(self._native_events)
            self._native_events.clear()
            self._native_flush_scheduled = False
        if self._closed:
            return
        for event in events:
            self._enqueue_input(event)

    def _enqueue_input(self, event: dict[str, Any]) -> None:
        if self._closed:
            return
        movement = event.get("t") == "move"
        if movement and self._input_queue and self._input_queue[-1].movement:
            previous = self._input_queue[-1].payload
            old_event = previous["event"]
            old_event["dx"] = max(-2.0, min(2.0, float(old_event["dx"]) + float(event["dx"])))
            old_event["dy"] = max(-2.0, min(2.0, float(old_event["dy"]) + float(event["dy"])))
            self._schedule_input_flush()
            return
        if len(self._input_queue) >= INPUT_QUEUE_MAX:
            # Drop the oldest movement first; never silently drop a key/button.
            removed = False
            for index, item in enumerate(self._input_queue):
                if item.movement:
                    del self._input_queue[index]
                    removed = True
                    break
            if not removed:
                log.warning("Input queue overflow; closing the session")
                asyncio.create_task(self._terminate("input_overflow"))
                return
        self._input_seq += 1
        self._input_queue.append(
            _QueuedInput(
                payload={
                    "v": PROTOCOL_VERSION,
                    "type": "input",
                    "seq": self._input_seq,
                    "event": event,
                },
                movement=movement,
            )
        )
        self._schedule_input_flush()

    def _schedule_input_flush(self) -> None:
        if self._input_flush_task is None or self._input_flush_task.done():
            self._input_flush_task = asyncio.create_task(
                self._flush_inputs(), name=f"ToRexLink-InputSend-{self.peer_id[:8]}"
            )

    async def _flush_inputs(self) -> None:
        try:
            # Yield once so a burst of hardware events is coalesced into one
            # TLS record batch while keeping sub-millisecond latency.
            await asyncio.sleep(0)
            while self._input_queue and not self._closed:
                batch = [self._input_queue.popleft() for _ in range(min(12, len(self._input_queue)))]
                payloads = [item.payload for item in batch]
                self._bytes_sent += await self.stream.send_batch(payloads)
                if self._input_queue:
                    await asyncio.sleep(0)
        except (OSError, ConnectionError, TransportError) as exc:
            log.info("Input send failed: %s", exc)
            await self._terminate("input_write_error")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Unexpected input send failure")
            await self._terminate("input_error")

    # --------------------------------------------------------------- receiving
    def _consume_input_budget(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self._input_refill_at)
        self._input_refill_at = now
        self._input_tokens = min(7000.0, self._input_tokens + elapsed * 4500.0)
        if self._input_tokens < 1.0:
            return False
        self._input_tokens -= 1.0
        return True

    def _consume_clipboard_budget(self) -> bool:
        now = time.monotonic()
        elapsed = max(0.0, now - self._clipboard_refill_at)
        self._clipboard_refill_at = now
        self._clipboard_tokens = min(8.0, self._clipboard_tokens + elapsed * 1.2)
        if self._clipboard_tokens < 1.0:
            return False
        self._clipboard_tokens -= 1.0
        return True

    async def _read_loop(self) -> None:
        try:
            while not self._closed:
                try:
                    raw = await asyncio.wait_for(self.stream.recv(), timeout=PING_TIMEOUT)
                except TimeoutError:
                    await self._terminate("heartbeat_timeout")
                    return
                except (asyncio.IncompleteReadError, ConnectionError, OSError):
                    await self._terminate("connection_lost")
                    return
                except (FrameError, ValueError) as exc:
                    log.warning("Protocol frame error from peer: %s", exc)
                    await self._terminate("protocol_error")
                    return
                self._last_received = time.monotonic()
                self._bytes_received += self.stream.last_frame_size
                try:
                    message = validate_peer_message(raw)
                except ProtocolError as exc:
                    log.warning("Invalid peer message: %s", exc)
                    await self._terminate("protocol_error")
                    return
                await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("Unhandled read loop error")
            await self._terminate("internal_error")

    async def _dispatch(self, message: dict[str, Any]) -> None:
        message_type = message["type"]
        if message_type == "ping":
            await self.send(
                {"v": PROTOCOL_VERSION, "type": "pong", "id": message["id"]}
            )
            return
        if message_type == "pong":
            started = self._pending_pings.pop(message["id"], None)
            if started is not None:
                self._latency_ms = max(0, int((time.monotonic() - started) * 1000))
            return
        if message_type == "input":
            if not self._consume_input_budget():
                log.warning("Peer exceeded input rate limit; closing")
                await self._terminate("rate_limit")
                return
            with contextlib.suppress(Exception):
                self.callbacks.on_input(message["event"])
            return
        if message_type == "clipboard":
            if not self._consume_clipboard_budget():
                log.warning("Peer clipboard rate limit exceeded; ignoring update")
                return
            with contextlib.suppress(Exception):
                self.callbacks.on_clipboard(
                    message["text"], message["sha256"], message["origin"]
                )
            return
        if message_type == "focus_request":
            with contextlib.suppress(Exception):
                result = self.callbacks.on_focus_request(message)
                if inspect.isawaitable(result):
                    await result
            return
        if message_type == "focus_state":
            with contextlib.suppress(Exception):
                result = self.callbacks.on_focus_state(message)
                if inspect.isawaitable(result):
                    await result
            return
        if message_type == "log_request":
            with contextlib.suppress(Exception):
                result = self.callbacks.on_log_request(message)
                if inspect.isawaitable(result):
                    await result
            return
        if message_type == "log_response":
            with contextlib.suppress(Exception):
                result = self.callbacks.on_log_response(message)
                if inspect.isawaitable(result):
                    await result
            return
        if message_type == "bye":
            await self._terminate("peer_bye", notify_peer=False)
            return
        if message_type == "error":
            await self._terminate(f"peer_error:{message.get('code', 'error')}", notify_peer=False)
            return

    async def _heartbeat_loop(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(PING_INTERVAL)
                if self._closed:
                    return
                if time.monotonic() - self._last_received > PING_TIMEOUT:
                    await self._terminate("heartbeat_timeout")
                    return
                self._ping_id = (self._ping_id + 1) & 0x7FFFFFFF
                now = time.monotonic()
                self._pending_pings[self._ping_id] = now
                # Bound the map even if the peer never answers.
                for ping_id in [pid for pid, sent in self._pending_pings.items() if now - sent > 60]:
                    self._pending_pings.pop(ping_id, None)
                try:
                    await self.send(
                        {"v": PROTOCOL_VERSION, "type": "ping", "id": self._ping_id}
                    )
                except TransportError:
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.debug("Heartbeat loop stopped", exc_info=True)

    # ------------------------------------------------------------------- close
    async def close(self, reason: str = "normal") -> None:
        await self._terminate(reason, notify_peer=True)

    async def _terminate(self, reason: str, *, notify_peer: bool = False) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._close_reason = reason
        with self._native_lock:
            self._native_events.clear()
        was_open = not self._closed
        self._closed = True
        if notify_peer and was_open:
            with contextlib.suppress(Exception):
                async with asyncio.timeout(0.8):
                    await self.stream.send(
                        {"v": PROTOCOL_VERSION, "type": "bye", "reason": "normal"}
                    )
        current = asyncio.current_task()
        for task in (self._read_task, self._heartbeat_task, self._input_flush_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        with contextlib.suppress(Exception):
            await self.stream.close()
        self._closed_event.set()
        if was_open:
            with contextlib.suppress(Exception):
                self.callbacks.on_closed(self, reason)
        log.info("Session with %s closed (%s)", self.peer_id[:8], reason)



__all__ = ["PeerSession", "SessionCallbacks"]
