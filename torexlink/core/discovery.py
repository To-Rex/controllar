"""mDNS device discovery and advertisement.

Discovery is a convenience, never a trust mechanism. A discovered device still
has to be paired with a code (or QR), and authenticated reconnects pin the
certificate fingerprint.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..constants import APP_VERSION, SERVICE_TYPE

log = logging.getLogger(__name__)


def local_ipv4_addresses() -> tuple[str, ...]:
    """Best-effort LAN addresses without relying on the default route only."""
    addresses: list[str] = []
    try:
        import ifaddr  # dependency of zeroconf

        for adapter in ifaddr.get_adapters():
            for ip_info in adapter.ips:
                if not ip_info.is_IPv4:
                    continue
                address = str(ip_info.ip)
                if address.startswith(("127.", "169.254.")):
                    continue
                if address not in addresses:
                    addresses.append(address)
    except Exception:  # noqa: BLE001 - optional discovery helper
        pass
    if not addresses:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("192.0.2.1", 9))
                address = sock.getsockname()[0]
                if not address.startswith(("127.", "169.254.")):
                    addresses.append(address)
        except OSError:
            pass
    return tuple(addresses)


@dataclass(frozen=True, slots=True)
class DiscoveredDevice:
    device_id: str
    name: str
    platform: str
    address: str
    addresses: tuple[str, ...]
    port: int
    fingerprint: str
    app_version: str


class DiscoveryService:
    def __init__(self) -> None:
        self._zeroconf: Any | None = None
        self._browser: Any | None = None
        self._service_info: Any | None = None
        self._devices: dict[str, DiscoveredDevice] = {}
        self._resolving: set[str] = set()
        self._on_change: Callable[[list[DiscoveredDevice]], None] | None = None
        self._identity_id: str = ""
        self._advertised_port: int | None = None
        self.running = False

    def set_callback(self, callback: Callable[[list[DiscoveredDevice]], None]) -> None:
        self._on_change = callback

    @property
    def devices(self) -> list[DiscoveredDevice]:
        return sorted(self._devices.values(), key=lambda item: item.name.lower())

    async def start(self, device_id: str) -> bool:
        self._identity_id = device_id
        if self.running:
            return True
        try:
            from zeroconf import IPVersion
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except ImportError:
            log.warning("zeroconf is unavailable; manual IP connection remains enabled")
            return False
        try:
            self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
            self._browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                SERVICE_TYPE,
                handlers=[self._on_service_state_change],
            )
            self.running = True
            log.info("mDNS discovery started")
            return True
        except Exception as exc:  # noqa: BLE001 - don't take down networking
            log.warning("mDNS discovery could not start: %s", exc)
            await self._close_browser()
            return False

    def _on_service_state_change(
        self,
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: Any,
    ) -> None:
        from zeroconf import ServiceStateChange

        if state_change is ServiceStateChange.Removed:
            device_id = None
            for known_id in list(self._devices):
                if name.startswith(f"ToRexLink-{known_id[:8]}"):
                    device_id = known_id
                    break
            if device_id:
                self._devices.pop(device_id, None)
                self._notify()
            return
        if state_change in (ServiceStateChange.Added, ServiceStateChange.Updated):
            try:
                asyncio.get_running_loop().create_task(self._resolve(name, zeroconf))
            except RuntimeError:
                pass

    async def _resolve(self, service_name: str, zeroconf: Any) -> None:
        if service_name in self._resolving:
            return
        self._resolving.add(service_name)
        try:
            from zeroconf.asyncio import AsyncServiceInfo

            info = AsyncServiceInfo(SERVICE_TYPE, service_name)
            if not await info.async_request(zeroconf, 2500):
                return
            properties = {
                key.decode("utf-8", "replace"): value.decode("utf-8", "replace")
                for key, value in (info.properties or {}).items()
                if value is not None
            }
            device_id = properties.get("id", "")
            if not device_id or len(device_id) != 64 or device_id == self._identity_id:
                return
            try:
                port = int(info.port or 0)
            except (TypeError, ValueError):
                return
            addresses = tuple(
                address
                for address in info.parsed_addresses()
                if ":" not in address and not address.startswith("127.")
            )
            if not addresses or not 1 <= port <= 65535:
                return
            name = properties.get("name", service_name.split(".")[0])[:64]
            fingerprint = properties.get("fp", "")
            if not fingerprint:
                return
            device = DiscoveredDevice(
                device_id=device_id,
                name=name or "ToRexLink",
                platform=properties.get("platform", "unknown")[:32],
                address=addresses[0],
                addresses=addresses,
                port=port,
                fingerprint=fingerprint,
                app_version=properties.get("version", "")[:32] or APP_VERSION,
            )
            previous = self._devices.get(device_id)
            self._devices[device_id] = device
            if previous != device:
                log.info("Discovered ToRexLink device: %s", name)
                self._notify()
        except Exception as exc:  # noqa: BLE001
            log.debug("mDNS resolve failed for %s: %s", service_name, exc)
        finally:
            self._resolving.discard(service_name)

    async def advertise(
        self,
        *,
        port: int,
        name: str,
        platform: str,
        fingerprint: str,
    ) -> bool:
        if self._zeroconf is None:
            return False
        try:
            from zeroconf import ServiceInfo

            if self._service_info is not None and self._advertised_port == port:
                return True
            await self.unadvertise()
            addresses = local_ipv4_addresses()
            if not addresses:
                log.warning("No LAN IPv4 address available for mDNS advertisement")
                return False
            import socket as _socket

            properties = {
                b"id": self._identity_id.encode("ascii"),
                b"name": name[:64].encode("utf-8"),
                b"platform": platform.encode("ascii", "replace"),
                b"version": APP_VERSION.encode("ascii"),
                b"fp": fingerprint.encode("ascii"),
            }
            hostname = _socket.gethostname().split(".")[0][:32] or "torexlink"
            info = ServiceInfo(
                SERVICE_TYPE,
                f"ToRexLink-{self._identity_id[:8]}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(address) for address in addresses[:8]],
                port=port,
                properties=properties,
                server=f"{hostname}-{self._identity_id[:4]}.local.",
            )
            await self._zeroconf.async_register_service(info, allow_name_change=True)
            self._service_info = info
            self._advertised_port = port
            log.info("mDNS advertisement active on port %s", port)
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("mDNS advertisement failed: %s", exc)
            return False

    async def unadvertise(self) -> None:
        if self._zeroconf is None or self._service_info is None:
            self._service_info = None
            self._advertised_port = None
            return
        with contextlib.suppress(Exception):
            await self._zeroconf.async_unregister_service(self._service_info)
        self._service_info = None
        self._advertised_port = None

    async def stop(self) -> None:
        self.running = False
        await self.unadvertise()
        await self._close_browser()
        self._devices.clear()
        self._resolving.clear()
        self._notify()

    async def _close_browser(self) -> None:
        browser = self._browser
        self._browser = None
        if browser is not None:
            with contextlib.suppress(Exception):
                await browser.async_cancel()
        zeroconf = self._zeroconf
        self._zeroconf = None
        if zeroconf is not None:
            with contextlib.suppress(Exception):
                await zeroconf.async_close()

    def _notify(self) -> None:
        if self._on_change is not None:
            with contextlib.suppress(Exception):
                self._on_change(self.devices)


__all__ = ["DiscoveredDevice", "DiscoveryService", "local_ipv4_addresses"]
