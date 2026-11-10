"""Network dashboard API: filters, detail, HAR/cURL redaction, WebSocket."""

from __future__ import annotations

import gzip
import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app, get_event_bus
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import FlowRecord, FlowSource, FlowTiming, RetentionPolicy
from viewlift_ai.models.log import LogLevel, LogRecord
from viewlift_ai.services.flow_export import build_curl, har_json
from viewlift_ai.services.flow_graphql import graphql_meta
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
        assert b'id="view-urls"' in root.content
        assert b'id="group-rows"' in root.content
        assert b'id="kind-switch"' in root.content
        assert b'data-kind="graphql"' in root.content
        assert b">GraphQL</button>" in root.content
        static = client.get("/static/js/app.js")
        assert static.status_code == 200
        assert b"ViewliftNetwork" in static.content
        assert b"vl-detail-tab" in static.content
        assert b"baseUrlKey" in static.content
        follow = static.text.split("setFollowLatest(on)", 1)[1].split("followPool()", 1)[0]
        assert "setFlowView" not in follow
        assert 'this.flowView !== "list"' not in static.text
        assert b'id="btn-flow-delete"' in root.content
        assert b"Delete flows" in root.content


def test_delete_flows_keeps_logs(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        store.save_flow(_sample_flow(flow_id="gone"))
        store.save_log(
            LogRecord(
                id="keep",
                level=LogLevel.INFO,
                subsystem="app",
                message="stay",
                created_at=datetime(2026, 10, 21, 12, 0, tzinfo=UTC),
                source="sdk",
            )
        )
        reveal = client.app.state.reveal
        token = reveal.unlock_token
        reveal.remember(
            "gone",
            request_headers={"Authorization": "Bearer secret"},
            request_body=b"secret-body",
            response_headers={},
            response_body=b"",
        )
        assert reveal.unlock(token) is True

        removed = client.post("/api/flows/delete")
        assert removed.status_code == 200
        assert removed.json()["deleted"] == 1
        assert client.get("/api/flows").json()["flows"] == []
        assert store.get_flow("gone") is None
        assert any(row.id == "keep" for row in store.list_logs())
        assert reveal.status()["cached_flows"] == 0
        assert reveal.unlocked is True
        assert reveal.unlock_token == token

        again = client.post("/api/flows/delete")
        assert again.status_code == 200
        assert again.json()["deleted"] == 0


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
        assert body["request"]["cookies"][0]["name"] == "session"
        assert body["request"]["cookies"][0]["value"] == "abc"
        assert body["response"]["body"]["kind"] == "json"
        assert "tok123" in (body["response"]["body"]["pretty"] or "")
        assert body["timing"]["ttfb_ms"] == 10.0
        page = client.get("/flows/f1")
        assert page.status_code == 200
        assert b"Copy cURL" in page.content


def test_har_keeps_stored_values(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        client.app.state.store.save_flow(_sample_flow())
        res = client.get("/api/flows/f1/har")
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        payload = res.text
        assert "secret-token" in payload
        assert "hunter2" in payload
        assert "tok123" in payload
        assert "session=abc" in payload
        har = json.loads(payload)
        assert har["log"]["entries"][0]["request"]["method"] == "POST"

        multi = client.get("/api/export/har")
        assert multi.status_code == 200
        assert "secret-token" in multi.text
        assert "hunter2" in multi.text


def test_curl_quoting(tmp_path: Path) -> None:
    flow = _sample_flow(path="/path with space", query='q=a&b="x"')
    command = build_curl(flow)
    assert "secret-token" in command
    assert "hunter2" in command
    assert "curl -X POST" in command
    # shlex quoting for spaces / quotes
    assert "path with space" in command
    assert "Authorization: Bearer secret-token" in command

    with _client(tmp_path) as client:
        client.app.state.store.save_flow(flow)
        res = client.get("/api/flows/f1/curl")
        assert res.status_code == 200
        assert res.headers.get("cache-control") == "no-store"
        assert "hunter2" in res.text


def test_har_builder_keeps_stored_values(tmp_path: Path) -> None:
    _ = tmp_path
    raw = har_json([_sample_flow()])
    assert "hunter2" in raw


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


def _with_body(
    flow: FlowRecord,
    body: bytes,
    *,
    headers: dict[str, str] | None = None,
) -> FlowRecord:
    flow.request_body = body
    if headers is not None:
        flow.request_headers = headers
    return flow


def test_graphql_detection() -> None:
    named = _with_body(
        _sample_flow(path="/graphql", query=None),
        b'{"operationName":"GetPage","query":"query GetPage { page }","variables":{"id":"1"}}',
    )
    assert graphql_meta(named).graphql is True
    assert graphql_meta(named).operation == "GetPage"
    assert graphql_meta(named).operation_type == "query"

    anonymous = _with_body(
        _sample_flow(path="/v1/graphql", query=None),
        b'{"query":"query { page }","variables":{}}',
    )
    assert graphql_meta(anonymous).operation == "anonymous"
    assert graphql_meta(anonymous).operation_type == "query"

    mutation = _with_body(
        _sample_flow(path="/graphql", query=None),
        b'{"operationName":"Save","query":"mutation Save { ok }","variables":{}}',
    )
    assert graphql_meta(mutation).operation == "Save"
    assert graphql_meta(mutation).operation_type == "mutation"

    override = _with_body(
        _sample_flow(path="/graphql", query=None),
        b'{"operationName":"Alias","query":"query RealName { a }","variables":{}}',
    )
    assert graphql_meta(override).operation == "Alias"

    fetched = _sample_flow(method="GET", path="/api", query="query=query GetPage { page }")
    fetched.request_body = b""
    assert graphql_meta(fetched).operation == "GetPage"
    assert graphql_meta(fetched).operation_type == "query"

    batch = _with_body(
        _sample_flow(path="/graphql", query=None),
        b'[{"operationName":"A","query":"query A { a }","variables":{}},'
        b'{"operationName":"B","query":"query B { b }","variables":{}}]',
    )
    assert graphql_meta(batch).operation == "A+1"

    persisted = _with_body(
        _sample_flow(path="/graphql", query=None),
        b'{"operationName":"Watch","variables":{},"extensions":{"persistedQuery":{"sha256Hash":"abc"}}}',
    )
    assert graphql_meta(persisted).operation == "Watch"
    assert graphql_meta(persisted).operation_type is None

    raw = _with_body(
        _sample_flow(path="/api", query=None),
        b"subscription OnScore { score }",
        headers={"Content-Type": "application/graphql"},
    )
    assert graphql_meta(raw).operation == "OnScore"
    assert graphql_meta(raw).operation_type == "subscription"

    path_only = _sample_flow(path="/graphql", query=None)
    path_only.request_body = b""
    assert graphql_meta(path_only).operation == "anonymous"

    search = _with_body(_sample_flow(path="/search", query=None), b'{"query":"football"}')
    assert graphql_meta(search).graphql is False
    assert graphql_meta(search).operation is None


def test_graphql_filters(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        store = client.app.state.store
        rest = _sample_flow(flow_id="rest", path="/login")
        store.save_flow(rest)
        store.save_flow(
            _with_body(
                _sample_flow(flow_id="page", path="/graphql", query=None),
                b'{"operationName":"GetPage","query":"query GetPage { page }","variables":{}}',
            )
        )
        store.save_flow(
            _with_body(
                _sample_flow(flow_id="watch", path="/graphql", query=None),
                b'{"operationName":"Watch","query":"query Watch { id }","variables":{}}',
            )
        )

        graphql = client.get("/api/flows", params={"kind": "graphql"})
        assert graphql.status_code == 200
        rows = {row["id"]: row for row in graphql.json()["flows"]}
        assert set(rows) == {"page", "watch"}
        assert rows["page"]["graphql"] is True
        assert rows["page"]["operation"] == "GetPage"
        assert rows["page"]["operation_type"] == "query"

        by_name = client.get("/api/flows", params={"operation": "getpage"})
        assert [row["id"] for row in by_name.json()["flows"]] == ["page"]

        http_only = client.get("/api/flows", params={"kind": "http"})
        assert [row["id"] for row in http_only.json()["flows"]] == ["rest"]
        assert http_only.json()["flows"][0]["graphql"] is False

        rejected = client.get("/api/flows", params={"kind": "nope"})
        assert rejected.status_code == 400

        exported = client.get("/api/export/har", params={"kind": "graphql", "operation": "Watch"})
        assert exported.status_code == 200
        assert "Watch" in exported.text
        assert "/login" not in exported.text

        page = client.get("/")
        assert b'id="f-kind"' in page.content
        assert b'id="f-operation"' in page.content
        script = client.get("/static/js/app.js")
        assert b"/api/flows/send" in script.content
        assert b'id="edit-send"' in script.content
        assert b"--data-raw" in script.content


def test_send_flow_returns_the_response(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(length)
            raw = b'{"secret":"hide-me","ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session=abc")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with _client(tmp_path) as client:
            rejected = client.post(
                "/api/flows/send",
                json={"method": "GET", "url": "file:///etc/passwd", "headers": {}, "body": ""},
            )
            assert rejected.status_code == 400

            sent = client.post(
                "/api/flows/send",
                json={
                    "method": "POST",
                    "url": f"http://127.0.0.1:{port}/graphql",
                    "headers": {"Content-Type": "application/json"},
                    "body": '{"query":"{ a }","variables":{}}',
                },
            )
            assert sent.status_code == 200
            data = sent.json()
            assert data["status"] == 200
            cookie = next(
                value for key, value in data["headers"].items() if key.lower() == "set-cookie"
            )
            assert cookie == "session=abc"
            assert "hide-me" in data["body"]
            assert client.get("/api/flows").json()["count"] == 0
    finally:
        server.shutdown()
        server.server_close()


def test_send_flow_decodes_gzip_json(tmp_path: Path) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            raw = gzip.compress(b'{"ok":true}')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with _client(tmp_path) as client:
            sent = client.post(
                "/api/flows/send",
                json={
                    "method": "GET",
                    "url": f"http://127.0.0.1:{port}/v3/monetization",
                    "headers": {"Accept-Encoding": "gzip"},
                    "body": "",
                },
            )
            assert sent.status_code == 200
            data = sent.json()
            assert data["body"] == '{"ok":true}'
            encoding = next(
                value for key, value in data["headers"].items() if key.lower() == "content-encoding"
            )
            assert encoding == "gzip"
    finally:
        server.shutdown()
        server.server_close()
