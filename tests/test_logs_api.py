"""Logs page APIs and requestId → flow linking."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import FlowRecord, FlowSource, RetentionPolicy
from viewlift_ai.models.log import LogLevel, LogRecord


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


def test_logs_page_and_api(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get("/logs")
        assert page.status_code == 200
        assert b"Delete logs" in page.content
        assert b"stdout" in page.content
        assert b"os_log" in page.content

        store = client.app.state.store
        rid = "req-link-1"
        flow_id = str(uuid4())
        store.save_flow(
            FlowRecord(
                id=flow_id,
                method="GET",
                host="api.example.com",
                path="/x",
                status=200,
                duration_ms=1.0,
                size_bytes=0,
                protocol="HTTPS",
                source=FlowSource.SDK,
                created_at=datetime.now(UTC),
                request_id=rid,
            )
        )
        log_id = str(uuid4())
        store.save_log(
            LogRecord(
                id=log_id,
                level=LogLevel.INFO,
                subsystem="app",
                message="hello",
                created_at=datetime.now(UTC),
                request_id=rid,
                source="sdk",
            )
        )
        listed = client.get("/api/logs?source=sdk")
        assert listed.status_code == 200
        assert any(row["id"] == log_id for row in listed.json()["logs"])

        detail = client.get(f"/api/logs/{log_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["linked_flow_id"] == flow_id
        assert "stdout" in body["console_note"]
        assert "os_log is not included" in body["console_note"]

        sources = client.get("/api/logs/sources")
        assert sources.status_code == 200
        assert "simulators" in sources.json()

        removed = client.post("/api/logs/delete")
        assert removed.status_code == 200
        assert removed.json()["deleted"] == 1
        assert client.get("/api/logs").json()["logs"] == []
        assert store.get_flow(flow_id) is not None


def test_device_log_capture_ignores_vendor_device_key(tmp_path: Path) -> None:
    class _FakeDevice:
        def __init__(self) -> None:
            self.started: str | None = "unset"
            self._running = False

        def list_devices(self) -> list[dict[str, object]]:
            return [{"ok": True, "udid": "00008110-REAL", "name": "iPhone"}]

        def start(self, udid: str | None, *, on_line: object, process: str | None = None) -> dict:
            self.started = udid
            self._running = True
            return {"ok": True, "detail": "streaming", "udid": udid or "00008110-REAL"}

        def stop(self) -> dict[str, object]:
            self._running = False
            return {"ok": True, "detail": "stopped"}

        @property
        def running(self) -> bool:
            return self._running

        @property
        def active_udid(self) -> str | None:
            return "00008110-REAL" if self._running else None

    with TestClient(create_app(_config(tmp_path))) as client:
        device = _FakeDevice()
        client.app.state.log_tail.device = device
        client.app.state.debug_sessions.register(
            {
                "client_id": "phone-1",
                "app_name": "OneApp",
                "bundle_id": "com.example.one",
                "platform": "ios",
                "executable": "OneApp",
                "device_key": "vendor:832E7336-F5DA-491B-95CC-2CDB4E945CDD",
                "is_simulator": False,
            }
        )
        started = client.post("/api/debug/logs/start", json={"client_id": "phone-1"})
        assert started.status_code == 200
        assert started.json()["ok"] is True
        assert device.started is None


def test_debug_log_capture_requires_a_connected_app(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        missing = client.post("/api/debug/logs/start", json={})
        assert missing.status_code == 400
        unknown = client.post("/api/debug/logs/start", json={"client_id": "missing"})
        assert unknown.status_code == 404
        stopped = client.post("/api/debug/logs/stop")
        assert stopped.status_code == 200
        assert stopped.json()["ok"] is True


def test_logs_page_has_no_os_log_stream_controls(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        page = client.get("/logs").content
        assert b"btn-log-start" not in page
        assert b'id="log-source"' not in page
        assert b'id="log-udid"' not in page
        # POST on these paths is rejected: 404, or 405 where the GET /api/logs/{log_id} route matches.
        for path, body in (
            ("/api/logs/start", {"source": "simulator"}),
            ("/api/logs/stop", None),
            ("/api/logs/filter", {"process": "App"}),
        ):
            status = client.post(path, json=body).status_code
            assert status in (404, 405), path


def test_ingest_keeps_stdout_and_stderr_sources(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        ingest = client.app.state.ingest
        out = ingest.ingest_log(
            {"level": "info", "subsystem": "com.example.app", "message": "hello", "source": "stdout"}
        )
        err = ingest.ingest_log(
            {"level": "error", "subsystem": "com.example.app", "message": "boom", "source": "stderr"}
        )
        explicit = ingest.ingest_log({"level": "info", "message": "explicit", "source": "os_log"})
        assert out["source"] == "stdout"
        assert err["source"] == "stderr"
        assert explicit["source"] == "sdk"
        console = client.get("/api/logs", params={"source": "stdout,stderr"}).json()["logs"]
        assert {row["message"] for row in console} == {"hello", "boom"}


def test_multiline_console_message_stays_one_log(tmp_path: Path) -> None:
    message = (
        "\"Analytics – Tracking: updateCurrent\"\n"
        "\"PlayheadTracker: Emitting ping\"\n"
        "\"done\""
    )
    with TestClient(create_app(_config(tmp_path))) as client:
        ingest = client.app.state.ingest
        saved = ingest.ingest_log(
            {
                "level": "info",
                "subsystem": "com.viewlift.spinco",
                "message": message,
                "source": "stdout",
            }
        )
        listed = client.get("/api/logs", params={"source": "stdout,stderr"}).json()["logs"]
        assert len(listed) == 1
        assert listed[0]["id"] == saved["id"]
        assert listed[0]["message"] == message
        detail = client.get(f"/api/logs/{saved['id']}").json()
        assert detail["message"] == message
        assert b"log-message" in client.get("/static/js/app.js").content
        assert b"td.log-message" in client.get("/static/css/app.css").content


def _sample_log(log_id: str) -> LogRecord:
    return LogRecord(
        id=log_id,
        level=LogLevel.INFO,
        subsystem="app",
        message="keep-or-drop",
        created_at=datetime.now(UTC),
        source="sdk",
    )


def test_reload_does_not_wipe_capture(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client.app.state.capture_wipe_delay = 0.15
        store = client.app.state.store
        store.save_log(_sample_log("stay"))
        assert client.post("/api/session/ping", json={"id": "tab", "page": 1}).status_code == 200
        assert client.post("/api/session/leave", json={"id": "tab", "page": 1}).json()["scheduled"]
        assert client.post("/api/session/ping", json={"id": "tab", "page": 2}).status_code == 200
        time.sleep(0.4)
        assert any(row.id == "stay" for row in store.list_logs())


def test_other_tab_keeps_capture(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client.app.state.capture_wipe_delay = 0.05
        store = client.app.state.store
        store.save_log(_sample_log("stay"))
        client.post("/api/session/ping", json={"id": "a", "page": 1})
        client.post("/api/session/ping", json={"id": "b", "page": 1})
        left = client.post("/api/session/leave", json={"id": "a", "page": 1})
        assert left.json()["scheduled"] is False
        time.sleep(0.2)
        assert any(row.id == "stay" for row in store.list_logs())


def test_last_tab_close_wipes_capture(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        client.app.state.capture_wipe_delay = 0.05
        store = client.app.state.store
        flow_id = str(uuid4())
        store.save_flow(
            FlowRecord(
                id=flow_id,
                method="GET",
                host="api.example.com",
                path="/x",
                status=200,
                duration_ms=1.0,
                size_bytes=4,
                protocol="HTTPS",
                source=FlowSource.SDK,
                created_at=datetime.now(UTC),
            )
        )
        store.save_log(_sample_log("gone"))
        rules = tmp_path / "rules.json"
        rules.write_text('{"kept": true}', encoding="utf-8")
        client.post("/api/session/ping", json={"id": "only", "page": 1})
        left = client.post("/api/session/leave", json={"id": "only", "page": 1})
        assert left.json()["scheduled"] is True
        time.sleep(0.4)
        assert store.list_logs() == []
        assert store.list_flows() == []
        assert rules.read_text(encoding="utf-8") == '{"kept": true}'


def test_setup_sdk_status_wired(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        status = client.get("/api/setup/status")
        assert status.status_code == 200
        sdk = status.json()["sdk_receiver"]
        assert sdk["enabled"] is False
        assert "disabled" in (sdk.get("note") or "").lower() or sdk["receiver_url"] is None
