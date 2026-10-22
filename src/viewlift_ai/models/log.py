"""Device/simulator/SDK log models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    FAULT = "fault"


@dataclass(slots=True)
class LogRecord:
    id: str
    level: LogLevel
    subsystem: str
    message: str
    created_at: datetime
    request_id: str | None = None
    source: str = "unknown"

    @staticmethod
    def utc_now() -> datetime:
        return datetime.now(UTC)
