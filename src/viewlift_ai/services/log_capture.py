"""Persist parsed device/simulator/SDK logs and publish to the log event bus."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from viewlift_ai.models.log import LogLevel, LogRecord
from viewlift_ai.services.event_bus import LogEventBus
from viewlift_ai.services.protocols import LogStoreProtocol


class LogCaptureService:
    """Store → event bus for log lines. No FastAPI imports."""

    def __init__(self, store: LogStoreProtocol, event_bus: LogEventBus) -> None:
        self._store = store
        self._bus = event_bus

    def persist(
        self,
        *,
        level: LogLevel | str,
        subsystem: str,
        message: str,
        source: str,
        request_id: str | None = None,
        created_at: datetime | None = None,
        log_id: str | None = None,
    ) -> LogRecord:
        resolved_level = level if isinstance(level, LogLevel) else _parse_level(level)
        record = LogRecord(
            id=log_id or str(uuid.uuid4()),
            level=resolved_level,
            subsystem=subsystem or "unknown",
            message=message,
            created_at=created_at or datetime.now(UTC),
            request_id=request_id,
            source=source,
        )
        self._store.save_log(record)
        self._bus.publish_threadsafe(record)
        return record


def _parse_level(raw: str) -> LogLevel:
    key = (raw or "info").strip().lower()
    aliases = {
        "debug": LogLevel.DEBUG,
        "info": LogLevel.INFO,
        "notice": LogLevel.INFO,
        "default": LogLevel.INFO,
        "warning": LogLevel.WARNING,
        "warn": LogLevel.WARNING,
        "error": LogLevel.ERROR,
        "err": LogLevel.ERROR,
        "fault": LogLevel.FAULT,
        "critical": LogLevel.FAULT,
    }
    return aliases.get(key, LogLevel.INFO)
