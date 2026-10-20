from __future__ import annotations

import json
import os
import stat

import pytest

from torexlink.peers import PeerRecord, PeerStore
from torexlink.security.identity import Identity, PeerIdentity
from torexlink.settings import SettingsStore


def test_settings_roundtrip_and_validation(tmp_path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.settings.language == "uz"
    updated = store.update({"language": "ru", "theme": "dark", "edge": "left"})
    assert updated.language == "ru"
    assert updated.theme == "dark"
    reloaded = SettingsStore(path)
    assert reloaded.settings.language == "ru"
    assert reloaded.settings.edge == "left"
    with pytest.raises(ValueError):
        store.update({"theme": "neon"})
    with pytest.raises(ValueError):
        store.update({"edge": "diagonal"})
    assert store.update({"language": "xx"}).language == "uz"
    assert store.update({"unknown": "ignored"}).edge == "left"


def test_settings_corrupt_backup(tmp_path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{broken", encoding="utf-8")
    store = SettingsStore(path)
    assert store.load().language == "uz"
    backups = list(tmp_path.glob("settings.corrupt-*.json"))
    assert len(backups) == 1


def test_peer_store_roundtrip_and_permissions(tmp_path) -> None:
    id2 = Identity.load_or_create(tmp_path / "b.pem", tmp_path / "b-key.pem")
    record = PeerRecord.from_pairing(
        PeerIdentity.from_pem(id2.cert_pem),
        name="Peer",
        platform="macos",
        token=b"x" * 32,
        outbound=True,
        address="192.168.1.2",
        port=48620,
    )
    path = tmp_path / "peers.json"
    store = PeerStore(path)
    store.upsert(record)
    reloaded = PeerStore(path)
    loaded = reloaded.get(record.peer_id)
    assert loaded is not None
    assert loaded.token == b"x" * 32
    assert loaded.outbound is True
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    reloaded.mark_seen(record.peer_id, address="192.168.1.3", port=50000)
    assert PeerStore(path).get(record.peer_id).last_address == "192.168.1.3"  # type: ignore[union-attr]
    assert reloaded.remove(record.peer_id)
    assert not reloaded.remove(record.peer_id)


def test_invalid_peer_records_are_ignored(tmp_path) -> None:
    path = tmp_path / "peers.json"
    path.write_text(json.dumps({"peers": [{"peer_id": "bad"}]}), encoding="utf-8")
    store = PeerStore(path)
    assert store.peers == []
