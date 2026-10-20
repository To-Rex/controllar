"""Application orchestrator: networking, pairing, focus and clipboard.

Everything in this module runs inside a single asyncio event loop. Native input
callbacks enter through ``call_soon_threadsafe`` only, so they never block the
OS event tap/hook and never race asyncio state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import secrets
import socket
import sys
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..constants import (
    APP_VERSION,
    DEFAULT_PORT,
    FOCUS_ACTIONS,
    LOG_SHARE_COOLDOWN_SECONDS,
    LOG_SHARE_MAX_BYTES,
    LOG_SHARE_TIMEOUT_SECONDS,
    RECONNECT_DELAYS,
    RECONNECT_JITTER,
    REMOTE_LOGS_MIN_VERSION,
)
from ..input.base import InputBackend
from ..input.bridge import InputBridge, InputBridgeCallbacks
from ..input.events import InputMoveEvent, event_from_wire
from ..input.factory import create_input_backend
from ..logging_setup import read_log_tail
from ..pairing_payload import PairingPayload, PairingPayloadError
from ..paths import AppPaths
from ..peers import PeerRecord, PeerStore
from ..protocol.handshake import (
    ServerDescriptor,
    clean_device_name,
    parse_hello,
    protocol_error,
)
from ..protocol.models import ProtocolError
from ..security.identity import Identity, short_fingerprint
from ..settings import Settings, SettingsStore
from ..util import atomic_write_text, backoff_delay, iso_now, version_at_least
from .auth import AuthenticatedLink, ReconnectAuthError, client_auth_exchange, server_auth_exchange
from .clipboard import ClipboardSync, NativeClipboard, create_native_clipboard
from .discovery import DiscoveredDevice, DiscoveryService, local_ipv4_addresses
from .focus import FocusAuthority, FocusError, FocusState
from .pairing import (
    PairingError,
    PairingOffer,
    PairingOfferManager,
    PairResult,
    client_pair_exchange,
    server_pair_exchange,
)
from .server import PeerServer, ServerError
from .session import PeerSession, SessionCallbacks
from .state import (
    ConnectionInfo,
    DiscoveredView,
    FocusInfo,
    LocalDeviceInfo,
    PairingInfo,
    PeerView,
    ServiceEvent,
    ServiceSnapshot,
)
from .transport import TransportError, receive_hello

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RemoteLogs:
    peer_name: str
    app_version: str
    text: str
    truncated: bool
    saved_path: Path


class ServiceError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass(slots=True)
class _ActiveLink:
    session: PeerSession
    peer: PeerRecord
    role: str
    address: str
    port: int
    app_version: str
    link: AuthenticatedLink
    connected_at: str


@dataclass(slots=True)
class _ReconnectState:
    attempt: int = 0
    next_retry_at: float | None = None


class ToRexLinkService:
    def __init__(
        self,
        paths: AppPaths,
        *,
        on_event: Callable[[ServiceEvent], None] | None = None,
        input_backend_factory: Callable[[], InputBackend] = create_input_backend,
        clipboard_factory: Callable[[], NativeClipboard] = create_native_clipboard,
        enable_discovery: bool = True,
        preferred_port: int = DEFAULT_PORT,
        address_provider: Callable[[], tuple[str, ...]] = local_ipv4_addresses,
        settings_store: SettingsStore | None = None,
        peer_store: PeerStore | None = None,
    ) -> None:
        self.paths = paths
        self.settings_store = settings_store or SettingsStore(paths.settings_file)
        self.peer_store = peer_store or PeerStore(paths.peers_file)
        self._on_event = on_event
        self._input_backend_factory = input_backend_factory
        self._clipboard_factory = clipboard_factory
        self._enable_discovery = enable_discovery
        self._preferred_port = preferred_port
        self._address_provider = address_provider

        self._settings = self.settings_store.settings
        self._identity: Identity | None = None
        self._running = False
        self._stopping = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: PeerServer | None = None
        self._discovery: DiscoveryService | None = None
        self._mdns_active = False
        self._pairing_manager = PairingOfferManager()
        self._pairing_in_progress = False
        self._pairing_expiry_task: asyncio.Task[None] | None = None
        self._active: _ActiveLink | None = None
        self._active_generation = 0
        self._connect_task: asyncio.Task[None] | None = None
        self._auto_task: asyncio.Task[None] | None = None
        self._network_watch_task: asyncio.Task[None] | None = None
        self._network_addresses: tuple[str, ...] = ()
        self._reconnect = _ReconnectState()
        self._manual_disconnect = False
        self._closing = False
        self._last_error = ""
        self._listener_error = ""
        self._status_override: str | None = None
        self._focus_authority: FocusAuthority | None = None
        self._focus: FocusState | None = None
        self._bridge: InputBridge | None = None
        self._input_lock = asyncio.Lock()
        self._input_error = ""
        self._clipboard: ClipboardSync | None = None
        self._clipboard_error = ""
        self._seq = 0
        self._discovered: dict[str, DiscoveredDevice] = {}
        self._auth_failures: dict[str, deque[float]] = {}
        self._auth_blocked_until: dict[str, float] = {}
        self._last_focus_request = 0.0
        self._log_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._last_log_share_time = 0.0
        self._last_snapshot: ServiceSnapshot | None = None
        self._last_peer_id: str | None = None

        self._pairing_manager.set_listener(self._on_pairing_offer_changed)
        if not self._settings.device_name:
            self._settings.device_name = self._hostname()

    # ------------------------------------------------------------------ helpers
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def identity(self) -> Identity | None:
        return self._identity

    @property
    def paths_root(self) -> Any:
        return self.paths.root

    @property
    def input_backend_factory(self) -> Callable[[], InputBackend]:
        return self._input_backend_factory

    def set_event_callback(self, callback: Callable[[ServiceEvent], None]) -> None:
        self._on_event = callback

    def _hostname(self) -> str:
        try:
            raw = socket.gethostname().split(".")[0]
        except OSError:
            raw = "ToRexLink"
        return clean_device_name(raw or "ToRexLink")

    @property
    def local_name(self) -> str:
        return clean_device_name(self._settings.device_name or self._hostname())

    @property
    def local_platform(self) -> str:
        if sys.platform == "darwin":
            return "macos"
        if sys.platform == "win32":
            return "windows"
        return "other"

    @property
    def local_addresses(self) -> tuple[str, ...]:
        return self._address_provider()

    def _ensure_running(self) -> None:
        if not self._running:
            raise ServiceError("not_running", "service is not running")

    def _ensure_identity(self) -> Identity:
        if self._identity is None:
            raise ServiceError("not_initialized", "device identity is not ready")
        return self._identity

    def _cancel_auto_connect(self) -> None:
        task = self._auto_task
        self._auto_task = None
        if task is not None:
            task.cancel()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._stopping = False
        await asyncio.to_thread(self.paths.ensure)
        if self._identity is None:
            try:
                self._identity = await asyncio.to_thread(
                    Identity.load_or_create,
                    self.paths.certificate_file,
                    self.paths.private_key_file,
                )
                if not self._identity.verify_own_key():
                    raise ServiceError("identity_error", "identity key verification failed")
            except Exception as exc:  # noqa: BLE001
                self._set_error("identity_error", str(exc))
                raise ServiceError("identity_error", str(exc)) from exc
        self._running = True
        self._discovered = {}
        self._network_addresses = tuple(self._address_provider())
        self._network_watch_task = asyncio.create_task(
            self._network_watch_loop(), name="ToRexLink-NetworkWatch"
        )
        if self._enable_discovery:
            await self._start_discovery()
        if self._settings.hosting_enabled:
            await self._start_hosting()
        self._notify("started")
        if self._settings.auto_reconnect:
            self._schedule_auto_connect(delay=0.7)

    async def stop(self) -> None:
        if not self._running:
            return
        self._stopping = True
        self._running = False
        self._cancel_pairing_expiry()
        for task in (self._auto_task, self._connect_task, self._network_watch_task):
            if task is not None:
                task.cancel()
        self._auto_task = None
        self._connect_task = None
        self._network_watch_task = None
        active = self._active
        self._active = None
        if active is not None:
            with contextlib.suppress(Exception):
                await active.session.close("shutdown")
        await self._stop_session_components()
        self._pairing_manager.cancel()
        await self._stop_discovery()
        await self._stop_hosting()
        self._fail_log_waiters()
        self._focus = None
        self._focus_authority = None
        self._notify("stopped")

    # ------------------------------------------------------------------ hosting
    async def _start_hosting(self) -> bool:
        if self._server is not None and self._server.running:
            return True
        identity = self._ensure_identity()
        server = PeerServer(identity=identity, handler=self._handle_incoming)
        try:
            port = await server.start(preferred_port=self._preferred_port)
        except ServerError as exc:
            self._listener_error = str(exc)
            self._server = None
            self._set_error("listener_error", str(exc))
            self._notify("error")
            return False
        self._server = server
        self._listener_error = ""
        if self._discovery is not None and self._discovery.running:
            self._mdns_active = await self._discovery.advertise(
                port=port,
                name=self.local_name,
                platform=self.local_platform,
                fingerprint=identity.fingerprint,
            )
        self._notify("hosting")
        return True

    async def _stop_hosting(self) -> None:
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.stop()
            self._server = None
        if self._discovery is not None:
            await self._discovery.unadvertise()
        self._mdns_active = False
        self._listener_error = ""
        if self._pairing_manager.offer is not None:
            self._pairing_manager.cancel()
        self._notify("hosting")

    # ---------------------------------------------------------------- discovery
    async def _start_discovery(self) -> None:
        identity = self._ensure_identity()
        discovery = DiscoveryService()
        discovery.set_callback(self._on_discovered)
        ok = await discovery.start(identity.device_id)
        self._discovery = discovery if ok else None
        if not ok:
            log.info("mDNS discovery is not available; manual connections remain enabled")

    async def _stop_discovery(self) -> None:
        discovery = self._discovery
        self._discovery = None
        self._mdns_active = False
        if discovery is not None:
            with contextlib.suppress(Exception):
                await discovery.stop()

    def _on_discovered(self, devices: list[DiscoveredDevice]) -> None:
        self._discovered = {device.device_id: device for device in devices}
        self._notify("discovery")

    async def _network_watch_loop(self) -> None:
        """Refresh mDNS/QR endpoints when the LAN address changes."""
        try:
            while self._running:
                await asyncio.sleep(8.0)
                if self._stopping or not self._running:
                    return
                current = tuple(self._address_provider())
                if current == self._network_addresses:
                    continue
                self._network_addresses = current
                log.info("LAN addresses changed: %s", ", ".join(current) or "(none)")
                identity = self._identity
                if (
                    identity is not None
                    and self._server is not None
                    and self._server.running
                    and self._server.port
                    and self._discovery is not None
                ):
                    self._mdns_active = await self._discovery.advertise(
                        port=self._server.port,
                        name=self.local_name,
                        platform=self.local_platform,
                        fingerprint=identity.fingerprint,
                    )
                offer = self._pairing_manager.offer
                if offer is not None and current:
                    offer.addresses = current
                self._notify("network")
        except asyncio.CancelledError:
            return
        except Exception:  # noqa: BLE001
            log.debug("Network address watcher stopped", exc_info=True)

    # ------------------------------------------------------------------ pairing
    def _on_pairing_offer_changed(self, offer: PairingOffer | None) -> None:
        self._notify("pairing")

    async def begin_pairing(self) -> PairingInfo:
        self._ensure_running()
        if self._pairing_in_progress:
            raise ServiceError("pairing_busy")
        if self._active is not None:
            raise ServiceError("already_connected", "disconnect before pairing")
        self._cancel_auto_connect()
        if not self._settings.hosting_enabled:
            self._settings = self.settings_store.update({"hosting_enabled": True})
            await self._start_hosting()
        if self._server is None or not self._server.running:
            ok = await self._start_hosting()
            if not ok:
                raise ServiceError("listener_error", self._listener_error or "listener unavailable")
        if self._active is not None:
            # An auto-reconnect completed while the listener was starting.
            raise ServiceError("already_connected", "disconnect before pairing")
        identity = self._ensure_identity()
        addresses = self._address_provider()
        if not addresses:
            raise ServiceError(
                "no_lan_address",
                "No LAN IPv4 address is available; connect both devices to the same network",
            )
        port = self._server.port if self._server else None
        if not port:
            raise ServiceError("listener_error", "listener is not ready")
        if self._discovery is not None and self._discovery.running:
            self._mdns_active = await self._discovery.advertise(
                port=port,
                name=self.local_name,
                platform=self.local_platform,
                fingerprint=identity.fingerprint,
            )
        offer = self._pairing_manager.create(
            device_id=identity.device_id,
            fingerprint=identity.fingerprint,
            name=self.local_name,
            addresses=addresses,
            port=port,
        )
        self._schedule_pairing_expiry(offer)
        self._notify("pairing_started")
        pair_info = self._pairing_info()
        assert pair_info is not None
        return pair_info

    async def cancel_pairing(self) -> None:
        self._cancel_pairing_expiry()
        self._pairing_manager.cancel()
        self._notify("pairing_cancelled")

    def _schedule_pairing_expiry(self, offer: PairingOffer) -> None:
        self._cancel_pairing_expiry()

        async def expire() -> None:
            await asyncio.sleep(max(0.0, offer.ttl_remaining) + 0.05)
            current = self._pairing_manager.offer
            if current is None or current.code != offer.code:
                return
            self._pairing_manager.cancel()
            self._notify("pairing_expired")

        self._pairing_expiry_task = asyncio.create_task(expire(), name="ToRexLink-PairExpiry")

    def _cancel_pairing_expiry(self) -> None:
        task = self._pairing_expiry_task
        self._pairing_expiry_task = None
        if task is not None and not task.done():
            task.cancel()

    def _pairing_info(self) -> PairingInfo | None:
        offer = self._pairing_manager.offer
        if offer is None:
            return None
        try:
            uri = offer.payload().to_uri()
        except PairingPayloadError:
            return None
        return PairingInfo(
            code=offer.code,
            qr_uri=uri,
            expires_at=time.time() + offer.ttl_remaining,
            seconds_remaining=int(math.ceil(offer.ttl_remaining)),
            attempts_remaining=offer.attempts_remaining,
            addresses=offer.addresses,
            port=offer.port,
        )

    async def _handle_pair_attempt(self, stream: Any, hello: Any) -> None:
        if self._active is not None:
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("pair_unavailable"))
            return
        if self._pairing_manager.offer is None:
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("pair_unavailable"))
            return
        identity = self._ensure_identity()
        descriptor = ServerDescriptor(
            identity=identity,
            name=self.local_name,
            platform=self.local_platform,
            nonce=secrets.token_bytes(32),
        )
        saved: dict[str, PeerRecord] = {}

        def persist(result: PairResult) -> None:
            record = PeerRecord.from_pairing(
                result.identity,
                name=result.name,
                platform=result.platform,
                token=result.token,
                outbound=False,
                address=result.address,
                port=result.port,
                app_version=result.app_version,
            )
            self.peer_store.upsert(record)
            saved["peer"] = record

        try:
            result = await server_pair_exchange(
                stream,
                hello,
                self._pairing_manager,
                descriptor,
                on_paired=persist,
            )
            if result is not None:
                record = saved.get("peer")
                self._notify(
                    "peer_paired",
                    data={"peer_name": record.name if record else result.name},
                )
                log.info("Pairing completed with %s", result.name)
                self._cancel_pairing_expiry()
        except PairingError as exc:
            log.info("Pairing attempt rejected: %s", exc)
            self._notify("pairing_attempt_failed")
        except Exception:  # noqa: BLE001
            log.exception("Unexpected pairing error")
            await self._pairing_manager.fail_attempt()
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("pair_denied"))
            await asyncio.sleep(0.5)

    async def pair_with_payload(self, payload: PairingPayload) -> PeerRecord:
        self._ensure_running()
        if self._pairing_in_progress:
            raise ServiceError("pairing_busy")
        if self._active is not None:
            raise ServiceError("already_connected", "disconnect before pairing")
        self._cancel_auto_connect()
        self._pairing_in_progress = True
        self._status_override = "connecting"
        self._notify("pairing_start")
        code = payload.code
        addresses = list(payload.addresses)
        last_error = ""
        try:
            await self.cancel_pairing()
            result: PairResult | None = None
            for address in addresses[:4]:
                try:
                    result = await client_pair_exchange(
                        address,
                        payload.port,
                        code,
                        identity=self._ensure_identity(),
                        name=self.local_name,
                        platform=self.local_platform,
                        expected_fingerprint=payload.fingerprint,
                        expected_device_id=payload.device_id,
                    )
                    break
                except PairingError as exc:
                    last_error = str(exc)
                    if getattr(exc, "retryable", False):
                        continue
                    break
            if result is None:
                self._set_error("pairing_failed", last_error)
                self._notify("pairing_failed")
                raise ServiceError("pairing_failed", last_error)
            record = PeerRecord.from_pairing(
                result.identity,
                name=result.name,
                platform=result.platform,
                token=result.token,
                outbound=True,
                address=result.address,
                port=result.port,
                app_version=result.app_version,
            )
            self.peer_store.upsert(record)
            self._settings = self.settings_store.update(
                {"preferred_peer_id": record.peer_id, "last_peer_id": record.peer_id}
            )
            self._notify("peer_paired", data={"peer_name": record.name})
            self._last_error = ""
            try:
                await self._establish_guest(record, address=result.address, port=result.port)
            except Exception as exc:  # noqa: BLE001
                log.info("Pairing succeeded but immediate reconnect failed: %s", exc)
                self._set_error("reconnect_failed", str(exc))
                if self._settings.auto_reconnect:
                    self._schedule_auto_connect(delay=1.0)
            return record
        finally:
            self._pairing_in_progress = False
            self._status_override = None
            self._notify("pairing")

    async def pair_with_code(
        self,
        host: str,
        port: int,
        code: str,
        *,
        expected_fingerprint: str | None = None,
        expected_device_id: str | None = None,
    ) -> PeerRecord:
        host = host.strip()
        if not host or len(host) > 253 or any(ord(char) < 33 for char in host):
            raise ServiceError("invalid_address")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ServiceError("invalid_port")
        if not isinstance(code, str) or len(code) != 6 or not code.isdigit():
            raise ServiceError("invalid_code")
        # Without a QR/discovery fingerprint the first exchange is TOFU over
        # CPace (never a plaintext code). With a known fingerprint it is pinned
        # even during the initial pairing exchange.
        self._ensure_running()
        if self._pairing_in_progress:
            raise ServiceError("pairing_busy")
        if self._active is not None:
            raise ServiceError("already_connected")
        self._cancel_auto_connect()
        self._pairing_in_progress = True
        self._status_override = "connecting"
        self._notify("pairing_start")
        try:
            await self.cancel_pairing()
            try:
                result = await client_pair_exchange(
                    host,
                    port,
                    code,
                    identity=self._ensure_identity(),
                    name=self.local_name,
                    platform=self.local_platform,
                    expected_fingerprint=expected_fingerprint,
                    expected_device_id=expected_device_id,
                )
            except PairingError as exc:
                self._set_error("pairing_failed", str(exc))
                raise ServiceError("pairing_failed", str(exc)) from exc
            record = PeerRecord.from_pairing(
                result.identity,
                name=result.name,
                platform=result.platform,
                token=result.token,
                outbound=True,
                address=result.address,
                port=result.port,
                app_version=result.app_version,
            )
            self.peer_store.upsert(record)
            self._settings = self.settings_store.update(
                {"preferred_peer_id": record.peer_id, "last_peer_id": record.peer_id}
            )
            self._notify("peer_paired", data={"peer_name": record.name})
            self._last_error = ""
            try:
                await self._establish_guest(record, address=result.address, port=result.port)
            except Exception as exc:  # noqa: BLE001
                log.info("Immediate connection after pairing failed: %s", exc)
                self._set_error("reconnect_failed", str(exc))
                if self._settings.auto_reconnect:
                    self._schedule_auto_connect(delay=1.0)
            return record
        finally:
            self._pairing_in_progress = False
            self._status_override = None
            self._notify("pairing")

    # --------------------------------------------------------------- remote logs
    async def request_remote_logs(self, max_bytes: int = LOG_SHARE_MAX_BYTES) -> RemoteLogs:
        """Fetch a bounded, secret-redacted log tail from the paired computer."""
        self._ensure_running()
        active = self._active
        if active is None or active.session.closed:
            raise ServiceError("not_connected")
        if not version_at_least(active.app_version, REMOTE_LOGS_MIN_VERSION):
            raise ServiceError("peer_unsupported")
        if max_bytes < 1024 or max_bytes > LOG_SHARE_MAX_BYTES:
            raise ServiceError("invalid_request")
        now = time.monotonic()
        if now - self._last_log_share_time < LOG_SHARE_COOLDOWN_SECONDS:
            raise ServiceError("rate_limited")
        self._last_log_share_time = now
        request_id = secrets.token_hex(8)
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._log_waiters[request_id] = future
        try:
            try:
                await active.session.send_log_request(request_id, max_bytes)
            except TransportError as exc:
                raise ServiceError("remote_logs_failed") from exc
            try:
                response = await asyncio.wait_for(future, timeout=LOG_SHARE_TIMEOUT_SECONDS)
            except TimeoutError as exc:
                raise ServiceError("remote_logs_timeout") from exc
        finally:
            self._log_waiters.pop(request_id, None)
        if self._active is not active:
            raise ServiceError("not_connected")
        text = str(response.get("text", ""))
        truncated = bool(response.get("truncated", False))
        header = (
            f"# ToRexLink remote log\n"
            f"# Peer: {active.peer.name} ({response.get('device', active.peer.name)})\n"
            f"# Version: {response.get('version', active.app_version)}\n"
            f"# Retrieved: {iso_now()}\n"
            f"# Truncated: {truncated}\n\n"
        )
        saved_path = self.paths.logs_dir / f"remote-{active.peer.peer_id[:8]}.log"
        await asyncio.to_thread(atomic_write_text, saved_path, header + text)
        return RemoteLogs(
            peer_name=active.peer.name,
            app_version=str(response.get("version", active.app_version)),
            text=text,
            truncated=truncated,
            saved_path=saved_path,
        )

    async def _on_remote_log_request(self, message: dict[str, Any]) -> None:
        active = self._active
        if active is None or active.session.closed:
            return
        now = time.monotonic()
        if now - self._last_log_share_time < LOG_SHARE_COOLDOWN_SECONDS:
            return
        self._last_log_share_time = now
        max_bytes = min(int(message.get("max_bytes", LOG_SHARE_MAX_BYTES)), LOG_SHARE_MAX_BYTES)
        payload = await asyncio.to_thread(self._read_local_log_for_share, max_bytes)
        text, truncated = payload
        try:
            await active.session.send_log_response(
                str(message["request_id"]),
                text,
                truncated=truncated,
                device=self.local_name,
                version=APP_VERSION,
            )
        except TransportError:
            return

    def _read_local_log_for_share(self, max_bytes: int) -> tuple[str, bool]:
        text = read_log_tail(self.paths.log_file, max_lines=4000)
        raw = text.encode("utf-8", "replace")
        truncated = len(raw) > max_bytes
        if truncated:
            # Keep the newest part and never split a UTF-8 sequence.
            text = raw[-max_bytes:].decode("utf-8", "ignore")
        return text, truncated

    def _on_remote_log_response(self, message: dict[str, Any]) -> None:
        future = self._log_waiters.get(str(message.get("request_id", "")))
        if future is None or future.done():
            return
        future.set_result(
            {
                "text": str(message.get("text", "")),
                "truncated": bool(message.get("truncated", False)),
                "device": str(message.get("device", "")),
                "version": str(message.get("version", "")),
            }
        )

    def _fail_log_waiters(self) -> None:
        for future in list(self._log_waiters.values()):
            if not future.done():
                future.set_exception(ServiceError("connection_lost"))
        self._log_waiters.clear()

    # ------------------------------------------------------------- connections
    async def _handle_incoming(self, stream: Any) -> None:
        peer_ip = stream.peername[0]
        hello_raw: dict[str, Any] | None = None
        try:
            hello_raw = await receive_hello(stream)
            hello = parse_hello(hello_raw)
        except (TransportError, ProtocolError, ValueError) as exc:
            log.info("Rejected malformed TLS hello from %s: %s", peer_ip, exc)
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("bad_handshake"))
            await asyncio.sleep(0.4)
            return
        if hello.mode == "pair":
            await self._handle_pair_attempt(stream, hello)
            return
        if not self._settings.hosting_enabled:
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("hosting_disabled"))
            return
        await self._handle_authenticated_incoming(stream, hello, hello_raw)

    def _is_auth_blocked(self, ip: str) -> bool:
        blocked_until = self._auth_blocked_until.get(ip)
        if blocked_until is None:
            return False
        if time.monotonic() >= blocked_until:
            self._auth_blocked_until.pop(ip, None)
            return False
        return True

    def _record_auth_failure(self, ip: str) -> None:
        now = time.monotonic()
        # Bound the per-IP failure map on a long-lived host.
        if len(self._auth_failures) > 256 and ip not in self._auth_failures:
            stale = [
                address
                for address, timestamps in self._auth_failures.items()
                if not timestamps or now - timestamps[-1] > 60
            ]
            for address in stale[:128]:
                self._auth_failures.pop(address, None)
                self._auth_blocked_until.pop(address, None)
        failures = self._auth_failures.setdefault(ip, deque(maxlen=12))
        while failures and now - failures[0] > 60:
            failures.popleft()
        failures.append(now)
        if len(failures) >= 6:
            self._auth_blocked_until[ip] = now + 30.0
            log.warning("Temporarily blocking repeated authentication failures from %s", ip)

    def _clear_auth_failures(self, ip: str) -> None:
        self._auth_failures.pop(ip, None)
        self._auth_blocked_until.pop(ip, None)

    async def _handle_authenticated_incoming(
        self,
        stream: Any,
        hello: Any,
        hello_raw: dict[str, Any],
    ) -> None:
        ip = stream.peername[0]
        if self._is_auth_blocked(ip):
            await asyncio.sleep(1.5)
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("auth_failed"))
            return
        record = self.peer_store.get(hello.device_id)
        if record is None:
            self._record_auth_failure(ip)
            await asyncio.sleep(0.7)
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("auth_failed"))
            return
        descriptor = ServerDescriptor(
            identity=self._ensure_identity(),
            name=self.local_name,
            platform=self.local_platform,
            nonce=secrets.token_bytes(32),
        )
        try:
            link = await server_auth_exchange(stream, hello, record, descriptor)
        except (ReconnectAuthError, TransportError, ProtocolError, ValueError) as exc:
            self._record_auth_failure(ip)
            log.info("Rejected authenticated connection from %s: %s", ip, exc)
            await asyncio.sleep(0.65)
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("auth_failed"))
            return
        self._clear_auth_failures(ip)
        other = self._active
        if other is not None and other.peer.peer_id != record.peer_id:
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("busy"))
            return
        if other is not None:
            await self._replace_active_session(other)
        try:
            session = await self._adopt_host_session(link, record)
        except Exception:  # noqa: BLE001
            log.exception("Could not establish host session")
            with contextlib.suppress(Exception):
                await stream.send(protocol_error("session_failed"))
            raise
        await session.wait_closed()

    async def _replace_active_session(self, old: _ActiveLink) -> None:
        self._active = None
        self._active_generation += 1
        with contextlib.suppress(Exception):
            await old.session.close("replaced")
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._stop_session_components(), timeout=4.0)

    def _session_callbacks(self) -> SessionCallbacks:
        return SessionCallbacks(
            on_input=self._on_remote_input,
            on_clipboard=self._on_remote_clipboard,
            on_focus_request=self._on_focus_request,
            on_focus_state=self._on_focus_state,
            on_log_request=self._on_remote_log_request,
            on_log_response=self._on_remote_log_response,
            on_closed=self._on_session_closed,
        )

    async def _adopt_host_session(
        self,
        link: AuthenticatedLink,
        record: PeerRecord,
    ) -> PeerSession:
        identity = self._ensure_identity()
        if not self._running or self._stopping:
            await link.stream.close()
            raise ServiceError("not_running")
        self.peer_store.mark_seen(
            record.peer_id,
            address=link.address,
            port=link.port,
        )
        record = self.peer_store.get(record.peer_id) or record
        session = PeerSession(
            link.stream,
            peer_id=record.peer_id,
            role="host",
            callbacks=self._session_callbacks(),
        )
        self._active = _ActiveLink(
            session=session,
            peer=record,
            role="host",
            address=link.address,
            port=link.port,
            app_version=link.peer_app_version,
            link=link,
            connected_at=iso_now(),
        )
        self._active_generation += 1
        self._focus_authority = FocusAuthority(
            host_id=identity.device_id,
            client_id=record.peer_id,
        )
        self._focus = self._focus_authority.initial()
        self._last_error = ""
        self._manual_disconnect = False
        self._reconnect = _ReconnectState()
        self._cancel_pairing_expiry()
        self._pairing_manager.cancel()
        session.start()
        if link.peer_app_version and link.peer_app_version != APP_VERSION:
            log.warning(
                "Peer %s runs ToRexLink %s but this device runs %s; "
                "update both computers for consistent focus handoff",
                record.name,
                link.peer_app_version,
                APP_VERSION,
            )
        await session.send_focus_state(self._focus_dict(self._focus))
        await self._start_session_components(initial_focus=self._focus)
        self._notify("connected", data={"peer_name": record.name})
        log.info("Host session established with %s", record.name)
        return session

    async def _establish_guest(
        self,
        record: PeerRecord,
        *,
        address: str | None = None,
        port: int | None = None,
    ) -> PeerSession:
        if self._active is not None:
            raise ServiceError("already_connected")
        self._status_override = "connecting"
        self._notify("connecting")
        try:
            link = await client_auth_exchange(
                record,
                self._ensure_identity(),
                name=self.local_name,
                platform=self.local_platform,
                host=address,
                port=port,
            )
        finally:
            self._status_override = None
        if not self._running or self._stopping:
            await link.stream.close()
            raise ServiceError("not_running")
        self.peer_store.mark_seen(record.peer_id, address=link.address, port=link.port)
        record = self.peer_store.get(record.peer_id) or record
        session = PeerSession(
            link.stream,
            peer_id=record.peer_id,
            role="guest",
            callbacks=self._session_callbacks(),
        )
        self._active = _ActiveLink(
            session=session,
            peer=record,
            role="guest",
            address=link.address,
            port=link.port,
            app_version=link.peer_app_version,
            link=link,
            connected_at=iso_now(),
        )
        self._active_generation += 1
        self._focus_authority = None
        self._focus = None
        self._last_error = ""
        self._manual_disconnect = False
        self._reconnect = _ReconnectState()
        self._settings = self.settings_store.update({"last_peer_id": record.peer_id})
        self._cancel_pairing_expiry()
        self._pairing_manager.cancel()
        session.start()
        if link.peer_app_version and link.peer_app_version != APP_VERSION:
            log.warning(
                "Peer %s runs ToRexLink %s but this device runs %s; "
                "update both computers for consistent focus handoff",
                record.name,
                link.peer_app_version,
                APP_VERSION,
            )
        await self._start_clipboard()
        self._notify("connected", data={"peer_name": record.name})
        log.info("Guest session established with %s", record.name)
        return session

    async def _start_session_components(self, *, initial_focus: FocusState | None = None) -> None:
        if initial_focus is not None:
            await self._init_input(initial_focus)
        await self._start_clipboard()
        self._notify("session_components")

    async def _stop_session_components(self) -> None:
        await self._stop_input()
        await self._stop_clipboard()
        self._input_error = ""
        self._clipboard_error = ""

    async def _activate_guest_focus_if_needed(self, state: FocusState) -> None:
        if self._bridge is None and self._active is not None:
            await self._init_input(state)

    # -------------------------------------------------------------- focus logic
    def _focus_dict(self, state: FocusState) -> dict[str, Any]:
        return {
            "epoch": state.epoch,
            "controller": state.controller,
            "target": state.target,
            "reason": state.reason,
            "entry_edge": state.entry_edge,
            "position": state.position,
        }

    def _state_from_message(self, message: dict[str, Any]) -> FocusState:
        return FocusState(
            epoch=int(message["epoch"]),
            controller=str(message["controller"]),
            target=str(message["target"]),
            reason=str(message["reason"]),
            entry_edge=message.get("entry_edge"),
            position=message.get("position"),
        )

    def _on_focus_request_from_bridge(
        self,
        action: str,
        edge: str | None,
        position: float | None,
    ) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(
                    self._local_focus_request(action, edge, position),
                    name="ToRexLink-Focus",
                )
            )

    async def _local_focus_request(
        self,
        action: str,
        edge: str | None,
        position: float | None,
    ) -> None:
        if self._active is None or action not in FOCUS_ACTIONS:
            return
        if self._active.role == "host":
            assert self._focus_authority is not None
            try:
                state = self._focus_authority.apply(
                    self._ensure_identity().device_id,
                    action,
                    edge=edge,
                    position=position,
                )
            except FocusError:
                return
            await self._publish_focus(state)
        else:
            request_id = secrets.token_hex(8)
            try:
                await self._active.session.send_focus_request(
                    action=action,
                    edge=edge,
                    position=position,
                    request_id=request_id,
                )
            except TransportError:
                return

    async def _publish_focus(self, state: FocusState) -> None:
        if self._active is None:
            return
        if self._active.role != "host":
            return
        try:
            await self._active.session.send_focus_state(self._focus_dict(state))
        except TransportError:
            return
        self._focus = state
        if self._bridge is not None:
            self._bridge.apply_focus(
                epoch=state.epoch,
                controller=state.controller,
                target=state.target,
                reason=state.reason,
                entry_edge=state.entry_edge,
                position=state.position,
            )
        self._notify("focus")

    async def _on_focus_request(self, message: dict[str, Any]) -> None:
        active = self._active
        if active is None or active.role != "host" or self._focus_authority is None:
            return
        now = time.monotonic()
        if now - self._last_focus_request < 0.04:
            return
        self._last_focus_request = now
        action = str(message.get("action"))
        edge = message.get("edge")
        position = message.get("position")
        if action not in FOCUS_ACTIONS:
            return
        if action in {"edge", "return"}:
            log.info("Peer focus request: action=%s edge=%s", action, edge)
        try:
            state = self._focus_authority.apply(
                active.peer.peer_id,
                action,
                edge=edge,
                position=position,
            )
        except FocusError:
            return
        await self._publish_focus(state)

    async def _on_focus_state(self, message: dict[str, Any]) -> None:
        active = self._active
        if active is None or active.role != "guest":
            return
        state = self._state_from_message(message)
        if state.controller not in {self._ensure_identity().device_id, active.peer.peer_id}:
            return
        if state.target not in {self._ensure_identity().device_id, active.peer.peer_id}:
            return
        current = self._focus
        if current is not None and state.epoch <= current.epoch:
            return
        self._focus = state
        if self._bridge is not None:
            self._bridge.apply_focus(
                epoch=state.epoch,
                controller=state.controller,
                target=state.target,
                reason=state.reason,
                entry_edge=state.entry_edge,
                position=state.position,
            )
        else:
            await self._activate_guest_focus_if_needed(state)
            if self._bridge is None:
                # Input is unavailable; state is still valid for the UI.
                pass
        self._notify("focus")

    # ------------------------------------------------------------- input engine
    async def _init_input(self, focus: FocusState) -> None:
        async with self._input_lock:
            active = self._active
            if active is None:
                return
            if self._bridge is not None:
                self._bridge.apply_focus(
                    epoch=focus.epoch,
                    controller=focus.controller,
                    target=focus.target,
                    reason=focus.reason,
                    entry_edge=focus.entry_edge,
                    position=focus.position,
                )
                return
            identity = self._ensure_identity()
            try:
                backend = self._input_backend_factory()
            except Exception as exc:  # noqa: BLE001 - OS APIs fail in many ways
                self._input_error = str(exc)
                log.warning("Native input backend unavailable: %s", exc)
                self._notify("input_unavailable", data={"detail": str(exc)})
                return
            bridge = InputBridge(
                self_id=identity.device_id,
                peer_id=active.peer.peer_id,
                backend=backend,
                callbacks=InputBridgeCallbacks(
                    send_input=self._post_input,
                    request_focus=self._on_focus_request_from_bridge,
                ),
                edge=self._settings.edge,
                edge_switch=self._settings.edge_switch,
                hotkey_switch=self._settings.hotkey_switch,
            )
            bridge.apply_focus(
                epoch=focus.epoch,
                controller=focus.controller,
                target=focus.target,
                reason=focus.reason,
                entry_edge=focus.entry_edge,
                position=focus.position,
            )
            try:
                await asyncio.to_thread(bridge.start)
            except Exception as exc:  # noqa: BLE001 - never break the TLS session
                self._input_error = str(exc)
                log.warning("Native input could not start: %s", exc)
                with contextlib.suppress(Exception):
                    bridge.stop()
                self._notify("input_unavailable", data={"detail": str(exc)})
                return
            if self._active is not active:
                with contextlib.suppress(Exception):
                    bridge.stop()
                return
            self._input_error = ""
            self._bridge = bridge
            self._notify("input_ready")

    async def _stop_input(self) -> None:
        async with self._input_lock:
            bridge = self._bridge
            self._bridge = None
            if bridge is not None:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(bridge.stop)

    def _post_input(self, wire_event: dict[str, Any]) -> None:
        active = self._active
        if active is None or active.session.closed:
            return
        if self._bridge is None:
            return
        active.session.post_input_threadsafe(wire_event)

    def _on_remote_input(self, wire_event: dict[str, Any]) -> None:
        bridge = self._bridge
        if bridge is None:
            return
        try:
            event = event_from_wire(wire_event)
        except (KeyError, TypeError, ValueError):
            return
        if not isinstance(event, InputMoveEvent) and self._focus is not None:
            if self._focus.target != self._ensure_identity().device_id:
                return
        bridge.handle_remote_event(event)

    # ----------------------------------------------------------------- clipboard
    async def _start_clipboard(self) -> None:
        if not self._settings.clipboard_sync or self._clipboard is not None:
            return
        if self._active is None:
            return
        try:
            native = self._clipboard_factory()
            clipboard = ClipboardSync(
                native,
                on_send=self._on_local_clipboard,
                on_remote_applied=self._on_clipboard_applied,
                on_skip=self._on_clipboard_skipped,
            )
            await clipboard.start()
        except Exception as exc:  # noqa: BLE001 - clipboard is optional
            self._clipboard_error = str(exc)
            log.warning("Clipboard sync unavailable: %s", exc)
            return
        self._clipboard = clipboard
        self._clipboard_error = ""
        self._notify("clipboard_started")

    async def _stop_clipboard(self) -> None:
        clipboard = self._clipboard
        self._clipboard = None
        if clipboard is not None:
            with contextlib.suppress(Exception):
                await clipboard.stop()

    def _on_local_clipboard(self, text: str, digest: str) -> None:
        active = self._active
        if active is None:
            return
        identity = self._identity
        if identity is None:
            return
        asyncio.create_task(
            self._send_clipboard(active, text, digest, identity.device_id),
            name="ToRexLink-ClipboardSend",
        )

    async def _send_clipboard(
        self,
        active: _ActiveLink,
        text: str,
        digest: str,
        origin: str,
    ) -> None:
        if self._active is not active or active.session.closed:
            return
        with contextlib.suppress(TransportError):
            await active.session.send_clipboard(text, digest, origin)

    def _on_remote_clipboard(self, text: str, digest: str, origin: str) -> None:
        clipboard = self._clipboard
        if clipboard is None:
            return
        identity = self._identity
        if identity is None or origin == identity.device_id:
            return
        asyncio.create_task(
            self._apply_clipboard(text, digest, origin),
            name="ToRexLink-ClipboardApply",
        )

    async def _apply_clipboard(self, text: str, digest: str, origin: str) -> None:
        clipboard = self._clipboard
        if clipboard is None:
            return
        applied = await clipboard.apply_remote(text, digest, origin)
        if applied:
            self._notify("clipboard_synced", data={"size": len(text.encode("utf-8"))})

    def _on_clipboard_applied(self, origin: str) -> None:
        return None

    def _on_clipboard_skipped(self, reason: str) -> None:
        self._notify("clipboard_skipped", data={"reason": reason})

    # -------------------------------------------------------------- disconnect
    async def disconnect(self) -> None:
        self._manual_disconnect = True
        task = self._auto_task
        self._auto_task = None
        if task is not None:
            task.cancel()
        if self._connect_task is not None:
            self._connect_task.cancel()
            self._connect_task = None
        active = self._active
        self._active = None
        if active is not None:
            with contextlib.suppress(Exception):
                await active.session.close("user")
        await self._stop_session_components()
        self._focus = None
        self._focus_authority = None
        self._reconnect = _ReconnectState()
        self._notify("disconnected")

    @property
    def manual_disconnect(self) -> bool:
        return self._manual_disconnect

    def _on_session_closed(self, session: PeerSession, reason: str) -> None:
        active = self._active
        if active is None or active.session is not session:
            return
        self._active = None
        self._active_generation += 1
        self._focus = None
        self._focus_authority = None
        self._fail_log_waiters()
        asyncio.create_task(
            self._finalize_closed_session(active, reason),
            name="ToRexLink-SessionCleanup",
        )

    async def _finalize_closed_session(self, active: _ActiveLink, reason: str) -> None:
        await self._stop_session_components()
        self._reconnect = _ReconnectState()
        if active.role == "guest" and self._settings.auto_reconnect and not self._manual_disconnect:
            self._set_error("connection_lost", reason)
        self._notify("disconnected", data={"reason": reason, "peer_name": active.peer.name})
        log.info("Connection ended with %s (%s)", active.peer.name, reason)
        if (
            active.role == "guest"
            and not self._manual_disconnect
            and self._settings.auto_reconnect
            and not self._stopping
        ):
            self._schedule_auto_connect(delay=0.4)

    async def connect_peer(self, peer_id: str) -> None:
        self._ensure_running()
        if self._pairing_in_progress:
            raise ServiceError("pairing_busy")
        if self._active is not None:
            raise ServiceError("already_connected")
        peer = self.peer_store.get(peer_id)
        if peer is None:
            raise ServiceError("peer_not_found")
        if not peer.outbound:
            raise ServiceError("not_outbound", "this device was paired by the peer")
        task = self._auto_task
        self._auto_task = None
        if task is not None:
            task.cancel()
        self._manual_disconnect = False
        self._settings = self.settings_store.update(
            {"preferred_peer_id": peer_id, "last_peer_id": peer_id}
        )
        await self._connect_peer_now(peer)

    async def _connect_peer_now(self, peer: PeerRecord) -> None:
        self._status_override = "connecting"
        self._notify("connecting")
        try:
            await self._establish_guest(peer)
        except (ReconnectAuthError, TransportError, ServiceError) as exc:
            self._set_error("connect_failed", str(exc))
            self._notify("connect_failed", data={"detail": str(exc)})
            raise ServiceError("connect_failed", str(exc)) from exc
        finally:
            self._status_override = None

    # ---------------------------------------------------------- auto reconnect
    def _schedule_auto_connect(self, *, delay: float = 0.0) -> None:
        if not self._running or self._stopping or not self._settings.auto_reconnect:
            return
        if self._active is not None or self._manual_disconnect:
            return
        task = self._auto_task
        if task is not None and not task.done():
            return
        self._auto_task = asyncio.create_task(
            self._auto_connect_loop(initial_delay=delay),
            name="ToRexLink-AutoReconnect",
        )

    def _preferred_outbound_peer(self) -> PeerRecord | None:
        wanted = self._settings.preferred_peer_id or self._settings.last_peer_id
        if wanted:
            peer = self.peer_store.get(wanted)
            if peer is not None and peer.outbound:
                return peer
        candidates = self.peer_store.outbound()
        return candidates[0] if candidates else None

    def _connection_candidates(self, peer: PeerRecord) -> list[tuple[str, int]]:
        candidates: list[tuple[str, int]] = []
        if peer.last_address and peer.last_port:
            candidates.append((peer.last_address, peer.last_port))
        discovered = self._discovered.get(peer.peer_id)
        if discovered is not None:
            for address in discovered.addresses:
                item = (address, discovered.port)
                if item not in candidates:
                    candidates.append(item)
        if not candidates and peer.last_port:
            for address in self._address_provider():
                item = (address, peer.last_port)
                if item not in candidates:
                    candidates.append(item)
        return candidates

    async def _wait_cancellable(self, delay: float, next_retry_at: float) -> None:
        self._reconnect.next_retry_at = time.time() + delay
        self._notify("reconnecting")
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        finally:
            self._reconnect.next_retry_at = None

    async def _auto_connect_loop(self, *, initial_delay: float = 0.0) -> None:
        try:
            if initial_delay:
                await asyncio.sleep(initial_delay)
            while (
                self._running
                and not self._stopping
                and self._active is None
                and not self._manual_disconnect
                and self._settings.auto_reconnect
            ):
                peer = self._preferred_outbound_peer()
                if peer is None:
                    return
                self._reconnect.attempt += 1
                self._status_override = "reconnecting"
                self._notify("reconnecting")
                attempted_any = False
                candidates = self._connection_candidates(peer)
                for address, port in candidates[:3]:
                    attempted_any = True
                    try:
                        await self._establish_guest(peer, address=address, port=port)
                        self._last_error = ""
                        return
                    except ReconnectAuthError as exc:
                        self._last_error = str(exc)
                        if exc.permanent:
                            self._status_override = None
                            self._set_error("auth_failed", str(exc))
                            self._notify("connect_failed", data={"detail": str(exc)})
                            return
                    except (TransportError, ServiceError) as exc:
                        self._last_error = str(exc)
                if not attempted_any:
                    self._last_error = "peer address is not known yet"
                self._status_override = "reconnecting"
                delay = backoff_delay(
                    self._reconnect.attempt - 1,
                    RECONNECT_DELAYS,
                    RECONNECT_JITTER,
                )
                await self._wait_cancellable(delay, time.time() + delay)
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            log.exception("Auto-reconnect loop failed")
        finally:
            self._status_override = None
            self._auto_task = None
            if (
                self._active is None
                and not self._manual_disconnect
                and not self._stopping
                and self._running
                and self._settings.auto_reconnect
            ):
                # The loop only exits on cancellation/permanent failure; if it
                # ended without a connection, leave control to the UI.
                self._notify("reconnect_stopped")

    # ------------------------------------------------------------- peer actions
    async def remove_peer(self, peer_id: str) -> None:
        task = self._auto_task
        self._auto_task = None
        if task is not None:
            task.cancel()
        if self._active is not None and self._active.peer.peer_id == peer_id:
            await self.disconnect()
        self.peer_store.remove(peer_id)
        if self._settings.preferred_peer_id == peer_id:
            self._settings = self.settings_store.update({"preferred_peer_id": None})
        if self._settings.last_peer_id == peer_id:
            self._settings = self.settings_store.update({"last_peer_id": None})
        self._notify("peer_removed")

    async def clear_peers(self) -> None:
        task = self._auto_task
        self._auto_task = None
        if task is not None:
            task.cancel()
        if self._active is not None:
            await self.disconnect()
        self.peer_store.clear()
        self._settings = self.settings_store.update(
            {"preferred_peer_id": None, "last_peer_id": None}
        )
        self._notify("peers_cleared")

    # ----------------------------------------------------------------- settings
    async def update_settings(self, changes: dict[str, Any]) -> Settings:
        old = self._settings
        new = self.settings_store.update(changes)
        self._settings = new
        if "hosting_enabled" in changes and old.hosting_enabled != new.hosting_enabled:
            if new.hosting_enabled:
                await self._start_hosting()
            else:
                if self._active is not None and self._active.role == "host":
                    with contextlib.suppress(Exception):
                        await self._active.session.close("hosting_disabled")
                await self._stop_hosting()
        if "auto_reconnect" in changes:
            if new.auto_reconnect and not self._manual_disconnect and self._active is None:
                self._schedule_auto_connect(delay=0.2)
            elif not new.auto_reconnect:
                task = self._auto_task
                self._auto_task = None
                if task is not None:
                    task.cancel()
        if self._bridge is not None:
            self._bridge.update_options(
                edge=new.edge,
                edge_switch=new.edge_switch,
                hotkey_switch=new.hotkey_switch,
            )
        if "clipboard_sync" in changes and old.clipboard_sync != new.clipboard_sync:
            if new.clipboard_sync:
                await self._start_clipboard()
            else:
                await self._stop_clipboard()
        if "device_name" in changes and new.device_name and self._server is not None:
            identity = self._ensure_identity()
            if self._server.port and self._discovery is not None:
                self._mdns_active = await self._discovery.advertise(
                    port=self._server.port,
                    name=self.local_name,
                    platform=self.local_platform,
                    fingerprint=identity.fingerprint,
                )
            if self._pairing_manager.offer is not None:
                await self.cancel_pairing()
        self._notify("settings")
        return new

    # ----------------------------------------------------------------- snapshot
    def _status(self) -> str:
        if not self._running:
            return "offline"
        if self._status_override:
            return self._status_override
        if self._active is not None:
            return "connected"
        if self._pairing_manager.offer is not None:
            return "pairing"
        if self._auto_task is not None and not self._auto_task.done():
            return "reconnecting"
        if self._listener_error:
            return "error"
        if self._settings.hosting_enabled:
            return "hosting"
        return "offline"

    def _status_detail(self) -> str:
        if self._last_error:
            return self._last_error
        if self._pairing_manager.offer is not None:
            return "pairing_code_active"
        if self._active is not None:
            return self._active.peer.name
        if self._listener_error:
            return self._listener_error
        return ""

    def _connection_info(self) -> ConnectionInfo | None:
        active = self._active
        if active is None:
            return None
        bridge_started = False
        if self._bridge is not None:
            bridge_started = bool(self._bridge.status.get("started"))
        return ConnectionInfo(
            peer_id=active.peer.peer_id,
            name=active.peer.name,
            platform=active.peer.platform,
            app_version=active.app_version,
            role=active.role,
            address=active.address,
            port=active.port,
            connected_at=active.connected_at,
            tls_version="TLS 1.3",
            latency_ms=active.session.latency_ms,
            bytes_sent=active.session.bytes_sent,
            bytes_received=active.session.bytes_received,
            input_ready=bridge_started,
            input_error=self._input_error,
            clipboard_active=self._clipboard is not None,
            fingerprint_short=active.peer.display_fingerprint(),
        )

    def _focus_info(self) -> FocusInfo | None:
        active = self._active
        state = self._focus
        if active is None or state is None or self._identity is None:
            return None
        local = self._identity.device_id
        controller_name = self.local_name if state.controller == local else active.peer.name
        target_name = self.local_name if state.target == local else active.peer.name
        return FocusInfo(
            epoch=state.epoch,
            controller_id=state.controller,
            target_id=state.target,
            controller_name=controller_name,
            target_name=target_name,
            local_controller=state.controller == local,
            local_target=state.target == local,
            reason=state.reason,
            entry_edge=state.entry_edge,
        )

    def _local_info(self) -> LocalDeviceInfo | None:
        if self._identity is None:
            return None
        return LocalDeviceInfo(
            device_id=self._identity.device_id,
            name=self.local_name,
            platform=self.local_platform,
            fingerprint=self._identity.fingerprint,
            addresses=self._network_addresses,
            hosting_port=self._server.port if self._server else None,
        )

    def _peer_views(self) -> tuple[PeerView, ...]:
        connected_id = self._active.peer.peer_id if self._active else None
        records = sorted(
            self.peer_store.peers,
            key=lambda peer: peer.last_seen or peer.paired_at,
            reverse=True,
        )
        return tuple(
            PeerView(
                peer_id=peer.peer_id,
                name=peer.name,
                platform=peer.platform,
                fingerprint=peer.fingerprint,
                fingerprint_short=short_fingerprint(peer.fingerprint),
                paired_at=peer.paired_at,
                last_seen=peer.last_seen,
                outbound=peer.outbound,
                connected=peer.peer_id == connected_id,
                connection_role=self._active.role if connected_id == peer.peer_id and self._active else None,
                address=peer.last_address,
                port=peer.last_port,
                app_version=peer.app_version,
            )
            for peer in records
        )

    def _discovered_views(self) -> tuple[DiscoveredView, ...]:
        paired_ids = {peer.peer_id for peer in self.peer_store.peers}
        return tuple(
            DiscoveredView(
                device_id=device.device_id,
                name=device.name,
                platform=device.platform,
                address=device.address,
                port=device.port,
                fingerprint=device.fingerprint,
                paired=device.device_id in paired_ids,
                app_version=device.app_version,
            )
            for device in sorted(self._discovered.values(), key=lambda item: item.name.lower())
            if device.device_id != (self._identity.device_id if self._identity else "")
        )

    def snapshot(self) -> ServiceSnapshot:
        self._seq += 1
        pairing = self._pairing_info()
        snapshot = ServiceSnapshot(
            seq=self._seq,
            running=self._running,
            status=self._status(),
            status_detail=self._status_detail(),
            hosting_enabled=bool(self._server and self._server.running),
            listener_port=self._server.port if self._server else None,
            listener_error=self._listener_error,
            mdns_active=self._mdns_active,
            local=self._local_info(),
            pairing=pairing,
            connection=self._connection_info(),
            focus=self._focus_info(),
            discovered=self._discovered_views(),
            peers=self._peer_views(),
            reconnect_attempt=self._reconnect.attempt,
            next_retry_at=(
                iso_now_from_epoch(self._reconnect.next_retry_at)
                if self._reconnect.next_retry_at
                else None
            ),
            last_error=self._last_error,
            clipboard_enabled=self._settings.clipboard_sync,
        )
        self._last_snapshot = snapshot
        return snapshot

    def _notify(self, kind: str, data: dict[str, object] | None = None) -> None:
        callback = self._on_event
        if callback is None:
            return
        try:
            snapshot = self.snapshot()
            callback(ServiceEvent(kind=kind, snapshot=snapshot, data=data or {}))
        except Exception:  # noqa: BLE001 - UI must never break the core
            log.debug("Service event callback failed", exc_info=True)

    def _set_error(self, code: str, detail: str) -> None:
        self._last_error = f"{code}: {detail}" if detail else code
        log.warning("Service error [%s]: %s", code, detail)

    def clear_error(self) -> None:
        self._last_error = ""
        self._notify("error_cleared")

    @property
    def last_snapshot(self) -> ServiceSnapshot | None:
        return self._last_snapshot


def iso_now_from_epoch(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    from datetime import UTC, datetime

    try:
        return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):
        return None


__all__ = ["RemoteLogs", "ServiceError", "ServiceEvent", "ServiceSnapshot", "ToRexLinkService"]
