"""Setup: CA public-only downloads, tokens, mobileconfig, bind auth, validation."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.proxy import ca as ca_mod
from viewlift_ai.services.setup import (
    SetupService,
    SetupValidationError,
    build_mobileconfig,
    mobileconfig_data_blobs,
    validate_ip,
    validate_port,
)


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"t" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
        proxy_host="127.0.0.1",
        proxy_port=8080,
    )


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(_config(tmp_path)))


def test_private_key_never_served(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        ensure = client.post("/api/setup/ca/ensure")
        assert ensure.status_code == 200
        confdir = tmp_path / "mitmproxy"
        private = (confdir / "mitmproxy-ca.pem").read_bytes()
        assert b"PRIVATE KEY" in private
        assert ca_mod.private_key_mode(confdir) == 0o600

        token_res = client.post("/api/setup/ca/token")
        assert token_res.status_code == 200
        token = token_res.json()["token"]
        cert = client.get(f"/api/setup/ca/cert?token={token}")
        assert cert.status_code == 200
        assert cert.headers.get("cache-control") == "no-store"
        assert b"PRIVATE KEY" not in cert.content
        assert b"BEGIN CERTIFICATE" in cert.content

        # Direct paths that must not expose the private key.
        for path in (
            "/api/setup/ca/cert",
            "/mitmproxy-ca.pem",
            "/static/mitmproxy-ca.pem",
        ):
            res = client.get(path)
            assert res.status_code in {404, 422}
            assert b"PRIVATE KEY" not in res.content


def test_token_expiry_and_replay(tmp_path: Path) -> None:
    clock = {"now": 1_000.0}

    def time_fn() -> float:
        return clock["now"]

    svc = SetupService(
        confdir=tmp_path / "mitmproxy",
        token_ttl_seconds=30.0,
        time_fn=time_fn,
    )
    svc.ensure_ca()
    minted = svc.mint_ca_download_token()
    token = minted["token"]
    first = svc.consume_download(token)
    assert b"BEGIN CERTIFICATE" in first.payload
    with pytest.raises(PermissionError):
        svc.consume_download(token)

    minted2 = svc.mint_ca_download_token()
    clock["now"] += 31.0
    with pytest.raises(TimeoutError):
        svc.consume_download(minted2["token"])

    with _client(tmp_path) as client:
        client.post("/api/setup/ca/ensure")
        tok = client.post("/api/setup/ca/token").json()["token"]
        ok = client.get(f"/api/setup/ca/cert?token={tok}")
        assert ok.status_code == 200
        replay = client.get(f"/api/setup/ca/cert?token={tok}")
        assert replay.status_code == 410


def test_mobileconfig_public_material_only(tmp_path: Path) -> None:
    confdir = tmp_path / "mitmproxy"
    ca_mod.ensure_ca(confdir)
    der = ca_mod.public_ca_der(confdir)
    payload = build_mobileconfig(confdir=confdir, proxy_host="192.168.1.10", proxy_port=8080)
    assert b"PRIVATE KEY" not in payload
    profile = plistlib.loads(payload)
    assert profile["PayloadType"] == "Configuration"
    types = {item["PayloadType"] for item in profile["PayloadContent"]}
    assert types == {"com.apple.security.root"}
    assert b"com.apple.proxy.http.global" not in payload
    assert b"192.168.1.10" in payload
    assert b"8080" in payload
    blobs = mobileconfig_data_blobs(payload)
    assert der in blobs
    for blob in blobs:
        assert b"PRIVATE KEY" not in blob


def test_ip_port_validation() -> None:
    assert validate_ip("127.0.0.1") == "127.0.0.1"
    assert validate_port(8080) == 8080
    with pytest.raises(SetupValidationError):
        validate_ip("not-an-ip")
    with pytest.raises(SetupValidationError):
        validate_ip("::1")
    with pytest.raises(SetupValidationError):
        validate_port(0)
    with pytest.raises(SetupValidationError):
        validate_port(70000)


def test_lan_bind_requires_explicit_enable_and_auth(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ifaces = [
        {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
        {"iface": "en0", "ip": "192.168.0.5", "scope": "lan"},
        {"iface": "en0", "ip": "10.0.0.2", "scope": "lan"},
    ]
    monkeypatch.setattr(
        "viewlift_ai.services.setup.list_ipv4_interfaces",
        lambda **_kwargs: ifaces,
    )
    svc = SetupService(
        confdir=tmp_path / "mitmproxy",
        interface_lister=lambda: ifaces,
    )
    with pytest.raises(SetupValidationError, match="enable_lan"):
        svc.configure_proxy_bind(host="192.168.0.5", port=8080, enable_lan=False)

    with pytest.raises(SetupValidationError, match="unspecified"):
        svc.configure_proxy_bind(host="0.0.0.0", port=8080, enable_lan=True)

    bind = svc.configure_proxy_bind(host="192.168.0.5", port=8080, enable_lan=True)
    assert bind.lan_enabled is True
    assert bind.auth is not None
    assert bind.auth.username
    assert bind.auth.password
    assert ":" not in bind.auth.password

    loop = svc.configure_proxy_bind(host="127.0.0.1", port=8080, enable_lan=False)
    assert loop.auth is None
    assert loop.lan_enabled is False

    with _client(tmp_path) as client:
        # Rebind setup service lister used by create_app.
        client.app.state.setup._interface_lister = lambda: ifaces  # noqa: SLF001
        denied = client.post(
            "/api/setup/proxy",
            json={"host": "10.0.0.2", "port": 8080, "enable_lan": False},
        )
        assert denied.status_code == 400

        allowed = client.post(
            "/api/setup/proxy",
            json={"host": "10.0.0.2", "port": 8080, "enable_lan": True},
        )
        assert allowed.status_code == 200
        body = allowed.json()
        assert body["auth_required"] is True
        assert body["auth"]["username"]
        assert body["auth"]["password"]


def test_setup_page_and_sdk_placeholder(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        page = client.get("/setup")
        assert page.status_code == 200
        assert b"Certificate Trust Settings" in page.content
        assert b"Apple Configurator" in page.content
        assert b"certificate pinning" in page.content.lower()
        assert b"sdk setup" in page.content.lower()

        status = client.get("/api/setup/status")
        assert status.status_code == 200
        body = status.json()
        assert body["sdk_receiver"]["enabled"] is False
        assert body["sdk_receiver"]["receiver_url"] is None
        note = body["sdk_receiver"]["note"].lower()
        assert "disabled" in note or "not enabled" in note or "off" in note


def test_mobileconfig_token_validation(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        bad = client.post(
            "/api/setup/mobileconfig/token",
            json={"host": "nope", "port": 8080},
        )
        assert bad.status_code == 400
        ok = client.post(
            "/api/setup/mobileconfig/token",
            json={"host": "127.0.0.1", "port": 8080},
        )
        assert ok.status_code == 200
        token = ok.json()["token"]
        dl = client.get(f"/api/setup/mobileconfig?token={token}")
        assert dl.status_code == 200
        assert dl.headers.get("cache-control") == "no-store"
        assert b"PRIVATE KEY" not in dl.content
        assert b"com.apple.security.root" in dl.content


def test_simulator_helpers_use_argv_and_actionable_failure(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        if argv[:4] == ["xcrun", "simctl", "list", "devices"]:
            return SimpleNamespace(returncode=0, stdout="== Devices ==\n", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="boom")

    svc = SetupService(confdir=tmp_path / "mitmproxy", runner=runner)
    svc.ensure_ca()
    result = svc.install_simulator_ca()
    assert result.ok is False
    assert "No booted simulator" in result.detail
    assert calls[0] == ["xcrun", "simctl", "list", "devices", "booted"]
    assert all(isinstance(c, list) for c in calls)

    def runner_booted(argv, **kwargs):
        calls.append(list(argv))
        if argv[:4] == ["xcrun", "simctl", "list", "devices"]:
            return SimpleNamespace(
                returncode=0,
                stdout="iPhone 16 (ABCDEF) (Booted)\n",
                stderr="",
            )
        return SimpleNamespace(returncode=1, stdout="", stderr="add-root-cert failed")

    calls.clear()
    svc2 = SetupService(confdir=tmp_path / "mitmproxy", runner=runner_booted)
    fail = svc2.install_simulator_ca()
    assert fail.ok is False
    assert "add-root-cert" in fail.detail
    assert fail.argv[:5] == ["xcrun", "simctl", "keychain", "booted", "add-root-cert"]
    # Ensure no shell string was used.
    assert not any(isinstance(arg, str) and " && " in arg for arg in fail.argv)


def test_test_request_status(tmp_path: Path) -> None:
    class FakeResp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def getcode(self):
            return 200

    svc = SetupService(confdir=tmp_path / "mitmproxy")
    result = svc.run_test_request(
        url="http://example.com/",
        opener_factory=lambda: SimpleNamespace(open=lambda *a, **k: FakeResp()),
    )
    assert result.ok is True
    assert result.status_code == 200

    with _client(tmp_path) as client:
        # Without a live proxy this may fail; endpoint should still return JSON status.
        res = client.post("/api/setup/test", json={"url": "http://127.0.0.1:9/"})
        assert res.status_code == 200
        body = res.json()
        assert "ok" in body
        assert "detail" in body
        status = client.get("/api/setup/test")
        assert status.status_code == 200
        assert "last_test" in status.json()


def test_subprocess_argv_not_shell() -> None:
    """Guard: SetupService runner is invoked with a list argv (no shell=True)."""
    seen: dict[str, object] = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell", False)
        return subprocess.CompletedProcess(argv, 0, stdout="(Booted)\n", stderr="")

    svc = SetupService(confdir=Path("/tmp/unused"), runner=runner)
    # Bypass CA by stubbing ensure path via list-only check.
    result = svc._require_booted_simulator()
    assert result.ok is True
    assert isinstance(seen["argv"], list)
    assert seen["shell"] is False
