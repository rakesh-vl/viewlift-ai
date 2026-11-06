"""App presence (online, background, offline), VPN alternates, and proxy-only detection."""

from __future__ import annotations

from pathlib import Path

import pytest
from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.debug_advertise import DebugAdvertiser
from viewlift_ai.services.debug_session import (
    BACKGROUND_GRACE_SECONDS,
    CLIENT_TTL_SECONDS,
    DebugSessionService,
)
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus, FlowEventBus, LogEventBus
from viewlift_ai.services.ingest import IngestService
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.proxy_clients import (
    ProxyClientRegistry,
    normalize_peer,
    parse_user_agent,
    unmatched_proxy_rows,
)

KEY = b"k" * SecretBox.KEY_SIZE


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Advertiser:
    def __init__(self) -> None:
        self.alternates: list[tuple[str, ...]] = []

    def publish(
        self, host: str, port: int, *, fingerprint: str, debug_key: str, alt: tuple[str, ...] = ()
    ) -> None:
        self.alternates.append(tuple(alt))

    def stop(self) -> None:
        return None


def _store(tmp_path: Path) -> EncryptedSqliteStore:
    return EncryptedSqliteStore(
        tmp_path / "presence.db", SecretBoxCipher(KEY), retention=RetentionPolicy.SESSION
    )


def _sessions(tmp_path: Path, clock: _Clock) -> DebugSessionService:
    return DebugSessionService(store=_store(tmp_path), events=EventBus(), time_fn=clock)


def _register(sessions: DebugSessionService, client_id: str = "app-a") -> None:
    sessions.register(
        {
            "client_id": client_id,
            "app_name": "Spinco",
            "bundle_id": "com.viewlift.spinco",
            "platform": "iOS",
            "device_key": "vendor:1",
        }
    )


def _state(sessions: DebugSessionService, client_id: str = "app-a") -> str:
    return str(sessions.get_client(client_id)["state"])


def test_silent_app_goes_offline_after_ttl(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    assert _state(sessions) == "online"
    clock.now += CLIENT_TTL_SECONDS + 1
    assert _state(sessions) == "offline"
    assert sessions.get_client("app-a")["online"] is False


def test_background_report_keeps_app_listed_as_background(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    sessions.ingest_event(
        "app-a", {"module": "lifecycle", "kind": "background", "payload": {"reason": "resign"}}
    )
    assert _state(sessions) == "background"
    clock.now += CLIENT_TTL_SECONDS + 600
    assert _state(sessions) == "background"
    row = sessions.apps_tree()["devices"][0]["apps"][0]
    assert row["state"] == "background"
    assert row["online"] is False
    assert sessions.online_count() == 0
    clock.now += BACKGROUND_GRACE_SECONDS
    assert _state(sessions) == "offline"


def test_foreground_report_and_reregister_return_app_online(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    sessions.ingest_event("app-a", {"module": "lifecycle", "kind": "background", "payload": {}})
    clock.now += 120
    sessions.ingest_event("app-a", {"module": "lifecycle", "kind": "foreground", "payload": {}})
    assert _state(sessions) == "online"
    sessions.ingest_event("app-a", {"module": "lifecycle", "kind": "background", "payload": {}})
    clock.now += 120
    _register(sessions)
    assert _state(sessions) == "online"


def test_lifecycle_reports_are_not_stored_as_debug_events(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    sessions.ingest_event("app-a", {"module": "lifecycle", "kind": "network", "payload": {}})
    assert sessions.list_events(client_id="app-a", module="lifecycle") == []


def test_command_to_background_app_explains_why(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    sessions.ingest_event("app-a", {"module": "lifecycle", "kind": "background", "payload": {}})
    with pytest.raises(LookupError, match="background"):
        sessions.invoke(client_id="app-a", name="performance.snapshot")


def test_touch_revives_offline_app_and_ignores_unknown_ids(tmp_path: Path) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    _register(sessions)
    clock.now += CLIENT_TTL_SECONDS + 5
    sessions.touch("app-a")
    assert _state(sessions) == "online"
    sessions.touch("never-registered")
    sessions.touch(None)


def test_flow_upload_marks_app_alive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _Clock()
    sessions = _sessions(tmp_path, clock)
    store = _store(tmp_path)
    svc = IngestService(
        capture=CaptureService(store, FlowEventBus()),
        log_capture=LogCaptureService(store, LogEventBus()),
        cert_dir=tmp_path / "ingest",
        debug_sessions=sessions,
        advertiser=_Advertiser(),
    )
    _register(sessions)
    clock.now += CLIENT_TTL_SECONDS + 5
    svc.ingest_flow({"url": "https://api.example.com/x"}, client_id="app-a")
    assert _state(sessions) == "online"
    clock.now += CLIENT_TTL_SECONDS + 5
    svc.ingest_log({"message": "hello"}, client_id="app-a")
    assert _state(sessions) == "online"


def _ingest_with_interfaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> IngestService:
    store = _store(tmp_path)
    svc = IngestService(
        capture=CaptureService(store, FlowEventBus()),
        log_capture=LogCaptureService(store, LogEventBus()),
        cert_dir=tmp_path / "ingest",
        advertiser=_Advertiser(),
    )
    svc.interface_lister = lambda: [
        {"iface": "en0", "ip": "192.168.1.10", "scope": "lan"},
        {"iface": "utun4", "ip": "100.105.93.60", "scope": "vpn"},
    ]
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    return svc


def test_vpn_address_is_advertised_only_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _ingest_with_interfaces(tmp_path, monkeypatch)
    off = svc.enable(host="", port=8742, iface="en0")
    assert off["extra_hosts"] == []
    assert off["receiver_urls"] == ["https://192.168.1.10:8742"]
    on = svc.enable(host="", port=8742, iface="en0", include_vpn=True)
    assert on["extra_hosts"] == ["100.105.93.60"]
    assert on["receiver_urls"] == [
        "https://192.168.1.10:8742",
        "https://100.105.93.60:8742",
    ]
    advertiser = svc.advertiser
    assert isinstance(advertiser, _Advertiser)
    assert advertiser.alternates[-1] == ("100.105.93.60",)
    svc.disable()


def test_network_watch_picks_up_vpn_that_connects_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc = _ingest_with_interfaces(tmp_path, monkeypatch)
    rows = [{"iface": "en0", "ip": "192.168.1.10", "scope": "lan"}]
    svc.interface_lister = lambda: list(rows)
    svc.enable(host="", port=8742, iface="en0", include_vpn=True)
    assert svc.bind.extra_hosts == ()
    rows.append({"iface": "utun4", "ip": "100.105.93.60", "scope": "vpn"})
    svc.follow_network()
    assert svc.bind.extra_hosts == ("100.105.93.60",)
    rows.pop()
    svc.follow_network()
    assert svc.bind.extra_hosts == ()
    svc.disable()


def test_bonjour_txt_carries_alternates(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[list[str]] = []

    class _Process:
        pid = 1

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float) -> None:
            return None

    def fake_popen(argv: list[str], **_: object) -> _Process:
        captured.append(argv)
        return _Process()

    monkeypatch.setattr("viewlift_ai.services.debug_advertise.subprocess.Popen", fake_popen)
    advertiser = DebugAdvertiser()
    advertiser.publish("192.168.1.10", 8742, fingerprint="ab", debug_key="k", alt=("100.1.2.3",))
    assert "alt=100.1.2.3" in captured[-1]
    advertiser.publish("192.168.1.10", 8742, fingerprint="ab", debug_key="k")
    assert not any(item.startswith("alt=") for item in captured[-1])
    advertiser.stop()


def test_user_agent_parsing() -> None:
    cfnetwork = parse_user_agent("OneApp/3.2.1 CFNetwork/1568.200.51 Darwin/24.1.0")
    assert (cfnetwork.app_name, cfnetwork.app_version, cfnetwork.platform) == (
        "OneApp",
        "3.2.1",
        "Apple",
    )
    alamofire = parse_user_agent(
        "Spinco/5.0 (com.viewlift.spinco; build:42; iOS 18.1.0) Alamofire/5.9.0"
    )
    assert alamofire.bundle_id == "com.viewlift.spinco"
    assert alamofire.platform == "iOS"
    assert parse_user_agent("Mozilla/5.0 (Macintosh) Chrome/120 Safari/537").app_name == "Chrome"
    assert parse_user_agent("").app_name == "Unknown app"


def test_peer_normalization() -> None:
    assert normalize_peer("::ffff:192.168.1.5") == "192.168.1.5"
    assert normalize_peer("100.105.93.60") == "100.105.93.60"
    assert normalize_peer("not-an-ip") == ""


def test_proxy_registry_groups_by_device_and_app() -> None:
    clock = _Clock()
    registry = ProxyClientRegistry(time_fn=clock)
    ua = "OneApp/3.2.1 CFNetwork/1568 Darwin/24.1.0"
    registry.observe(peer="192.168.1.59", user_agent=ua, host="api.example.com")
    registry.observe(peer="192.168.1.59", user_agent=ua, host="cdn.example.com")
    registry.observe(peer="100.105.93.61", user_agent=ua, host="api.example.com")
    registry.observe(peer="127.0.0.1", user_agent="curl/8", host="localhost")
    rows = registry.snapshot()
    assert len(rows) == 2
    wifi = next(row for row in rows if row["peer"] == "192.168.1.59")
    assert wifi["requests"] == 2
    assert wifi["last_host"] == "cdn.example.com"
    assert wifi["online"] is True
    clock.now += 600
    assert all(row["online"] is False for row in registry.snapshot())


def test_proxy_addon_records_peer_and_app_from_connect_and_request() -> None:
    from mitmproxy.test import tflow

    from viewlift_ai.proxy.addon import ViewliftCaptureAddon
    from viewlift_ai.proxy.allowlist import HostAllowlist

    registry = ProxyClientRegistry(time_fn=_Clock())
    addon = ViewliftCaptureAddon(
        HostAllowlist(["*"]), None, proxy_clients=registry  # type: ignore[arg-type]
    )
    flow = tflow.tflow()
    flow.client_conn.peername = ("::ffff:192.168.1.59", 50000)
    flow.request.headers["User-Agent"] = "Spinco/5.0 CFNetwork/1568 Darwin/24.1.0"
    addon.http_connect(flow)
    rows = registry.snapshot()
    assert [(row["peer"], row["app_name"]) for row in rows] == [("192.168.1.59", "Spinco")]
    flow.client_conn.peername = ("127.0.0.1", 50001)
    addon.http_connect(flow)
    assert len(registry.snapshot()) == 1


def test_proxy_rows_hide_apps_already_registered_through_sdk() -> None:
    registry = ProxyClientRegistry(time_fn=_Clock())
    ua = "Spinco/5.0 CFNetwork/1568 Darwin/24.1.0"
    registry.observe(peer="192.168.1.59", user_agent=ua)
    registry.observe(peer="192.168.1.77", user_agent=ua)
    sdk_apps = [{"id": "app-a", "app_name": "Spinco", "bundle_id": "x", "executable": "Spinco"}]
    activity = {"app-a": {"peer": "192.168.1.59"}}
    left = unmatched_proxy_rows(registry.snapshot(), sdk_apps, activity)
    assert [row["peer"] for row in left] == ["192.168.1.77"]
    assert sdk_apps[0]["via_proxy"] is True
