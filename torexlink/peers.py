"""Paired device records, including long-term authentication tokens.

This file is private to the operating-system user (mode 0600). Tokens are never
written to logs, shown in the UI, or sent over the network.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .constants import MAX_DEVICE_NAME, MAX_PEERS, TOKEN_BYTES
from .security.identity import PeerIdentity, normalize_fingerprint
from .util import atomic_write_json, b64decode, b64e, iso_now, read_json

log = logging.getLogger(__name__)


class PeerStoreError(RuntimeError):
    pass


@dataclass(slots=True)
class PeerRecord:
    peer_id: str
    name: str
    platform: str
    fingerprint: str
    cert_pem: str
    token_b64: str
    paired_at: str
    outbound: bool
    last_seen: str | None = None
    last_address: str | None = None
    last_port: int | None = None
    app_version: str | None = None

    @property
    def token(self) -> bytes:
        return b64decode(self.token_b64, expected_length=TOKEN_BYTES)

    @property
    def identity(self) -> PeerIdentity:
        identity = PeerIdentity.from_pem(self.cert_pem)
        if identity.device_id != self.peer_id:
            raise ValueError("stored peer id does not match certificate")
        return identity

    @property
    def initial(self) -> str:
        return (self.name or "?").strip()[:1].upper() or "?"

    def display_fingerprint(self) -> str:
        try:
            compact = normalize_fingerprint(self.fingerprint).upper()
        except ValueError:
            return self.fingerprint
        groups = " ".join(compact[i : i + 4] for i in range(0, 24, 4))
        return groups + "…"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> PeerRecord:
        required = {
            "peer_id": str,
            "name": str,
            "platform": str,
            "fingerprint": str,
            "cert_pem": str,
            "token_b64": str,
            "paired_at": str,
            "outbound": bool,
        }
        for key, expected in required.items():
            if not isinstance(raw.get(key), expected):
                raise ValueError(f"invalid peer field: {key}")
        identity = PeerIdentity.from_pem(raw["cert_pem"])
        if identity.device_id != raw["peer_id"]:
            raise ValueError("peer certificate fingerprint mismatch")
        if normalize_fingerprint(raw["fingerprint"]) != identity.device_id:
            raise ValueError("peer fingerprint mismatch")
        b64decode(raw["token_b64"], expected_length=TOKEN_BYTES)
        name = " ".join(str(raw["name"]).split())[:MAX_DEVICE_NAME] or "ToRexLink"
        port = raw.get("last_port")
        if port is not None and (not isinstance(port, int) or not 1 <= port <= 65535):
            port = None
        address = raw.get("last_address")
        if address is not None and not isinstance(address, str):
            address = None
        return cls(
            peer_id=raw["peer_id"],
            name=name,
            platform=raw["platform"][:32],
            fingerprint=raw["fingerprint"],
            cert_pem=raw["cert_pem"],
            token_b64=raw["token_b64"],
            paired_at=raw["paired_at"],
            outbound=bool(raw["outbound"]),
            last_seen=raw.get("last_seen") if isinstance(raw.get("last_seen"), str) else None,
            last_address=address,
            last_port=port,
            app_version=raw.get("app_version") if isinstance(raw.get("app_version"), str) else None,
        )

    @classmethod
    def from_pairing(
        cls,
        identity: PeerIdentity,
        *,
        name: str,
        platform: str,
        token: bytes,
        outbound: bool,
        address: str | None = None,
        port: int | None = None,
        app_version: str | None = None,
    ) -> PeerRecord:
        if len(token) != TOKEN_BYTES:
            raise ValueError("invalid pairing token")
        return cls(
            peer_id=identity.device_id,
            name=" ".join(name.split())[:MAX_DEVICE_NAME] or "ToRexLink",
            platform=platform[:32],
            fingerprint=identity.fingerprint,
            cert_pem=identity.cert_pem,
            token_b64=b64e(token),
            paired_at=iso_now(),
            outbound=outbound,
            last_seen=iso_now(),
            last_address=address,
            last_port=port,
            app_version=app_version,
        )


class PeerStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._peers: dict[str, PeerRecord] | None = None

    @property
    def peers(self) -> list[PeerRecord]:
        return list(self._load().values())

    def get(self, peer_id: str) -> PeerRecord | None:
        return self._load().get(peer_id)

    def outbound(self) -> list[PeerRecord]:
        return sorted(
            (peer for peer in self._load().values() if peer.outbound),
            key=lambda peer: peer.last_seen or peer.paired_at,
            reverse=True,
        )

    def upsert(self, record: PeerRecord) -> None:
        peers = self._load()
        if record.peer_id not in peers and len(peers) >= MAX_PEERS:
            raise PeerStoreError("paired-device limit reached")
        # Validate before writing; this catches a corrupt certificate or token.
        _ = record.identity
        _ = record.token
        peers[record.peer_id] = record
        self._save(peers)

    def remove(self, peer_id: str) -> bool:
        peers = self._load()
        if peer_id not in peers:
            return False
        del peers[peer_id]
        self._save(peers)
        return True

    def mark_seen(
        self,
        peer_id: str,
        *,
        address: str | None = None,
        port: int | None = None,
    ) -> None:
        peers = self._load()
        record = peers.get(peer_id)
        if record is None:
            return
        record.last_seen = iso_now()
        if address:
            record.last_address = address
        if port:
            record.last_port = port
        self._save(peers)

    def clear(self) -> None:
        self._peers = {}
        self._save(self._peers)

    def _load(self) -> dict[str, PeerRecord]:
        if self._peers is not None:
            return self._peers
        self._peers = {}
        if not self.path.exists():
            return self._peers
        try:
            raw = read_json(self.path)
            items = raw.get("peers", []) if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                raise ValueError("peers must be a list")
            for item in items:
                if not isinstance(item, dict):
                    continue
                try:
                    record = PeerRecord.from_dict(item)
                except (ValueError, TypeError, KeyError) as exc:
                    log.warning("Skipping invalid paired-device record: %s", exc)
                    continue
                self._peers[record.peer_id] = record
        except (OSError, ValueError, TypeError) as exc:
            log.warning("Peer store unreadable: %s", exc)
            try:
                import time

                self.path.replace(
                    self.path.with_suffix(f".corrupt-{int(time.time())}.json")
                )
            except OSError:
                pass
            self._peers = {}
        return self._peers

    def _save(self, peers: dict[str, PeerRecord]) -> None:
        payload = {
            "version": 1,
            "peers": [record.to_dict() for record in peers.values()],
        }
        atomic_write_json(self.path, payload, private=True)


__all__ = ["PeerRecord", "PeerStore", "PeerStoreError"]
