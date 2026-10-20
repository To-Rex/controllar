"""QR / copy-paste pairing payloads.

A payload includes the one-time six digit code, endpoint hints, and the host
certificate fingerprint. Treat it as a secret until it expires: anyone who sees
it during an active pairing window can attempt to pair.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, unquote, urlparse

from .constants import PAIRING_PAYLOAD_PREFIX, PROTOCOL_VERSION
from .security.identity import normalize_fingerprint
from .util import b64decode, b64e, canonical_json_bytes

_CODE_RE = re.compile(r"^\d{6}$")


class PairingPayloadError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PairingPayload:
    code: str
    fingerprint: str
    device_id: str
    name: str
    port: int
    addresses: tuple[str, ...]

    @property
    def primary_address(self) -> str:
        if not self.addresses:
            raise PairingPayloadError("payload has no network address")
        return self.addresses[0]

    def to_uri(self) -> str:
        payload = {
            "v": PROTOCOL_VERSION,
            "code": self.code,
            "fp": self.fingerprint,
            "id": self.device_id,
            "name": self.name,
            "port": self.port,
            "addrs": list(self.addresses),
        }
        encoded = b64e(canonical_json_bytes(payload))
        return f"{PAIRING_PAYLOAD_PREFIX}?d={quote(encoded, safe='')}"

    def to_json(self) -> str:
        return json.dumps(
            {
                "type": "torexlink-pairing",
                "v": PROTOCOL_VERSION,
                "code": self.code,
                "fingerprint": self.fingerprint,
                "device_id": self.device_id,
                "name": self.name,
                "port": self.port,
                "addresses": list(self.addresses),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def parse(cls, value: str) -> PairingPayload:
        if not isinstance(value, str):
            raise PairingPayloadError("pairing payload must be text")
        text = value.strip()
        if len(text) > 16_384:
            raise PairingPayloadError("pairing payload is too large")
        if text.startswith("torexlink://"):
            return cls._parse_uri(text)
        if text.startswith("{"):
            return cls._parse_json(text)
        raise PairingPayloadError("unrecognized pairing payload")

    @classmethod
    def _parse_uri(cls, text: str) -> PairingPayload:
        parsed = urlparse(text)
        if f"{parsed.scheme}://{parsed.netloc}" != "torexlink://pair":
            raise PairingPayloadError("unsupported ToRexLink link")
        values = parse_qs(parsed.query)
        encoded = (values.get("d") or [""])[0]
        if not encoded:
            raise PairingPayloadError("missing pairing data")
        try:
            raw = b64decode(unquote(encoded))
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PairingPayloadError("invalid pairing link data") from exc
        if not isinstance(data, dict):
            raise PairingPayloadError("invalid pairing data")
        return cls._from_mapping(data, uri_style=True)

    @classmethod
    def _parse_json(cls, text: str) -> PairingPayload:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PairingPayloadError("invalid pairing JSON") from exc
        if not isinstance(data, dict):
            raise PairingPayloadError("invalid pairing JSON")
        return cls._from_mapping(data, uri_style=False)

    @classmethod
    def _from_mapping(cls, data: dict[str, object], *, uri_style: bool) -> PairingPayload:
        if data.get("v") != PROTOCOL_VERSION:
            raise PairingPayloadError("unsupported pairing payload version")
        code = data.get("code")
        if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
            raise PairingPayloadError("pairing code must contain 6 digits")
        fingerprint_raw = data.get("fp") if uri_style else data.get("fingerprint")
        try:
            fingerprint = normalize_fingerprint(str(fingerprint_raw))
        except (ValueError, TypeError) as exc:
            raise PairingPayloadError("invalid certificate fingerprint") from exc
        device_id = data.get("id") if uri_style else data.get("device_id")
        if not isinstance(device_id, str) or device_id.lower() != fingerprint:
            raise PairingPayloadError("pairing device id mismatch")
        name = data.get("name")
        if not isinstance(name, str) or not name or len(name) > 64:
            raise PairingPayloadError("invalid device name")
        port = data.get("port")
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise PairingPayloadError("invalid port")
        raw_addresses = data.get("addrs") if uri_style else data.get("addresses")
        if not isinstance(raw_addresses, list) or not 1 <= len(raw_addresses) <= 16:
            raise PairingPayloadError("missing network address")
        addresses: list[str] = []
        for raw_address in raw_addresses:
            if not isinstance(raw_address, str):
                continue
            candidate = raw_address.split("%", 1)[0].strip()
            try:
                parsed_ip = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed_ip.is_unspecified or parsed_ip.is_multicast or parsed_ip.is_link_local:
                continue
            normalized = str(parsed_ip)
            if normalized not in addresses:
                addresses.append(normalized)
        if not addresses:
            raise PairingPayloadError("no usable network address")
        return cls(
            code=code,
            fingerprint=fingerprint,
            device_id=device_id.lower(),
            name=" ".join(name.split())[:64],
            port=port,
            addresses=tuple(addresses[:8]),
        )


__all__ = ["PairingPayload", "PairingPayloadError"]
