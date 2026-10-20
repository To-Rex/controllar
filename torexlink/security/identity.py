"""Persistent device identity.

The TLS certificate *is* the device identity. It is self-signed, generated
locally, private to the OS account, and pinned by peers after pairing. No
public CA is involved and no online trust lookup can silently replace it.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from ..constants import CERT_VALIDITY_DAYS, MAX_CERT_PEM_BYTES
from ..util import atomic_write_bytes

_KEY_SIZE = 3072


def fingerprint_from_der(der: bytes) -> str:
    """Colon-separated SHA-256 certificate fingerprint (display form)."""
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def normalize_fingerprint(value: str) -> str:
    """Canonical comparison form: 64 lowercase hex characters."""
    cleaned = "".join(char for char in value.lower() if char not in ": -")
    if len(cleaned) != 64 or any(char not in "0123456789abcdef" for char in cleaned):
        raise ValueError("invalid SHA-256 certificate fingerprint")
    return cleaned


def short_fingerprint(value: str, groups: int = 4) -> str:
    try:
        compact = normalize_fingerprint(value).upper()
    except ValueError:
        return value[:15] + "…" if len(value) > 16 else value
    return " ".join(compact[i : i + 4] for i in range(0, groups * 4, 4)) + "…"


def certificate_to_pem(certificate: x509.Certificate) -> str:
    return certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")


def load_certificate(pem: str | bytes) -> x509.Certificate:
    raw = pem.encode("ascii") if isinstance(pem, str) else pem
    if len(raw) > MAX_CERT_PEM_BYTES:
        raise ValueError("certificate is too large")
    cert = x509.load_pem_x509_certificate(raw)
    now = datetime.now(UTC)
    not_before, not_after = certificate_validity(cert)
    if now < not_before - timedelta(minutes=5):
        raise ValueError("certificate is not valid yet")
    if now > not_after:
        raise ValueError("certificate has expired")
    return cert


def certificate_validity(certificate: x509.Certificate) -> tuple[datetime, datetime]:
    """Return timezone-aware validity bounds across cryptography versions."""
    if hasattr(certificate, "not_valid_before_utc"):
        return certificate.not_valid_before_utc, certificate.not_valid_after_utc
    return (
        certificate.not_valid_before.replace(tzinfo=UTC),
        certificate.not_valid_after.replace(tzinfo=UTC),
    )


@dataclass(frozen=True, slots=True)
class PeerIdentity:
    """A verified peer certificate."""

    cert_pem: str
    fingerprint: str
    device_id: str

    @classmethod
    def from_pem(cls, pem: str) -> PeerIdentity:
        if len(pem.encode("utf-8")) > MAX_CERT_PEM_BYTES:
            raise ValueError("peer certificate is too large")
        cert = load_certificate(pem)
        der = cert.public_bytes(serialization.Encoding.DER)
        compact = hashlib.sha256(der).hexdigest()
        return cls(cert_pem=pem, fingerprint=fingerprint_from_der(der), device_id=compact)

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        cert = load_certificate(self.cert_pem)
        key = cert.public_key()
        if not isinstance(key, rsa.RSAPublicKey):
            raise ValueError("unsupported peer public key type")
        return key

    def verify(self, data: bytes, signature: bytes) -> bool:
        try:
            self.public_key.verify(
                signature,
                data,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError):
            return False
        return True


class Identity:
    """Long-lived device certificate and private key."""

    def __init__(
        self,
        cert_path: Path,
        key_path: Path,
        *,
        certificate: x509.Certificate,
        private_key: rsa.RSAPrivateKey,
    ) -> None:
        self.cert_path = cert_path
        self.key_path = key_path
        self.certificate = certificate
        self.private_key = private_key
        self.cert_der = certificate.public_bytes(serialization.Encoding.DER)
        self.cert_pem = certificate_to_pem(certificate)
        self.fingerprint = fingerprint_from_der(self.cert_der)
        self.device_id = hashlib.sha256(self.cert_der).hexdigest()

    @property
    def display_fingerprint(self) -> str:
        return short_fingerprint(self.fingerprint, groups=6)

    @property
    def not_after(self) -> datetime:
        return certificate_validity(self.certificate)[1]

    def sign(self, data: bytes) -> bytes:
        return self.private_key.sign(
            data,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
            hashes.SHA256(),
        )

    def verify_own_key(self) -> bool:
        sample = secrets.token_bytes(32)
        return PeerIdentity.from_pem(self.cert_pem).verify(sample, self.sign(sample))

    @classmethod
    def load_or_create(cls, cert_path: Path, key_path: Path) -> Identity:
        cert_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if cert_path.exists() and key_path.exists():
            try:
                return cls._load(cert_path, key_path)
            except (ValueError, OSError, TypeError) as exc:
                # Never silently replace a possibly compromised identity.
                # Preserve it for inspection and create a new one only after
                # moving the broken pair aside.
                stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
                for path in (cert_path, key_path):
                    if path.exists():
                        with contextlib.suppress(OSError):
                            path.replace(path.with_suffix(path.suffix + f".broken-{stamp}"))
                import logging

                logging.getLogger(__name__).warning("Identity rotated after load failure: %s", exc)
        elif cert_path.exists() or key_path.exists():
            # An orphaned certificate or key must never be overwritten without
            # a forensic copy; it may be the only recoverable half of a pair.
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            for path in (cert_path, key_path):
                if path.exists():
                    with contextlib.suppress(OSError):
                        path.replace(path.with_suffix(path.suffix + f".orphan-{stamp}"))
        return cls._generate(cert_path, key_path)

    @classmethod
    def _load(cls, cert_path: Path, key_path: Path) -> Identity:
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        cert = load_certificate(cert_path.read_bytes())
        if not isinstance(cert.public_key(), rsa.RSAPublicKey):
            raise ValueError("identity key is not RSA")
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey):
            raise ValueError("unsupported private key")
        public_der = key.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        cert_public = cert.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if public_der != cert_public:
            raise ValueError("certificate/private key mismatch")
        now = datetime.now(UTC)
        if certificate_validity(cert)[1] <= now:
            raise ValueError("identity certificate expired")
        return cls(cert_path, key_path, certificate=cert, private_key=key)

    @classmethod
    def _generate(cls, cert_path: Path, key_path: Path) -> Identity:
        key = rsa.generate_private_key(public_exponent=65537, key_size=_KEY_SIZE)
        now = datetime.now(UTC)
        suffix = secrets.token_hex(4)
        subject = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, f"ToRexLink Device {suffix}"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "ToRexLink"),
            ]
        )
        san = x509.SubjectAlternativeName(
            [
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                x509.IPAddress(ipaddress.ip_address("::1")),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=CERT_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(san, critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=True,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage(
                    [
                        ExtendedKeyUsageOID.SERVER_AUTH,
                        ExtendedKeyUsageOID.CLIENT_AUTH,
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        atomic_write_bytes(key_path, key_pem, private=True)
        atomic_write_bytes(cert_path, cert_pem, private=True)
        return cls(cert_path, key_path, certificate=cert, private_key=key)


__all__ = [
    "Identity",
    "PeerIdentity",
    "certificate_to_pem",
    "certificate_validity",
    "fingerprint_from_der",
    "load_certificate",
    "normalize_fingerprint",
    "short_fingerprint",
]
