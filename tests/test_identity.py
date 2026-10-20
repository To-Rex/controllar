from __future__ import annotations

import os
import stat

from torexlink.security.identity import (
    Identity,
    PeerIdentity,
    normalize_fingerprint,
    short_fingerprint,
)


def test_identity_generation_and_persistence(tmp_path) -> None:
    cert = tmp_path / "certs" / "device.pem"
    key = tmp_path / "certs" / "device-key.pem"
    identity = Identity.load_or_create(cert, key)
    assert cert.exists() and key.exists()
    if os.name != "nt":
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert identity.verify_own_key()
    assert normalize_fingerprint(identity.fingerprint) == identity.device_id
    peer = PeerIdentity.from_pem(identity.cert_pem)
    message = b"hello ToRexLink"
    signature = identity.sign(message)
    assert peer.verify(message, signature)
    assert not peer.verify(message + b"!", signature)
    again = Identity.load_or_create(cert, key)
    assert again.device_id == identity.device_id
    assert again.cert_pem == identity.cert_pem


def test_short_fingerprint() -> None:
    value = ":".join(["AB"] * 32)
    rendered = short_fingerprint(value)
    assert rendered.endswith("…")
    assert "ABAB" in rendered
