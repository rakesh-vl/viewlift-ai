"""Async pub/sub for live flow/log updates (FastAPI WebSocket consumers later)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TypeVar

from viewlift_ai.models.flow import FlowRecord
from viewlift_ai.models.log import LogRecord

T = TypeVar("T")


class EventBus[T]:
    """Thread-aware async bus. No FastAPI imports — WS layer only subscribes."""

    def __init__(self, *, maxsize: int = 256) -> None:
        self._maxsize = maxsize
        self._subscribers: list[asyncio.Queue[T]] = []
        self._lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the consumer event loop (typically uvicorn's)."""
        self._loop = loop

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, item: T) -> None:
        async with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            self._enqueue(queue, item)

    def publish_threadsafe(self, item: T) -> None:
        """Publish from a worker thread onto the bound asyncio loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._enqueue_all_nowait(item)
            return
        asyncio.run_coroutine_threadsafe(self.publish(item), loop)

    async def subscribe(self) -> AsyncIterator[T]:
        queue: asyncio.Queue[T] = asyncio.Queue(maxsize=self._maxsize)
        async with self._lock:
            self._subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            async with self._lock:
                if queue in self._subscribers:
                    self._subscribers.remove(queue)

    def _enqueue_all_nowait(self, item: T) -> None:
        for queue in list(self._subscribers):
            self._enqueue(queue, item)

    @staticmethod
    def _enqueue(queue: asyncio.Queue[T], item: T) -> None:
        try:
            queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                pass


class FlowEventBus(EventBus[FlowRecord]):
    """Flow-typed bus (keeps existing import sites stable)."""


class LogEventBus(EventBus[LogRecord]):
    """Log-typed bus for live Logs WebSocket tails."""
