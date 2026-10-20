from __future__ import annotations

import json

import pytest

from torexlink.pairing_payload import PairingPayload, PairingPayloadError


def payload(**overrides) -> PairingPayload:
    values = dict(
        code="042731",
        fingerprint="ab" * 32,
        device_id="ab" * 32,
        name="Studio Mac",
        port=48620,
        addresses=("192.168.1.20",),
    )
    values.update(overrides)
    return PairingPayload(**values)


def test_uri_roundtrip() -> None:
    original = payload()
    parsed = PairingPayload.parse(original.to_uri())
    assert parsed == original


def test_json_roundtrip() -> None:
    original = payload()
    parsed = PairingPayload.parse(original.to_json())
    assert parsed == original


@pytest.mark.parametrize(
    "value",
    [
        "",
        "torexlink://pair?d=%%%",
        "{broken",
        "https://example.com",
        "torexlink://pair",
    ],
)
def test_invalid_payloads(value: str) -> None:
    with pytest.raises(PairingPayloadError):
        PairingPayload.parse(value)


def test_rejects_bad_code_and_fingerprint() -> None:
    raw = {
        "v": 1,
        "code": "12345",
        "fp": "ab" * 32,
        "id": "ab" * 32,
        "name": "Mac",
        "port": 48620,
        "addrs": ["192.168.1.2"],
    }
    with pytest.raises(PairingPayloadError):
        PairingPayload.parse(json.dumps(raw))
    raw["code"] = "123456"
    raw["fp"] = "xyz"
    with pytest.raises(PairingPayloadError):
        PairingPayload.parse(json.dumps(raw))


def test_filters_unusable_addresses() -> None:
    raw = {
        "v": 1,
        "code": "123456",
        "fingerprint": "ab" * 32,
        "device_id": "ab" * 32,
        "name": "Mac",
        "port": 48620,
        "addresses": ["127.0.0.1", "224.0.0.1", "192.168.1.2", "192.168.1.2", "fe80::1%en0"],
    }
    parsed = PairingPayload.parse(json.dumps(raw))
    assert parsed.addresses == ("127.0.0.1", "192.168.1.2")
