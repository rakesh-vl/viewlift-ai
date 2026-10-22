"""Encrypted SQLite flow/log store and retention."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nacl.secret import SecretBox

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import (
    FlowRecord,
    FlowSource,
    RetentionPolicy,
    WebSocketMessageRecord,
)
from viewlift_ai.models.log import LogLevel, LogRecord
from viewlift_ai.services.encrypted_store import EncryptedSqliteStore
from viewlift_ai.services.protocols import StoreProtocol


def _cipher() -> SecretBoxCipher:
    return SecretBoxCipher(b"t" * SecretBox.KEY_SIZE)


def _flow(*, flow_id: str = "f1", created_at: datetime | None = None) -> FlowRecord:
    return FlowRecord(
        id=flow_id,
        method="GET",
        host="api.example.com",
        path="/v1/me",
        status=200,
        duration_ms=12.5,
        size_bytes=42,
        protocol="HTTP/2",
        source=FlowSource.PROXY,
        created_at=created_at or datetime.now(UTC),
        request_headers={"Authorization": "Bearer secret-token"},
        request_body=b'{"ping":true}',
        response_headers={"Content-Type": "application/json"},
        response_body=b'{"ok":true}',
        request_id="req-1",
        websocket_messages=(
            WebSocketMessageRecord(
                from_client=True,
                content=b'{"ping":1}',
                is_text=True,
                timestamp=1.5,
            ),
        ),
    )


def test_store_implements_protocol(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(tmp_path / "t.db", _cipher())
    assert isinstance(store, StoreProtocol)
    store.close()


def test_flow_round_trip_decrypts_in_memory(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(tmp_path / "t.db", _cipher())
    store.save_flow(_flow())
    loaded = store.get_flow("f1")
    assert loaded is not None
    assert loaded.request_headers["Authorization"] == "Bearer secret-token"
    assert loaded.request_body == b'{"ping":true}'
    assert loaded.response_body == b'{"ok":true}'
    assert len(loaded.websocket_messages) == 1
    assert loaded.websocket_messages[0].content == b'{"ping":1}'
    assert loaded.websocket_messages[0].from_client is True
    store.close()


def test_sensitive_columns_encrypted_at_rest(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    store = EncryptedSqliteStore(db, _cipher())
    store.save_flow(_flow())
    store.close()

    raw = sqlite3.connect(db)
    row = raw.execute(
        "SELECT request_headers, request_body, response_headers, response_body FROM flows"
    ).fetchone()
    raw.close()
    assert row is not None
    for cell in row:
        assert isinstance(cell, bytes)
        assert b"secret-token" not in cell
        assert b"Bearer" not in cell
        assert b'{"ping":true}' not in cell


def test_log_and_request_link(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(tmp_path / "t.db", _cipher())
    store.save_log(
        LogRecord(
            id="l1",
            level=LogLevel.INFO,
            subsystem="net",
            message="hello secret-token",
            created_at=datetime.now(UTC),
            request_id="req-1",
            source="simulator",
        )
    )
    linked = store.logs_for_request("req-1")
    assert len(linked) == 1
    assert linked[0].message == "hello secret-token"
    store.close()

    raw = sqlite3.connect(tmp_path / "t.db")
    cell = raw.execute("SELECT message FROM logs WHERE id = ?", ("l1",)).fetchone()[0]
    raw.close()
    assert isinstance(cell, bytes)
    assert b"secret-token" not in cell
    assert b"hello" not in cell


def test_log_plaintext_migration(tmp_path: Path) -> None:
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE logs (
            id TEXT PRIMARY KEY,
            level TEXT NOT NULL,
            subsystem TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL,
            request_id TEXT,
            source TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy",
            "info",
            "net",
            "plaintext-secret",
            datetime.now(UTC).isoformat(),
            None,
            "simulator",
        ),
    )
    conn.commit()
    conn.close()

    store = EncryptedSqliteStore(db, _cipher())
    loaded = store.get_log("legacy")
    assert loaded is not None
    assert loaded.message == "plaintext-secret"
    store.close()

    raw = sqlite3.connect(db)
    cell = raw.execute("SELECT message FROM logs WHERE id = ?", ("legacy",)).fetchone()[0]
    raw.close()
    assert isinstance(cell, bytes)
    assert b"plaintext-secret" not in cell


def test_session_wipe(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(tmp_path / "t.db", _cipher())
    store.save_flow(_flow())
    store.save_log(
        LogRecord(
            id="l1",
            level=LogLevel.DEBUG,
            subsystem="x",
            message="m",
            created_at=datetime.now(UTC),
        )
    )
    store.save_debug_event(
        event_id="d1",
        client_id="phone-1",
        module="performance",
        kind="sample",
        payload={"cpu": 4.2},
        created_at=datetime.now(UTC),
    )
    deleted = store.wipe_session()
    assert deleted >= 3
    assert store.list_flows() == []
    assert store.list_logs() == []
    assert store.list_debug_events(client_id="phone-1") == []
    store.close()


def test_24h_cleanup_removes_old_rows(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(
        tmp_path / "t.db",
        _cipher(),
        retention=RetentionPolicy.HOURS_24,
    )
    now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    store.save_flow(_flow(flow_id="old", created_at=now - timedelta(hours=25)))
    store.save_flow(_flow(flow_id="new", created_at=now - timedelta(hours=1)))
    store.save_debug_event(
        event_id="debug-old",
        client_id="phone-1",
        module="performance",
        kind="sample",
        payload={},
        created_at=now - timedelta(hours=25),
    )
    store.save_debug_event(
        event_id="debug-new",
        client_id="phone-1",
        module="performance",
        kind="sample",
        payload={},
        created_at=now - timedelta(hours=1),
    )
    removed = store.apply_retention_cleanup(now=now)
    assert removed == 2
    assert store.get_flow("old") is None
    assert store.get_flow("new") is not None
    events = store.list_debug_events(client_id="phone-1")
    assert [event["id"] for event in events] == ["debug-new"]
    store.close()


def test_concurrent_writes_from_threads(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    store = EncryptedSqliteStore(tmp_path / "t.db", _cipher())

    def write(index: int) -> None:
        store.save_flow(_flow(flow_id=f"f{index}"))
        store.save_debug_event(
            event_id=f"d{index}",
            client_id="phone-1",
            module="performance",
            kind="sample",
            payload={"i": index},
            created_at=datetime.now(UTC),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(write, range(200)))
    assert len(store.list_flows(limit=500)) == 200
    assert len(store.list_debug_events(client_id="phone-1", limit=500)) == 200
    store.close()


def test_session_retention_skips_age_cleanup(tmp_path: Path) -> None:
    store = EncryptedSqliteStore(
        tmp_path / "t.db",
        _cipher(),
        retention=RetentionPolicy.SESSION,
    )
    store.save_flow(
        _flow(created_at=datetime.now(UTC) - timedelta(days=7))
    )
    assert store.apply_retention_cleanup() == 0
    assert store.get_flow("f1") is not None
    store.close()
