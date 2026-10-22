"""Capture mapping: redact, PROXY source, TLS failure, websocket messages."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import TLS_FAILED_LIKELY_PINNING, FlowSource, WebSocketMessageRecord
from viewlift_ai.proxy.redact import REDACTED, RedactionService
from viewlift_ai.services.capture import CaptureService, HttpCaptureSnapshot
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.event_bus import FlowEventBus


def _store(tmp_path: Path) -> EncryptedSqliteStore:
    return EncryptedSqliteStore(tmp_path / "t.db", SecretBoxCipher(b"t" * SecretBox.KEY_SIZE))


def test_map_snapshot_redacts_and_sets_proxy_source(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bus = FlowEventBus()
    svc = CaptureService(store, bus, RedactionService())
    snap = HttpCaptureSnapshot(
        id="f1",
        method="POST",
        host="api.example.com",
        path="/login",
        status=200,
        duration_ms=10.0,
        protocol="HTTP/1.1",
        request_headers={"Authorization": "Bearer x", "X-Req": "1"},
        request_body=b'{"password":"secret","ok":true}',
        response_headers={"Set-Cookie": "a=b", "X-Res": "2"},
        response_body=b'{"access_token":"tok","n":1}',
        websocket_messages=(
            WebSocketMessageRecord(
                from_client=True,
                content=b'{"token":"ws"}',
                is_text=True,
                timestamp=1.0,
            ),
        ),
        created_at=datetime(2026, 10, 21, tzinfo=UTC),
    )
    flow = svc.map_snapshot(snap)
    assert flow.source == FlowSource.PROXY
    assert flow.request_headers["Authorization"] == "Bearer x"
    assert flow.request_headers["X-Req"] == "1"
    assert REDACTED.encode() in flow.request_body
    assert b"secret" not in flow.request_body
    assert flow.response_headers["Set-Cookie"] == REDACTED
    assert b'"tok"' in flow.response_body
    assert flow.websocket_messages[0].content == b'{"token":"ws"}'
    store.close()


def test_persist_writes_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bus = FlowEventBus()
    svc = CaptureService(store, bus)
    svc.persist_http(
        HttpCaptureSnapshot(
            id="f2",
            method="GET",
            host="h",
            path="/",
            status=204,
            duration_ms=1.0,
            protocol="HTTP/1.1",
        )
    )
    loaded = store.get_flow("f2")
    assert loaded is not None
    assert loaded.source == FlowSource.PROXY
    store.close()


def test_tls_failure_message(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bus = FlowEventBus()
    svc = CaptureService(store, bus)
    flow = svc.persist_tls_failure(
        flow_id="tls1",
        host="pinned.app",
        error="The client does not trust the proxy's certificate",
    )
    assert flow.tls_failed is True
    assert flow.error == TLS_FAILED_LIKELY_PINNING
    assert flow.request_body == b""
    assert flow.response_body == b""
    store.close()


def test_non_body_recording_strips_payloads(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bus = FlowEventBus()
    svc = CaptureService(store, bus)
    flow = svc.map_snapshot(
        HttpCaptureSnapshot(
            id="f3",
            method="GET",
            host="h",
            path="/",
            status=200,
            duration_ms=1.0,
            protocol="HTTP/1.1",
            request_body=b"secret-body",
            response_body=b"secret-resp",
            record_bodies=False,
        )
    )
    assert flow.request_body == b""
    assert flow.response_body == b""
    assert flow.size_bytes is None
    store.close()
