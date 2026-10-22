"""Async breakpoint pause registry. Does not block the event loop with sleep locks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from viewlift_ai.models.breakpoints import (
    BreakpointDecision,
    BreakpointEvent,
    BreakpointEventKind,
    PendingBreakpoint,
    pending_to_dict,
)
from viewlift_ai.models.rules import BREAKPOINT_TIMED_OUT, BreakpointStage
from viewlift_ai.services.rule_engine import (
    MAX_RESULT_BODY_BYTES,
    MAX_STATUS,
    MIN_STATUS,
    RuleValidationError,
)

logger = logging.getLogger(__name__)

DEFAULT_BREAKPOINT_TIMEOUT_S = 120.0
MAX_HEADER_NAME_LEN = 256
MAX_HEADER_VALUE_LEN = 8_192
MAX_HEADER_COUNT = 100


@dataclass
class _PendingEntry:
    item: PendingBreakpoint
    future: asyncio.Future[BreakpointDecision]
    original_headers: dict[str, str]
    original_body: bytes
    original_status: int | None


class BreakpointRegistry:
    """Holds paused flows; resume/drop/timeout resolve an asyncio.Future."""

    def __init__(
        self,
        *,
        timeout_s: float = DEFAULT_BREAKPOINT_TIMEOUT_S,
        maxsize: int = 256,
    ) -> None:
        self._timeout_s = float(timeout_s)
        self._maxsize = maxsize
        self._lock = threading.RLock()
        self._pending: dict[str, _PendingEntry] = {}
        self._subscribers: list[asyncio.Queue[BreakpointEvent]] = []
        self._async_lock: asyncio.Lock | None = None
        self._consumer_loop: asyncio.AbstractEventLoop | None = None
        self._closed = False
        self._listeners: list[Callable[[BreakpointEvent], None]] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind UI/API consumer loop for event fan-out (typically uvicorn)."""
        self._consumer_loop = loop

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def closed(self) -> bool:
        return self._closed

    def subscribe_sync(self, listener: Callable[[BreakpointEvent], None]) -> None:
        self._listeners.append(listener)

    def list_pending(self) -> list[PendingBreakpoint]:
        with self._lock:
            return [
                PendingBreakpoint(
                    id=e.item.id,
                    flow_id=e.item.flow_id,
                    rule_id=e.item.rule_id,
                    stage=e.item.stage,
                    method=e.item.method,
                    host=e.item.host,
                    path=e.item.path,
                    headers=dict(e.item.headers),
                    body=bytes(e.item.body),
                    status=e.item.status,
                    created_at=e.item.created_at,
                    timeout_at=e.item.timeout_at,
                    timed_out=e.item.timed_out,
                )
                for e in self._pending.values()
            ]

    def pending_dicts(self) -> list[dict[str, Any]]:
        return [pending_to_dict(p) for p in self.list_pending()]

    async def subscribe(self) -> AsyncIterator[BreakpointEvent]:
        queue: asyncio.Queue[BreakpointEvent] = asyncio.Queue(maxsize=self._maxsize)
        lock = self._ensure_async_lock()
        async with lock:
            self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    async def pause(
        self,
        *,
        flow_id: str,
        rule_id: str,
        stage: BreakpointStage,
        method: str,
        host: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
        status: int | None = None,
        timeout_s: float | None = None,
    ) -> BreakpointDecision:
        """Await operator decision without busy-waiting the event loop."""
        if self._closed:
            return BreakpointDecision(action="resume")

        loop = asyncio.get_running_loop()
        pause_id = str(uuid.uuid4())
        now = time.time()
        timeout = self._timeout_s if timeout_s is None else float(timeout_s)
        item = PendingBreakpoint(
            id=pause_id,
            flow_id=flow_id,
            rule_id=rule_id,
            stage=stage,
            method=method,
            host=host,
            path=path,
            headers=dict(headers),
            body=bytes(body),
            status=status,
            created_at=now,
            timeout_at=now + timeout,
        )
        future: asyncio.Future[BreakpointDecision] = loop.create_future()
        entry = _PendingEntry(
            item=item,
            future=future,
            original_headers=dict(headers),
            original_body=bytes(body),
            original_status=status,
        )
        with self._lock:
            self._pending[pause_id] = entry

        self._emit(BreakpointEvent(kind=BreakpointEventKind.PAUSED, pending=item))

        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=timeout)
        except TimeoutError:
            timed = PendingBreakpoint(
                id=item.id,
                flow_id=item.flow_id,
                rule_id=item.rule_id,
                stage=item.stage,
                method=item.method,
                host=item.host,
                path=item.path,
                headers=dict(entry.original_headers),
                body=bytes(entry.original_body),
                status=entry.original_status,
                created_at=item.created_at,
                timeout_at=item.timeout_at,
                timed_out=True,
            )
            with self._lock:
                current = self._pending.pop(pause_id, None)
                if current is not None and not current.future.done():
                    self._resolve_future(
                        current.future,
                        BreakpointDecision(action="timeout"),
                    )
            self._emit(
                BreakpointEvent(kind=BreakpointEventKind.TIMED_OUT, pending=timed)
            )
            return BreakpointDecision(action="timeout")
        except asyncio.CancelledError:
            self._cancel_entry(pause_id)
            raise
        finally:
            with self._lock:
                self._pending.pop(pause_id, None)

    def resume(self, pause_id: str, payload: dict[str, Any] | None = None) -> PendingBreakpoint:
        with self._lock:
            entry = self._pending.get(pause_id)
            if entry is None:
                raise RuleValidationError(f"breakpoint not found: {pause_id}")
            if entry.future.done():
                raise RuleValidationError(f"breakpoint already resolved: {pause_id}")
            decision = self._validate_resume(entry, payload or {})
            updated = PendingBreakpoint(
                id=entry.item.id,
                flow_id=entry.item.flow_id,
                rule_id=entry.item.rule_id,
                stage=entry.item.stage,
                method=entry.item.method,
                host=entry.item.host,
                path=entry.item.path,
                headers=dict(decision.headers or entry.item.headers),
                body=bytes(decision.body if decision.body is not None else entry.item.body),
                status=decision.status if decision.status is not None else entry.item.status,
                created_at=entry.item.created_at,
                timeout_at=entry.item.timeout_at,
                timed_out=False,
            )
            entry.item = updated
            future = entry.future
            out = updated
        self._resolve_future(future, decision)
        self._emit(BreakpointEvent(kind=BreakpointEventKind.RESUMED, pending=out))
        return out

    def drop(self, pause_id: str) -> PendingBreakpoint:
        with self._lock:
            entry = self._pending.get(pause_id)
            if entry is None:
                raise RuleValidationError(f"breakpoint not found: {pause_id}")
            if entry.future.done():
                raise RuleValidationError(f"breakpoint already resolved: {pause_id}")
            item = entry.item
            future = entry.future
        self._resolve_future(future, BreakpointDecision(action="drop"))
        self._emit(BreakpointEvent(kind=BreakpointEventKind.DROPPED, pending=item))
        return item

    async def shutdown(self) -> None:
        """Resolve all pending pauses with original traffic (safe process exit)."""
        self._closed = True
        with self._lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for entry in entries:
            if not entry.future.done():
                self._resolve_future(entry.future, BreakpointDecision(action="resume"))
            self._emit(
                BreakpointEvent(kind=BreakpointEventKind.CLEARED, pending=entry.item)
            )

    def _resolve_future(
        self,
        future: asyncio.Future[BreakpointDecision],
        decision: BreakpointDecision,
    ) -> None:
        if future.done():
            return

        def _set() -> None:
            if not future.done():
                future.set_result(decision)

        try:
            loop = future.get_loop()
        except Exception:
            _set()
            return
        if loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            _set()
            return
        try:
            loop.call_soon_threadsafe(_set)
        except RuntimeError:
            return

    def _cancel_entry(self, pause_id: str) -> None:
        with self._lock:
            entry = self._pending.pop(pause_id, None)
        if entry is None:
            return
        if not entry.future.done():
            fut = entry.future

            def _cancel() -> None:
                if not fut.done():
                    fut.cancel()

            try:
                loop = fut.get_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(_cancel)
                else:
                    _cancel()
            except Exception:
                pass
        self._emit(
            BreakpointEvent(kind=BreakpointEventKind.CLEARED, pending=entry.item)
        )

    def _validate_resume(
        self,
        entry: _PendingEntry,
        payload: dict[str, Any],
    ) -> BreakpointDecision:
        stage = entry.item.stage
        headers = entry.original_headers
        body = entry.original_body
        status = entry.original_status

        if "headers" in payload and payload["headers"] is not None:
            headers = validate_headers(payload["headers"])
        if "body" in payload and payload["body"] is not None:
            body = validate_body(payload["body"])
        if "status" in payload and payload["status"] is not None:
            if stage != BreakpointStage.RESPONSE:
                raise RuleValidationError("status edits allowed only on response stage")
            status = validate_status(payload["status"])

        return BreakpointDecision(
            action="resume",
            headers=headers,
            body=body,
            status=status,
        )

    def _ensure_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def _emit(self, event: BreakpointEvent) -> None:
        for listener in list(self._listeners):
            try:
                listener(event)
            except Exception:
                logger.exception("Breakpoint listener failed")
        loop = self._consumer_loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._enqueue_all(event)
            return
        try:
            loop.call_soon_threadsafe(self._enqueue_all, event)
        except RuntimeError:
            return

    def _enqueue_all(self, event: BreakpointEvent) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass


def validate_headers(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise RuleValidationError("headers must be an object")
    if len(raw) > MAX_HEADER_COUNT:
        raise RuleValidationError(f"headers exceed count cap ({MAX_HEADER_COUNT})")
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = str(key)
        if not name or len(name) > MAX_HEADER_NAME_LEN:
            raise RuleValidationError("header name invalid or too long")
        text = "" if value is None else str(value)
        if len(text) > MAX_HEADER_VALUE_LEN:
            raise RuleValidationError("header value exceeds size cap")
        if "\r" in name or "\n" in name or "\r" in text or "\n" in text:
            raise RuleValidationError("header CR/LF not allowed")
        out[name] = text
    return out


def validate_body(raw: Any) -> bytes:
    if isinstance(raw, bytes):
        data = raw
    elif isinstance(raw, str):
        data = raw.encode("utf-8")
    else:
        raise RuleValidationError("body must be a string")
    if len(data) > MAX_RESULT_BODY_BYTES:
        raise RuleValidationError("body exceeds size cap")
    return data


def validate_status(raw: Any) -> int:
    code = int(raw)
    if code < MIN_STATUS or code > MAX_STATUS:
        raise RuleValidationError("status out of range")
    return code


def apply_decision_to_request(flow: Any, decision: BreakpointDecision) -> None:
    """Apply resume edits to mitmproxy request; drop kills the flow."""
    if decision.action == "drop":
        flow.kill()
        return
    if decision.action == "timeout":
        flow.metadata["breakpoint_timed_out"] = True
        flow.metadata["breakpoint_mark"] = BREAKPOINT_TIMED_OUT
        return
    if decision.headers is not None:
        flow.request.headers.clear()
        for key, value in decision.headers.items():
            flow.request.headers[key] = value
    if decision.body is not None:
        flow.request.content = decision.body


def apply_decision_to_response(flow: Any, decision: BreakpointDecision) -> None:
    if decision.action == "drop":
        flow.kill()
        return
    if flow.response is None:
        return
    if decision.action == "timeout":
        flow.metadata["breakpoint_timed_out"] = True
        flow.metadata["breakpoint_mark"] = BREAKPOINT_TIMED_OUT
        return
    if decision.status is not None:
        flow.response.status_code = int(decision.status)
    if decision.headers is not None:
        flow.response.headers.clear()
        for key, value in decision.headers.items():
            flow.response.headers[key] = value
    if decision.body is not None:
        flow.response.content = decision.body
