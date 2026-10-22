"""Logs page APIs and requestId → flow linking."""

from __future__ import annotations

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
        assert b"TestFlight" in page.content or b"private" in page.content.lower()

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
        assert "<private>" in body["testflight_private_note"] or "private" in body[
            "testflight_private_note"
        ].lower()

        sources = client.get("/api/logs/sources")
        assert sources.status_code == 200
        assert "simulators" in sources.json()


def test_setup_sdk_status_wired(tmp_path: Path) -> None:
    with TestClient(create_app(_config(tmp_path))) as client:
        status = client.get("/api/setup/status")
        assert status.status_code == 200
        sdk = status.json()["sdk_receiver"]
        assert sdk["enabled"] is False
        assert "disabled" in (sdk.get("note") or "").lower() or sdk["receiver_url"] is None
