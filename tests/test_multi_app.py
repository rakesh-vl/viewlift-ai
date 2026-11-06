"""Several capture-enabled apps on one simulator or device stay isolated end to end."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import FlowRecord, FlowSource, RetentionPolicy
from viewlift_ai.models.log import LogLevel, LogRecord
from viewlift_ai.services.capture import CaptureService
from viewlift_ai.services.debug_session import CLIENT_TTL_SECONDS, DebugSessionService
from viewlift_ai.services.device_logs import LogSelector
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import EventBus, FlowEventBus, LogEventBus
from viewlift_ai.services.ingest import ClientMismatchError, IngestService
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.log_tail import AppAttribution, LogTailService

SIM = "SIM-UDID-1"
KEY = b"k" * SecretBox.KEY_SIZE


class _Advertiser:
    def publish(
        self, host: str, port: int, *, fingerprint: str, debug_key: str, alt: tuple[str, ...] = ()
    ) -> None:
        return None

    def stop(self) -> None:
        return None


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _store(tmp_path: Path, name: str = "multi.db") -> EncryptedSqliteStore:
    return EncryptedSqliteStore(
        tmp_path / name, SecretBoxCipher(KEY), retention=RetentionPolicy.SESSION
    )


def _registration(client_id: str, bundle_id: str, *, device_key: str = SIM) -> dict[str, object]:
    return {
        "client_id": client_id,
        "app_name": bundle_id.rsplit(".", 1)[-1].title(),
        "bundle_id": bundle_id,
        "platform": "iOS",
        "os_version": "18.0",
        "executable": bundle_id.rsplit(".", 1)[-1].title(),
        "device_key": device_key,
        "device_name": "iPhone 16",
        "device_model": "iPhone17,3",
        "is_simulator": True,
        "sdk_version": "1.1.0",
    }


def _flow(client_id: str | None, *, request_id: str | None = None) -> FlowRecord:
    return FlowRecord(
        id=str(uuid4()),
        method="GET",
        host="api.example.com",
        path="/x",
        status=200,
        duration_ms=1.0,
        size_bytes=0,
        protocol="HTTPS",
        source=FlowSource.SDK if client_id else FlowSource.PROXY,
        created_at=datetime.now(UTC),
        request_id=request_id,
        client_id=client_id,
    )


def _log(client_id: str | None, *, request_id: str | None = None) -> LogRecord:
    return LogRecord(
        id=str(uuid4()),
        level=LogLevel.INFO,
        subsystem="app",
        message="hello",
        created_at=datetime.now(UTC),
        request_id=request_id,
        source="sdk",
        client_id=client_id,
    )


def _ingest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[IngestService, EncryptedSqliteStore]:
    store = _store(tmp_path)
    svc = IngestService(
        capture=CaptureService(store, FlowEventBus()),
        log_capture=LogCaptureService(store, LogEventBus()),
        cert_dir=tmp_path / "ingest",
        advertiser=_Advertiser(),
    )
    monkeypatch.setattr(IngestService, "start_https_server", lambda self: None)
    svc.enable(host="", port=8742)
    return svc, store


def _session(svc: IngestService, client_id: str | None) -> str:
    pairing = str(svc.mint_pairing_token()["pairing_token"])
    return str(svc.exchange_pairing(pairing, client_id=client_id)["session_token"])


def test_session_is_bound_to_one_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    svc, _ = _ingest(tmp_path, monkeypatch)
    token = _session(svc, "app-a")
    assert svc.bind_client(token, None) == "app-a"
    assert svc.bind_client(token, "app-a") == "app-a"
    with pytest.raises(ClientMismatchError):
        svc.bind_client(token, "app-b")

    legacy = _session(svc, None)
    assert svc.bind_client(legacy, None) is None
    assert svc.bind_client(legacy, "app-c") == "app-c"
    with pytest.raises(ClientMismatchError):
        svc.bind_client(legacy, "app-d")
    svc.disable()


def test_flows_and_logs_are_tagged_and_counted_per_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    svc, store = _ingest(tmp_path, monkeypatch)
    svc.ingest_flow({"url": "https://a.example.com/x"}, client_id="app-a")
    svc.ingest_flow({"url": "https://b.example.com/y"}, client_id="app-b")
    svc.ingest_log({"message": "from a"}, client_id="app-a")
    for client_id, path in (("app-a", "/v1/flows"), ("app-b", "/v1/flows"), ("app-a", "/v1/logs")):
        svc.record_request("127.0.0.1", path, client_id=client_id)

    assert [f.host for f in store.list_flows(client_ids=["app-a"])] == ["a.example.com"]
    assert [f.host for f in store.list_flows(client_ids=["app-b"])] == ["b.example.com"]
    assert len(store.list_flows()) == 2
    assert [log.message for log in store.list_logs(client_ids=["app-a"])] == ["from a"]
    assert store.list_logs(client_ids=["app-b"]) == []

    clients = svc.activity()["clients"]
    assert clients["app-a"]["flows"] == 1
    assert clients["app-a"]["logs"] == 1
    assert clients["app-b"]["flows"] == 1
    svc.disable()


def test_registry_groups_apps_by_device_and_never_flips(tmp_path: Path) -> None:
    clock = _Clock()
    service = DebugSessionService(store=_store(tmp_path), events=EventBus(), time_fn=clock)
    service.register(_registration("app-a", "com.example.alpha"))
    service.register(_registration("app-b", "com.example.beta"))
    service.register(_registration("tv-1", "com.example.tv", device_key="SIM-TV"))

    tree = service.apps_tree()
    devices = {row["device_key"]: row for row in tree["devices"]}
    assert {app["id"] for app in devices[SIM]["apps"]} == {"app-a", "app-b"}
    assert [app["id"] for app in devices["SIM-TV"]["apps"]] == ["tv-1"]
    assert sorted(service.client_ids_for_device(SIM)) == ["app-a", "app-b"]

    assert service.resolve_client_id(None) is None
    assert service.resolve_client_id("app-b") == "app-b"

    clock.now += CLIENT_TTL_SECONDS + 1
    service.heartbeat("app-a")
    assert service.resolve_client_id(None) == "app-a"


def test_reinstall_supersedes_old_instance_and_registry_survives_restart(tmp_path: Path) -> None:
    clock = _Clock()
    store = _store(tmp_path)
    service = DebugSessionService(store=store, events=EventBus(), time_fn=clock)
    service.register(_registration("old-install", "com.example.alpha"))
    clock.now += 5
    service.register(_registration("new-install", "com.example.alpha"))

    assert service.get_client("old-install")["superseded_by"] == "new-install"
    assert service.get_client("new-install")["superseded_by"] is None

    reloaded = DebugSessionService(store=store, events=EventBus(), time_fn=clock)
    assert reloaded.get_client("new-install")["executable"] == "Alpha"
    assert reloaded.get_client("old-install")["superseded_by"] == "new-install"
    assert reloaded.find_by_udid(SIM)[0]["id"] == "new-install"

    clock.now += CLIENT_TTL_SECONDS + 1
    reloaded.reset_session()
    with pytest.raises(LookupError):
        reloaded.get_client("old-install")
    assert store.list_clients() == []


def test_store_migrates_old_database_and_isolates_request_links(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE flows (
            id TEXT PRIMARY KEY, method TEXT NOT NULL, host TEXT NOT NULL, path TEXT NOT NULL,
            status INTEGER, duration_ms REAL, size_bytes INTEGER, protocol TEXT NOT NULL,
            source TEXT NOT NULL, created_at TEXT NOT NULL, request_headers BLOB NOT NULL,
            request_body BLOB NOT NULL, response_headers BLOB NOT NULL,
            response_body BLOB NOT NULL, error TEXT, request_id TEXT,
            tls_failed INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE logs (
            id TEXT PRIMARY KEY, level TEXT NOT NULL, subsystem TEXT NOT NULL,
            message BLOB NOT NULL, created_at TEXT NOT NULL, request_id TEXT,
            source TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()

    store = EncryptedSqliteStore(path, SecretBoxCipher(KEY), retention=RetentionPolicy.SESSION)
    flow_cols = {row[1] for row in store._conn.execute("PRAGMA table_info(flows)")}  # noqa: SLF001
    log_cols = {row[1] for row in store._conn.execute("PRAGMA table_info(logs)")}  # noqa: SLF001
    assert "client_id" in flow_cols
    assert {"client_id", "udid"} <= log_cols

    flow_a = _flow("app-a", request_id="same-rid")
    flow_b = _flow("app-b", request_id="same-rid")
    store.save_flow(flow_a)
    store.save_flow(flow_b)
    store.save_flow(_flow(None))
    store.save_log(_log("app-a", request_id="same-rid"))
    store.save_log(_log("app-b", request_id="same-rid"))

    assert store.flow_id_for_request("same-rid", client_id="app-a") == flow_a.id
    assert store.flow_id_for_request("same-rid", client_id="app-b") == flow_b.id
    assert len(store.logs_for_request("same-rid", client_id="app-a")) == 1
    assert store.list_flows(client_ids=[]) == []
    assert len(store.list_flows()) == 3
    assert store.client_counts() == {
        "app-a": {"flows": 1, "logs": 1},
        "app-b": {"flows": 1, "logs": 1},
    }

    assert store.delete_client_data("app-a") == 2
    assert {f.client_id for f in store.list_flows()} == {"app-b", None}
    assert {log.client_id for log in store.list_logs()} == {"app-b"}


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
    )


def test_scoped_dashboard_endpoints(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        sessions = client.app.state.debug_sessions
        store = client.app.state.store
        sessions.register(_registration("app-a", "com.example.alpha"))
        sessions.register(_registration("app-b", "com.example.beta"))
        store.save_flow(_flow("app-a"))
        store.save_flow(_flow("app-b"))
        store.save_flow(_flow(None))
        store.save_log(_log("app-a"))

        tree = client.get("/api/apps").json()
        assert tree["online_apps"] == 2
        (device,) = tree["devices"]
        counts = {app["id"]: app["counts"] for app in device["apps"]}
        assert counts["app-a"] == {"flows": 1, "logs": 1}

        assert len(client.get("/api/flows").json()["flows"]) == 3
        only_a = client.get("/api/flows?client_id=app-a").json()["flows"]
        assert [row["client_id"] for row in only_a] == ["app-a"]
        both = client.get(f"/api/flows?device_key={SIM}").json()["flows"]
        assert {row["client_id"] for row in both} == {"app-a", "app-b"}
        assert client.get("/api/flows?device_key=unknown").json()["flows"] == []
        assert len(client.get("/api/logs?client_id=app-b").json()["logs"]) == 0
        har = client.get("/api/export/har?client_id=app-b")
        assert har.status_code == 200

        assert client.get("/api/debug/app").status_code == 409
        picked = client.get("/api/debug/app?client_id=app-b").json()
        assert picked["client"]["bundle_id"] == "com.example.beta"

        cleared = client.delete("/api/apps/app-a/data").json()
        assert cleared["deleted"] == 2
        assert client.get("/api/flows?client_id=app-a").json()["flows"] == []
        assert len(client.get("/api/flows").json()["flows"]) == 2

        assert client.delete("/api/apps/data").json()["ok"] is True
        assert client.get("/api/flows").json()["flows"] == []


class _FakeSim:
    def __init__(self) -> None:
        self.udid: str | None = None
        self.on_line = None
        self.starts: list[str] = []

    def list_booted(self) -> list[dict[str, str]]:
        return [{"udid": SIM}, {"udid": "SIM-2"}]

    def start(self, udid: str, *, on_line, process: str | None = None) -> dict[str, object]:
        self.udid = udid
        self.on_line = on_line
        self.starts.append(udid)
        return {"ok": True, "detail": f"streaming {udid}"}

    def stop(self) -> dict[str, object]:
        self.udid = None
        return {"ok": True, "detail": "stopped"}

    @property
    def running(self) -> bool:
        return self.udid is not None

    @property
    def active_udid(self) -> str | None:
        return self.udid


class _FakeDevice:
    def __init__(self) -> None:
        self.selector = LogSelector()
        self.running = False
        self.active_udid = None

    def list_devices(self) -> list[dict[str, str]]:
        return []

    def stop(self) -> dict[str, object]:
        return {"ok": True}


def test_log_tail_restarts_for_other_simulator_and_attributes_app_lines(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sim = _FakeSim()
    tail = LogTailService(
        capture=LogCaptureService(store, LogEventBus()),
        simulator=sim,
        device=_FakeDevice(),
    )
    attribution = AppAttribution(client_id="app-a", executable="Alpha")
    assert tail.start("simulator", udid=SIM, process="Alpha", attribution=attribution)["ok"]
    assert tail.status()["client_id"] == "app-a"
    sim.on_line(LogLevel.INFO, "com.example.alpha", "requestId=abcdef12 ok")

    tail.start("simulator", udid="SIM-2", process="Beta")
    assert sim.starts == [SIM, "SIM-2"]
    assert tail.status()["client_id"] is None
    sim.on_line(LogLevel.INFO, "com.example.beta", "other app")

    rows = {log.message: log for log in store.list_logs()}
    assert rows["requestId=abcdef12 ok"].client_id == "app-a"
    assert rows["requestId=abcdef12 ok"].udid == SIM
    assert rows["other app"].client_id is None
    assert rows["other app"].udid == "SIM-2"
