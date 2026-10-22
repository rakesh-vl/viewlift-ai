"""SDK ingest: pairing, session, redaction, body caps, rate limit."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import FlowSource, RetentionPolicy
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import FlowEventBus, LogEventBus
from viewlift_ai.services.ingest import IngestService, SessionToken
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.setup import SetupValidationError


class _Advertiser:
    def __init__(self) -> None:
        self.published: list[tuple[str, int, str, str]] = []
        self.stopped = 0

    def publish(self, host: str, port: int, *, fingerprint: str, debug_key: str) -> None:
        self.published.append((host, port, fingerprint, debug_key))

    def stop(self) -> None:
        self.stopped += 1


def _svc(tmp_path: Path) -> IngestService:
    cipher = SecretBoxCipher(b"k" * SecretBox.KEY_SIZE)
    store = EncryptedSqliteStore(
        tmp_path / "t.db",
        cipher,
        retention=RetentionPolicy.SESSION,
    )
    capture = CaptureService(store, FlowEventBus())
    log_capture = LogCaptureService(store, LogEventBus())
    return IngestService(
        capture=capture,
        log_capture=log_capture,
        cert_dir=tmp_path / "ingest",
        pairing_ttl=60.0,
        session_ttl=120.0,
        rate_limit_per_minute=5,
        max_body_bytes=32,
        advertiser=_Advertiser(),
    )


def test_disabled_by_default_and_accepts_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path)
    assert svc.enabled is False
    assert svc.status()["receiver_url"] is None
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    status = svc.enable(host="", port=8742)
    assert status["enabled"] is True
    assert status["host"] == "127.0.0.1"
    assert status["receiver_url"] == "https://127.0.0.1:8742"
    with pytest.raises(SetupValidationError, match="0.0.0.0"):
        svc.enable(host="0.0.0.0", port=8742)
    svc.disable()


def test_enable_requires_listed_private_iface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path)
    svc.interface_lister = lambda: [
        {"iface": "en0", "ip": "192.168.1.10", "scope": "lan"},
    ]
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    status = svc.enable(host="192.168.1.10", port=8742)
    assert status["enabled"] is True
    assert status["host"] == "192.168.1.10"
    with pytest.raises(SetupValidationError, match="not on an active"):
        svc.enable(host="192.168.1.99", port=8742)
    svc.disable()


def test_enable_rebinds_running_server_when_interface_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _svc(tmp_path)
    svc.interface_lister = lambda: [
        {"iface": "en0", "ip": "192.168.0.39", "scope": "lan"},
        {"iface": "en1", "ip": "192.168.1.68", "scope": "lan"},
    ]
    actions: list[str] = []

    def start(service: IngestService) -> None:
        actions.append(f"start:{service._bind.host}")  # noqa: SLF001
        service._server = object()  # type: ignore[assignment]  # noqa: SLF001

    def stop(service: IngestService) -> None:
        actions.append("stop")
        service._server = None  # noqa: SLF001

    monkeypatch.setattr(IngestService, "start_https_server", start)
    monkeypatch.setattr(IngestService, "stop_https_server", stop)

    svc.enable(host="192.168.1.68", port=8742)
    svc.enable(host="192.168.0.39", port=8742)

    assert actions == ["start:192.168.1.68", "stop", "start:192.168.0.39"]


def test_follow_network_rebinds_when_wifi_address_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    advertiser = _Advertiser()
    rows = [
        {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
        {"iface": "en0", "ip": "192.168.0.39", "scope": "lan"},
    ]
    svc = _svc(tmp_path)
    svc.advertiser = advertiser
    svc.interface_lister = lambda: rows
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    monkeypatch.setattr(IngestService, "stop_https_server", lambda self: None)
    svc.enable(host="192.168.0.39", port=8742)
    rows[1] = {"iface": "en0", "ip": "10.117.22.7", "scope": "lan"}

    assert svc.follow_network() == "10.117.22.7"
    host, port, fingerprint, debug_key = advertiser.published[-1]
    assert (host, port) == ("10.117.22.7", 8742)
    assert fingerprint
    assert debug_key

    svc.disable()
    assert svc.follow_network() == "127.0.0.1"
    assert advertiser.stopped >= 1


def test_follow_network_keeps_selected_vpn_interface(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
        {"iface": "en0", "ip": "10.117.22.7", "scope": "lan"},
        {"iface": "utun4", "ip": "100.100.20.5", "scope": "vpn"},
    ]
    svc = _svc(tmp_path)
    svc.interface_lister = lambda: rows
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    monkeypatch.setattr(IngestService, "stop_https_server", lambda self: None)

    status = svc.enable(host="", port=9000, iface="utun4")

    assert status["host"] == "100.100.20.5"
    assert status["port"] == 9000
    assert status["iface"] == "utun4"
    rows[1] = {"iface": "en0", "ip": "192.168.1.20", "scope": "lan"}
    assert svc.follow_network() == "100.100.20.5"


def test_debug_key_mints_one_use_pairing_token(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc._bind.enabled = True  # noqa: SLF001
    key = svc.debug_key()
    assert (tmp_path / "ingest" / "debug.key").stat().st_mode & 0o777 == 0o600
    minted = svc.mint_pairing_for_debug_key(key)
    token = minted["pairing_token"]
    assert svc.exchange_pairing(token)["session_token"]
    with pytest.raises(PermissionError):
        svc.exchange_pairing(token)
    with pytest.raises(PermissionError, match="invalid debug key"):
        svc.mint_pairing_for_debug_key("wrong")


def test_pairing_one_use_and_session_ingest(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc._bind.enabled = True  # noqa: SLF001
    svc._bind.host = "192.168.1.10"  # noqa: SLF001
    svc._ensure_tls_material()  # noqa: SLF001
    minted = svc.mint_pairing_token()
    token = minted["pairing_token"]
    session = svc.exchange_pairing(token)
    assert "session_token" in session
    with pytest.raises(PermissionError):
        svc.exchange_pairing(token)

    auth = f"Bearer {session['session_token']}"
    svc.require_session(auth)
    body = base64.b64encode(b'{"token":"secret","x":1}').decode()
    result = svc.ingest_flow(
        {
            "url": "https://api.example.com/v1",
            "method": "POST",
            "request_headers": {"Authorization": "Bearer abc"},
            "request_body": body,
            "status": 200,
            "request_id": "req-1",
        }
    )
    assert result["source"] == FlowSource.SDK.value
    flow = svc.capture._store.get_flow(result["id"])  # noqa: SLF001
    assert flow is not None
    assert flow.source == FlowSource.SDK
    assert flow.request_headers["Authorization"] == "Bearer abc"
    assert b"secret" in flow.request_body

    log_result = svc.ingest_log(
        {
            "level": "info",
            "subsystem": "app",
            "message": "linked requestId=req-1",
            "request_id": "req-1",
        }
    )
    assert log_result["ok"] is True


def test_body_cap(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc._bind.enabled = True  # noqa: SLF001
    svc._sessions["s"] = SessionToken(token="s", expires_at=9_999_999)  # noqa: SLF001
    huge = base64.b64encode(b"a" * 200).decode()
    result = svc.ingest_flow(
        {
            "url": "https://h/p",
            "method": "GET",
            "response_body": huge,
            "status": 200,
        }
    )
    flow = svc.capture._store.get_flow(result["id"])  # noqa: SLF001
    assert flow is not None
    assert len(flow.response_body) <= 32


def test_fingerprint_stable(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc._ensure_tls_material()  # noqa: SLF001
    fp1 = svc.status()["pinned_fingerprint"]
    svc2 = _svc(tmp_path)
    svc2._ensure_tls_material()  # noqa: SLF001
    fp2 = svc2.status()["pinned_fingerprint"]
    assert fp1 == fp2
    assert isinstance(fp1, str) and len(fp1) == 64


def test_activity_counts_requests_and_rejections(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    svc.record_request("192.168.0.224", "/v1/pair")
    svc.record_request("192.168.0.224", "/v1/flows")
    svc.record_request("192.168.0.224", "/v1/flows", error="401 invalid or expired session token")
    act = svc.status()["activity"]
    assert act["last_client"] == "192.168.0.224"
    assert act["pairings"] == 1
    assert act["flows"] == 1
    assert act["rejected"] == 1
    assert "401" in str(act["last_error"])
