"""Immutable UI-facing state snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class LocalDeviceInfo:
    device_id: str
    name: str
    platform: str
    fingerprint: str
    addresses: tuple[str, ...]
    hosting_port: int | None


@dataclass(frozen=True, slots=True)
class PairingInfo:
    code: str
    qr_uri: str
    expires_at: float
    seconds_remaining: int
    attempts_remaining: int
    addresses: tuple[str, ...]
    port: int


@dataclass(frozen=True, slots=True)
class ConnectionInfo:
    peer_id: str
    name: str
    platform: str
    app_version: str
    role: str  # "host" or "guest"
    address: str
    port: int
    connected_at: str
    tls_version: str
    latency_ms: int
    bytes_sent: int
    bytes_received: int
    input_ready: bool
    input_error: str
    clipboard_active: bool
    fingerprint_short: str


@dataclass(frozen=True, slots=True)
class FocusInfo:
    epoch: int
    controller_id: str
    target_id: str
    controller_name: str
    target_name: str
    local_controller: bool
    local_target: bool
    reason: str
    entry_edge: str | None


@dataclass(frozen=True, slots=True)
class DiscoveredView:
    device_id: str
    name: str
    platform: str
    address: str
    port: int
    fingerprint: str
    paired: bool
    app_version: str


@dataclass(frozen=True, slots=True)
class PeerView:
    peer_id: str
    name: str
    platform: str
    fingerprint: str
    fingerprint_short: str
    paired_at: str
    last_seen: str | None
    outbound: bool
    connected: bool
    connection_role: str | None
    address: str | None
    port: int | None
    app_version: str | None


@dataclass(frozen=True, slots=True)
class ServiceSnapshot:
    seq: int = 0
    running: bool = False
    status: str = "offline"
    status_detail: str = ""
    hosting_enabled: bool = False
    listener_port: int | None = None
    listener_error: str = ""
    mdns_active: bool = False
    local: LocalDeviceInfo | None = None
    pairing: PairingInfo | None = None
    connection: ConnectionInfo | None = None
    focus: FocusInfo | None = None
    discovered: tuple[DiscoveredView, ...] = ()
    peers: tuple[PeerView, ...] = ()
    reconnect_attempt: int = 0
    next_retry_at: str | None = None
    last_error: str = ""
    clipboard_enabled: bool = True


@dataclass(frozen=True, slots=True)
class ServiceEvent:
    kind: str
    snapshot: ServiceSnapshot
    data: dict[str, object] = field(default_factory=dict)


__all__ = [
    "ConnectionInfo",
    "DiscoveredView",
    "FocusInfo",
    "LocalDeviceInfo",
    "PairingInfo",
    "PeerView",
    "ServiceEvent",
    "ServiceSnapshot",
]
