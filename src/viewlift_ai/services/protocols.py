"""Service contracts (SOLID ISP/DIP). Implementations must not import FastAPI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable

from viewlift_ai.models.flow import FlowRecord
from viewlift_ai.models.log import LogRecord


@runtime_checkable
class FlowStoreProtocol(Protocol):
    def save_flow(self, flow: FlowRecord) -> None: ...

    def get_flow(self, flow_id: str) -> FlowRecord | None: ...

    def list_flows(
        self,
        *,
        limit: int = 200,
        method: str | None = None,
        host: str | None = None,
        status: int | None = None,
        content_type: str | None = None,
        text: str | None = None,
    ) -> list[FlowRecord]: ...

    def wipe_session(self) -> int: ...


@runtime_checkable
class LogStoreProtocol(Protocol):
    def save_log(self, log: LogRecord) -> None: ...

    def get_log(self, log_id: str) -> LogRecord | None: ...

    def list_logs(
        self,
        *,
        limit: int = 200,
        source: str | None = None,
        level: str | None = None,
        subsystem: str | None = None,
        text: str | None = None,
    ) -> list[LogRecord]: ...

    def logs_for_request(self, request_id: str, *, limit: int = 100) -> list[LogRecord]: ...

    def flow_id_for_request(self, request_id: str) -> str | None: ...


@runtime_checkable
class StoreProtocol(FlowStoreProtocol, LogStoreProtocol, Protocol):
    """Combined encrypted persistence for flows and logs."""

    def cleanup_older_than(self, max_age: timedelta, *, now: datetime | None = None) -> int: ...

    def close(self) -> None: ...
