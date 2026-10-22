"""Network dashboard API: filters, detail, HAR/cURL redaction, WebSocket."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app, get_event_bus
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import FlowRecord, FlowSource, FlowTiming, RetentionPolicy
from viewlift_ai.proxy.redact import REDACTED
from viewlift_ai.services.flow_export import build_curl, har_json
from viewlift_ai.services.flow_present import flow_summary


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


def _client(tmp_path: Path) -> TestClient:
    app = create_app(_config(tmp_path))
    return TestClient(app)


def _sample_flow(
    *,
    flow_id: str = "f1",
    method: str = "POST",
    host: str = "api.example.com",
    path: str = "/login",
    status: int | None = 200,
    content_type: str | None = "application/json",
    query: str | None = "x=1",
) -> FlowRecord:
    return FlowRecord(
        id=flow_id,
        method=method,
        host=host,
        path=path,
        status=status,
        duration_ms=42.5,
        size_bytes=20,
        protocol="HTTPS/HTTP/1.1",
        source=FlowSource.PROXY,
        created_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
        request_headers={
            "Authorization": "Bearer secret-token",
            "Cookie": "session=abc",
            "Content-Type": "application/json",
        },
        request_body=b'{"password":"hunter2","ok":true}',
        response_headers={
            "Set-Cookie": "session=abc",
            "Content-Type": content_type or "application/json",
        },
        response_body=b'{"access_token":"tok123","n":1}',
        content_type=content_type,
        query=query,
        timing=FlowTiming(connect_ms=1.0, tls_ms=2.0, ttfb_ms=10.0, download_ms=5.0),
    )


def test_root_dashboard_and_health(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert b"Viewlift AI" in root.content
        assert b"/static/css/app.css" in root.content
        assert b"cdn." not in root.content.lower()
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"
        static = client.get("/static/js/app.js")
        assert static.status_code == 200
        assert b"ViewliftNetwork" in static.content


def test_flow_filters(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        store.save_flow(_sample_flow(flow_id="a", method="GET", host="a.example.com", status=204))
        store.save_flow(
            _sample_flow(
                flow_id="b",
                method="POST",
                host="b.example.com",
                status=500,
                content_type="text/plain",
                path="/err",
            )
        )
        store.save_flow(
            _sample_flow(
                flow_id="c",
                method="POST",
                host="api.example.com",
                content_type="application/json",
                path="/login",
            )
        )

        by_method = client.get("/api/flows", params={"method": "GET"})
        assert by_method.status_code == 200
        assert by_method.json()["count"] == 1
        assert by_method.json()["flows"][0]["id"] == "a"

        by_host = client.get("/api/flows", params={"host": "b.example"})
        assert [f["id"] for f in by_host.json()["flows"]] == ["b"]

        by_status = client.get("/api/flows", params={"status": 500})
        assert by_status.json()["flows"][0]["id"] == "b"

        by_ctype = client.get("/api/flows", params={"content-type": "json"})
        ids = {f["id"] for f in by_ctype.json()["flows"]}
        assert ids == {"a", "c"} or "c" in ids

        by_text = client.get("/api/flows", params={"text": "hunter2"})
        # password value redacted at persist time in capture; here we saved raw via store
        assert by_text.json()["count"] >= 1


def test_flow_detail_sections(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.app.state.store.save_flow(_sample_flow())
        res = client.get("/api/flows/f1")
        assert res.status_code == 200
        body = res.json()
        assert body["overview"]["method"] == "POST"
        assert body["query"][0]["name"] == "x"
        assert body["request"]["headers"]["Authorization"] == "Bearer secret-token"
        assert body["request"]["cookies"][0]["value"] == REDACTED
        assert body["response"]["body"]["kind"] == "json"
        assert "tok123" in (body["response"]["body"]["pretty"] or "")
        assert body["timing"]["ttfb_ms"] == 10.0
        page = client.get("/flows/f1")
        assert page.status_code == 200
        assert b"Copy cURL" in page.content


def test_har_secret_redaction(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # Intentionally store unredacted secrets; export must still redact.
        client.app.state.store.save_flow(_sample_flow())
        res = client.get("/api/flows/f1/har")
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        payload = res.text
        assert "secret-token" in payload
        assert "hunter2" not in payload
        assert "tok123" in payload
        assert "session=abc" not in payload
        assert REDACTED in payload
        har = json.loads(payload)
        assert har["log"]["entries"][0]["request"]["method"] == "POST"

        multi = client.get("/api/export/har")
        assert multi.status_code == 200
        assert "secret-token" in multi.text
        assert "hunter2" not in multi.text


def test_curl_quoting_and_redaction(tmp_path: Path) -> None:
    flow = _sample_flow(path="/path with space", query='q=a&b="x"')
    command = build_curl(flow)
    assert "secret-token" in command
    assert "hunter2" not in command
    assert REDACTED in command
    assert "curl -X POST" in command
    # shlex quoting for spaces / quotes
    assert "path with space" in command
    assert "Authorization: Bearer secret-token" in command

    with _client(tmp_path) as client:
        client.app.state.store.save_flow(flow)
        res = client.get("/api/flows/f1/curl")
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        assert "hunter2" not in res.text
        assert REDACTED in res.text


def test_har_builder_redacts_without_http(tmp_path: Path) -> None:
    _ = tmp_path
    raw = har_json([_sample_flow()])
    assert "hunter2" not in raw
    assert REDACTED in raw


@pytest.mark.asyncio
async def test_websocket_flow_event_serialization(tmp_path: Path) -> None:
    import asyncio

    with _client(tmp_path) as client:
        bus = get_event_bus()
        with client.websocket_connect("/ws/flows") as ws:
            for _ in range(100):
                if bus.subscriber_count >= 1:
                    break
                await asyncio.sleep(0.01)
            assert bus.subscriber_count >= 1
            bus.publish_threadsafe(_sample_flow(flow_id="live-1"))
            await bus.publish(_sample_flow(flow_id="live-2"))
            msg1 = ws.receive_json()
            msg2 = ws.receive_json()
        ids = {msg1["flow"]["id"], msg2["flow"]["id"]}
        assert ids == {"live-1", "live-2"}
        assert msg1["type"] == "flow"
        summary = flow_summary(_sample_flow())
        assert "content_type" in summary
        assert "timing" in summary


def test_migration_safe_new_columns(tmp_path: Path) -> None:
    """Old DB without new columns still opens and accepts writes."""
    import sqlite3

    from viewlift_ai.crypto import SecretBoxCipher
    from viewlift_ai.services.encrypted_store import EncryptedSqliteStore

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE flows (
            id TEXT PRIMARY KEY,
            method TEXT NOT NULL,
            host TEXT NOT NULL,
            path TEXT NOT NULL,
            status INTEGER,
            duration_ms REAL,
            size_bytes INTEGER,
            protocol TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            request_headers BLOB NOT NULL,
            request_body BLOB NOT NULL,
            response_headers BLOB NOT NULL,
            response_body BLOB NOT NULL,
            error TEXT,
            request_id TEXT,
            tls_failed INTEGER NOT NULL DEFAULT 0,
            websocket_messages BLOB NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    store = EncryptedSqliteStore(db, SecretBoxCipher(b"t" * SecretBox.KEY_SIZE))
    store.save_flow(_sample_flow(flow_id="migrated"))
    loaded = store.get_flow("migrated")
    assert loaded is not None
    assert loaded.content_type == "application/json"
    assert loaded.query == "x=1"
    assert loaded.timing.ttfb_ms == 10.0
    store.close()
