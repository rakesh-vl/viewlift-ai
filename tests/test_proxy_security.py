"""Proxy allowlist tunnel + LAN proxyauth wiring."""

from __future__ import annotations

import socket
from pathlib import Path
from types import SimpleNamespace

from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.proxy.addon import ViewliftCaptureAddon
from viewlift_ai.proxy.allowlist import HostAllowlist
from viewlift_ai.proxy.runner import ProxyRunner
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import FlowEventBus


def _capture(tmp_path: Path) -> CaptureService:
    cipher = SecretBoxCipher(b"k" * SecretBox.KEY_SIZE)
    store = EncryptedSqliteStore(
        tmp_path / "p.db",
        cipher,
        retention=RetentionPolicy.SESSION,
    )
    return CaptureService(store, FlowEventBus())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_deny_host_sets_ignore_connection(tmp_path: Path) -> None:
    addon = ViewliftCaptureAddon(HostAllowlist(["api.allowed.test"]), _capture(tmp_path))
    data = SimpleNamespace(
        ignore_connection=False,
        client_hello=SimpleNamespace(sni="evil.example"),
        context=SimpleNamespace(server=SimpleNamespace(address=("1.2.3.4", 443))),
    )
    addon.tls_clienthello(data)
    assert data.ignore_connection is True

    allowed = SimpleNamespace(
        ignore_connection=False,
        client_hello=SimpleNamespace(sni="api.allowed.test"),
        context=SimpleNamespace(server=SimpleNamespace(address=("1.2.3.4", 443))),
    )
    addon.tls_clienthello(allowed)
    assert allowed.ignore_connection is False


def test_proxy_runner_applies_lan_proxyauth(tmp_path: Path) -> None:
    capture = _capture(tmp_path)
    port = _free_port()
    runner = ProxyRunner(
        listen_host="127.0.0.1",
        listen_port=port,
        confdir=tmp_path / "mitm",
        allowlist=HostAllowlist([]),
        capture=capture,
        proxy_auth=("vluser", "secret-pass"),
    )
    try:
        runner.start(timeout=20.0)
        master = runner._master  # noqa: SLF001
        assert master is not None
        assert master.options.proxyauth == "vluser:secret-pass"
    finally:
        runner.stop()
