"""mitmproxy CA helpers. Public cert only leaves this module; private key stays on disk."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from mitmproxy.certs import CertStore

CA_BASENAME = "mitmproxy"
PRIVATE_KEY_FILES = (f"{CA_BASENAME}-ca.pem", f"{CA_BASENAME}-ca.p12")
PUBLIC_CERT_PEM = f"{CA_BASENAME}-ca-cert.pem"
PRIVATE_MARKER = b"PRIVATE KEY"


@dataclass(slots=True, frozen=True)
class CaPaths:
    confdir: Path
    private_pem: Path
    public_pem: Path

    @classmethod
    def from_confdir(cls, confdir: Path) -> CaPaths:
        root = Path(confdir)
        return cls(
            confdir=root,
            private_pem=root / f"{CA_BASENAME}-ca.pem",
            public_pem=root / PUBLIC_CERT_PEM,
        )


def ensure_ca(confdir: Path, *, key_size: int = 2048) -> CaPaths:
    """Create mitmproxy CA in confdir if missing; lock private key files to mode 0600."""
    paths = CaPaths.from_confdir(confdir)
    paths.confdir.mkdir(parents=True, exist_ok=True)
    CertStore.from_store(paths.confdir, CA_BASENAME, key_size)
    for name in PRIVATE_KEY_FILES:
        path = paths.confdir / name
        if path.is_file():
            os.chmod(path, 0o600)
    if not paths.public_pem.is_file():
        msg = f"mitmproxy public CA missing at {paths.public_pem}"
        raise FileNotFoundError(msg)
    return paths


def public_ca_pem(confdir: Path) -> bytes:
    """Return PEM bytes for the public CA certificate only."""
    paths = ensure_ca(confdir)
    data = paths.public_pem.read_bytes()
    if PRIVATE_MARKER in data:
        msg = "refusing to serve material that contains a private key"
        raise RuntimeError(msg)
    if b"BEGIN CERTIFICATE" not in data:
        msg = "public CA PEM is not a certificate"
        raise RuntimeError(msg)
    return data


def public_ca_der(confdir: Path) -> bytes:
    """DER encoding of the public CA certificate (for Apple payloads)."""
    pem = public_ca_pem(confdir)
    cert = x509.load_pem_x509_certificate(pem)
    return cert.public_bytes(Encoding.DER)


def ca_ready(confdir: Path) -> bool:
    paths = CaPaths.from_confdir(confdir)
    return paths.public_pem.is_file() and paths.private_pem.is_file()


def private_key_mode(confdir: Path) -> int | None:
    paths = CaPaths.from_confdir(confdir)
    if not paths.private_pem.is_file():
        return None
    return paths.private_pem.stat().st_mode & 0o777
