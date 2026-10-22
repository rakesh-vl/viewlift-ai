"""Encrypted SQLite persistence for flows and logs."""

from __future__ import annotations

import base64
import functools
import inspect
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from viewlift_ai.crypto import SecretBoxCipher
from viewlift_ai.models.flow import (
    FlowRecord,
    FlowSource,
    FlowTiming,
    RetentionPolicy,
    WebSocketMessageRecord,
)
from viewlift_ai.models.log import LogLevel, LogRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS flows (
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
    websocket_messages BLOB NOT NULL,
    content_type TEXT,
    query TEXT,
    timing_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_flows_created_at ON flows(created_at);
CREATE INDEX IF NOT EXISTS idx_flows_request_id ON flows(request_id);
CREATE INDEX IF NOT EXISTS idx_flows_host ON flows(host);
CREATE INDEX IF NOT EXISTS idx_flows_method ON flows(method);

CREATE TABLE IF NOT EXISTS logs (
    id TEXT PRIMARY KEY,
    level TEXT NOT NULL,
    subsystem TEXT NOT NULL,
    message BLOB NOT NULL,
    created_at TEXT NOT NULL,
    request_id TEXT,
    source TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_logs_created_at ON logs(created_at);
CREATE INDEX IF NOT EXISTS idx_logs_request_id ON logs(request_id);

CREATE TABLE IF NOT EXISTS debug_events (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    module TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload BLOB NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_debug_events_client_module
ON debug_events(client_id, module, created_at);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

HOURS_24 = timedelta(hours=24)

def _serialize_public_methods[StoreT: type](cls: StoreT) -> StoreT:
    """One shared connection serves proxy, ingest, and API threads; one call at a time."""

    def wrap(method: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(method)
        def locked(self: Any, *args: Any, **kwargs: Any) -> Any:
            with self._db_lock:
                return method(self, *args, **kwargs)

        return locked

    for name, member in list(vars(cls).items()):
        if name.startswith("_") or not inspect.isfunction(member):
            continue
        setattr(cls, name, wrap(member))
    return cls


@_serialize_public_methods
class EncryptedSqliteStore:
    """SQLite store: sensitive flow headers/bodies encrypted with SecretBox."""

    def __init__(
        self,
        db_path: Path,
        cipher: SecretBoxCipher,
        *,
        retention: RetentionPolicy = RetentionPolicy.SESSION,
    ) -> None:
        self._db_path = db_path
        self._cipher = cipher
        self._retention = retention
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_lock = threading.RLock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._migrate_schema()
        self._conn.commit()

    @property
    def retention(self) -> RetentionPolicy:
        return self._retention

    def save_flow(self, flow: FlowRecord) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO flows (
                id, method, host, path, status, duration_ms, size_bytes, protocol,
                source, created_at, request_headers, request_body,
                response_headers, response_body, error, request_id, tls_failed,
                websocket_messages, content_type, query, timing_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow.id,
                flow.method,
                flow.host,
                flow.path,
                flow.status,
                flow.duration_ms,
                flow.size_bytes,
                flow.protocol,
                flow.source.value,
                _iso(flow.created_at),
                self._cipher.encrypt_headers(flow.request_headers),
                self._cipher.encrypt(flow.request_body),
                self._cipher.encrypt_headers(flow.response_headers),
                self._cipher.encrypt(flow.response_body),
                flow.error,
                flow.request_id,
                1 if flow.tls_failed else 0,
                self._cipher.encrypt(self._encode_websocket_messages(flow.websocket_messages)),
                flow.content_type,
                flow.query,
                _encode_timing(flow.timing),
            ),
        )
        self._conn.commit()

    def save_debug_event(
        self,
        *,
        event_id: str,
        client_id: str,
        module: str,
        kind: str,
        payload: dict[str, object],
        created_at: datetime,
    ) -> None:
        """Persist a bounded debug payload encrypted at rest."""
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        self._conn.execute(
            """
            INSERT OR REPLACE INTO debug_events (
                id, client_id, module, kind, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                client_id,
                module,
                kind,
                self._cipher.encrypt(encoded),
                _iso(created_at),
            ),
        )
        self._conn.commit()

    def list_debug_events(
        self,
        *,
        client_id: str,
        module: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, object]]:
        clauses = ["client_id = ?"]
        params: list[object] = [client_id]
        if module:
            clauses.append("module = ?")
            params.append(module)
        params.append(max(1, min(limit, 1000)))
        rows = self._conn.execute(
            f"""
            SELECT id, client_id, module, kind, payload, created_at
            FROM debug_events
            WHERE {" AND ".join(clauses)}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        out: list[dict[str, object]] = []
        for row in rows:
            payload = json.loads(self._cipher.decrypt(row["payload"]).decode("utf-8"))
            out.append(
                {
                    "id": row["id"],
                    "client_id": row["client_id"],
                    "module": row["module"],
                    "kind": row["kind"],
                    "payload": payload if isinstance(payload, dict) else {},
                    "created_at": row["created_at"],
                }
            )
        return out

    def delete_debug_events(self, *, client_id: str | None = None) -> int:
        if client_id:
            cursor = self._conn.execute(
                "DELETE FROM debug_events WHERE client_id = ?",
                (client_id,),
            )
        else:
            cursor = self._conn.execute("DELETE FROM debug_events")
        self._conn.commit()
        return int(cursor.rowcount)

    def get_flow(self, flow_id: str) -> FlowRecord | None:
        row = self._conn.execute("SELECT * FROM flows WHERE id = ?", (flow_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_flow(row)

    def list_flows(
        self,
        *,
        limit: int = 200,
        method: str | None = None,
        host: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        text: str | None = None,
    ) -> list[FlowRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if method:
            clauses.append("UPPER(method) = UPPER(?)")
            params.append(method)
        if host:
            clauses.append("host LIKE ?")
            params.append(f"%{host}%")
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if content_type:
            clauses.append("content_type LIKE ?")
            params.append(f"%{content_type}%")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        # Fetch extra rows when text filter needs post-decrypt matching.
        fetch_limit = limit if not text else min(max(limit * 5, 500), 2000)
        params.append(fetch_limit)
        rows = self._conn.execute(
            f"SELECT * FROM flows {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        flows = [self._row_to_flow(row) for row in rows]
        if text:
            needle = text.lower()
            flows = [f for f in flows if _flow_matches_text(f, needle)]
        return flows[:limit]

    def wipe_session(self) -> int:
        cur = self._conn.execute("DELETE FROM flows")
        logs = self._conn.execute("DELETE FROM logs")
        debug = self._conn.execute("DELETE FROM debug_events")
        self._conn.commit()
        return int(cur.rowcount) + int(logs.rowcount) + int(debug.rowcount)

    def save_log(self, log: LogRecord) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO logs (
                id, level, subsystem, message, created_at, request_id, source
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log.id,
                log.level.value,
                log.subsystem,
                self._cipher.encrypt(log.message.encode("utf-8")),
                _iso(log.created_at),
                log.request_id,
                log.source,
            ),
        )
        self._conn.commit()

    def get_log(self, log_id: str) -> LogRecord | None:
        row = self._conn.execute("SELECT * FROM logs WHERE id = ?", (log_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_log(row)

    def list_logs(
        self,
        *,
        limit: int = 200,
        source: str | None = None,
        level: str | None = None,
        subsystem: str | None = None,
        text: str | None = None,
    ) -> list[LogRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if source:
            clauses.append("source = ?")
            params.append(source)
        if level:
            clauses.append("level = ?")
            params.append(level.lower())
        if subsystem:
            clauses.append("subsystem LIKE ?")
            params.append(f"%{subsystem}%")
        # Message is encrypted; text filter matches after decrypt (bounded fetch).
        fetch_limit = limit if not text else min(max(limit * 5, 500), 2000)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(fetch_limit)
        rows = self._conn.execute(
            f"SELECT * FROM logs {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ).fetchall()
        logs = [self._row_to_log(row) for row in rows]
        if text:
            needle = text.lower()
            logs = [
                item
                for item in logs
                if needle in item.message.lower()
                or needle in item.subsystem.lower()
                or needle in (item.request_id or "").lower()
            ]
        return logs[:limit]

    def logs_for_request(self, request_id: str, *, limit: int = 100) -> list[LogRecord]:
        rows = self._conn.execute(
            """
            SELECT * FROM logs
            WHERE request_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (request_id, limit),
        ).fetchall()
        return [self._row_to_log(row) for row in rows]

    def flow_id_for_request(self, request_id: str) -> str | None:
        if not request_id:
            return None
        row = self._conn.execute(
            """
            SELECT id FROM flows
            WHERE request_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (request_id,),
        ).fetchone()
        if row is None:
            return None
        return str(row["id"])

    def cleanup_older_than(self, max_age: timedelta, *, now: datetime | None = None) -> int:
        """Delete flows/logs older than max_age. Used when retention is 24h."""
        cutoff = (now or datetime.now(UTC)) - max_age
        cutoff_s = _iso(cutoff)
        flows = self._conn.execute("DELETE FROM flows WHERE created_at < ?", (cutoff_s,))
        logs = self._conn.execute("DELETE FROM logs WHERE created_at < ?", (cutoff_s,))
        debug = self._conn.execute(
            "DELETE FROM debug_events WHERE created_at < ?",
            (cutoff_s,),
        )
        self._conn.commit()
        return int(flows.rowcount) + int(logs.rowcount) + int(debug.rowcount)

    def apply_retention_cleanup(self, *, now: datetime | None = None) -> int:
        """No-op for session retention; 24h policy drops rows older than 24 hours."""
        if self._retention != RetentionPolicy.HOURS_24:
            return 0
        return self.cleanup_older_than(HOURS_24, now=now)

    def close(self) -> None:
        self._conn.close()

    def _migrate_schema(self) -> None:
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(flows)")}
        if "websocket_messages" not in cols:
            empty = self._cipher.encrypt(b"[]")
            self._conn.execute(
                "ALTER TABLE flows ADD COLUMN websocket_messages BLOB NOT NULL DEFAULT X''"
            )
            self._conn.execute(
                "UPDATE flows SET websocket_messages = ? WHERE length(websocket_messages) = 0",
                (empty,),
            )
            cols.add("websocket_messages")
        if "content_type" not in cols:
            self._conn.execute("ALTER TABLE flows ADD COLUMN content_type TEXT")
        if "query" not in cols:
            self._conn.execute("ALTER TABLE flows ADD COLUMN query TEXT")
        if "timing_json" not in cols:
            self._conn.execute("ALTER TABLE flows ADD COLUMN timing_json TEXT")
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_flows_content_type ON flows(content_type)"
        )
        self._migrate_log_messages()

    def _migrate_log_messages(self) -> None:
        """Encrypt legacy plaintext log messages in place (idempotent)."""
        rows = self._conn.execute("SELECT id, message FROM logs").fetchall()
        for row in rows:
            raw = row["message"]
            if self._message_is_encrypted(raw):
                continue
            if isinstance(raw, bytes):
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    continue
            else:
                text = str(raw)
            encrypted = self._cipher.encrypt(text.encode("utf-8"))
            self._conn.execute(
                "UPDATE logs SET message = ? WHERE id = ?",
                (encrypted, row["id"]),
            )

    def _message_is_encrypted(self, raw: object) -> bool:
        if not isinstance(raw, (bytes, memoryview)):
            return False
        data = bytes(raw)
        if len(data) < 40:
            return False
        try:
            self._cipher.decrypt(data)
        except Exception:
            return False
        return True

    def _decode_log_message(self, raw: object) -> str:
        """Decrypt message blob; fall back to legacy plaintext TEXT/bytes."""
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw
        data = bytes(raw)
        try:
            return self._cipher.decrypt(data).decode("utf-8")
        except Exception:
            try:
                return data.decode("utf-8")
            except UnicodeDecodeError:
                return ""

    def _encode_websocket_messages(
        self, messages: list[WebSocketMessageRecord] | tuple[WebSocketMessageRecord, ...]
    ) -> bytes:
        payload = [
            {
                "from_client": msg.from_client,
                "content": base64.b64encode(msg.content).decode("ascii"),
                "is_text": msg.is_text,
                "timestamp": msg.timestamp,
            }
            for msg in messages
        ]
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    def _decode_websocket_messages(self, blob: bytes) -> tuple[WebSocketMessageRecord, ...]:
        if not blob:
            return ()
        raw = self._cipher.decrypt(blob)
        if not raw:
            return ()
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            return ()
        out: list[WebSocketMessageRecord] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            out.append(
                WebSocketMessageRecord(
                    from_client=bool(item.get("from_client")),
                    content=base64.b64decode(item.get("content", "")),
                    is_text=bool(item.get("is_text", True)),
                    timestamp=float(item.get("timestamp", 0.0)),
                )
            )
        return tuple(out)

    def _row_to_flow(self, row: sqlite3.Row) -> FlowRecord:
        keys = set(row.keys())
        ws_blob = row["websocket_messages"] if "websocket_messages" in keys else b""
        content_type = row["content_type"] if "content_type" in keys else None
        query = row["query"] if "query" in keys else None
        timing_raw = row["timing_json"] if "timing_json" in keys else None
        return FlowRecord(
            id=row["id"],
            method=row["method"],
            host=row["host"],
            path=row["path"],
            status=row["status"],
            duration_ms=row["duration_ms"],
            size_bytes=row["size_bytes"],
            protocol=row["protocol"],
            source=FlowSource(row["source"]),
            created_at=_parse_iso(row["created_at"]),
            request_headers=self._cipher.decrypt_headers(row["request_headers"]),
            request_body=self._cipher.decrypt(row["request_body"]),
            response_headers=self._cipher.decrypt_headers(row["response_headers"]),
            response_body=self._cipher.decrypt(row["response_body"]),
            error=row["error"],
            request_id=row["request_id"],
            tls_failed=bool(row["tls_failed"]),
            websocket_messages=self._decode_websocket_messages(ws_blob),
            content_type=content_type,
            query=query,
            timing=_decode_timing(timing_raw),
        )

    def _row_to_log(self, row: sqlite3.Row) -> LogRecord:
        return LogRecord(
            id=row["id"],
            level=LogLevel(row["level"]),
            subsystem=row["subsystem"],
            message=self._decode_log_message(row["message"]),
            created_at=_parse_iso(row["created_at"]),
            request_id=row["request_id"],
            source=row["source"],
        )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _encode_timing(timing: FlowTiming) -> str:
    return json.dumps(
        {
            "connect_ms": timing.connect_ms,
            "tls_ms": timing.tls_ms,
            "ttfb_ms": timing.ttfb_ms,
            "download_ms": timing.download_ms,
        },
        separators=(",", ":"),
    )


def _decode_timing(raw: str | None) -> FlowTiming:
    if not raw:
        return FlowTiming()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return FlowTiming()
    if not isinstance(data, dict):
        return FlowTiming()
    return FlowTiming(
        connect_ms=_opt_float(data.get("connect_ms")),
        tls_ms=_opt_float(data.get("tls_ms")),
        ttfb_ms=_opt_float(data.get("ttfb_ms")),
        download_ms=_opt_float(data.get("download_ms")),
    )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _flow_matches_text(flow: FlowRecord, needle: str) -> bool:
    haystacks: list[str] = [
        flow.method,
        flow.host,
        flow.path,
        flow.query or "",
        flow.content_type or "",
        flow.error or "",
        flow.protocol,
        flow.request_id or "",
    ]
    for key, value in flow.request_headers.items():
        haystacks.append(f"{key}: {value}")
    for key, value in flow.response_headers.items():
        haystacks.append(f"{key}: {value}")
    for blob in (flow.request_body, flow.response_body):
        try:
            haystacks.append(blob.decode("utf-8"))
        except UnicodeDecodeError:
            continue
    return any(needle in part.lower() for part in haystacks)
