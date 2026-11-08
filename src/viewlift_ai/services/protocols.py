"""Service contracts (SOLID ISP/DIP). Implementations must not import FastAPI."""

from __future__ import annotations

from collections.abc import Sequence
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
        kind: str | None = None,
        operation: str | None = None,
        client_ids: Sequence[str] | None = None,
    ) -> list[FlowRecord]: ...

    def delete_flows(self) -> int: ...

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
        client_ids: Sequence[str] | None = None,
    ) -> list[LogRecord]: ...

    def logs_for_request(
        self,
        request_id: str,
        *,
        limit: int = 100,
        client_id: str | None = None,
    ) -> list[LogRecord]: ...

    def delete_logs(self) -> int: ...

    def flow_id_for_request(
        self, request_id: str, *, client_id: str | None = None
    ) -> str | None: ...


@runtime_checkable
class ClientStoreProtocol(Protocol):
    """Persisted app-instance registry plus per-app data housekeeping."""

    def save_client(
        self,
        *,
        client_id: str,
        device_key: str,
        bundle_id: str,
        payload: dict[str, object],
        first_seen: float,
        last_seen: float,
        superseded_by: str | None = None,
    ) -> None: ...

    def list_clients(self) -> list[dict[str, object]]: ...

    def delete_clients(self, client_ids: Sequence[str]) -> int: ...

    def delete_client_data(self, client_id: str) -> int: ...

    def client_counts(self) -> dict[str, dict[str, int]]: ...


@runtime_checkable
class StoreProtocol(FlowStoreProtocol, LogStoreProtocol, Protocol):
    """Combined encrypted persistence for flows and logs."""

    def cleanup_older_than(self, max_age: timedelta, *, now: datetime | None = None) -> int: ...

    def close(self) -> None: ...
