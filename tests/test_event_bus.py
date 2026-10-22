"""Async flow event bus for WebSocket fan-out."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from viewlift_ai.models.flow import FlowRecord, FlowSource
from viewlift_ai.services.event_bus import FlowEventBus


def _flow(flow_id: str = "f1") -> FlowRecord:
    return FlowRecord(
        id=flow_id,
        method="GET",
        host="h",
        path="/",
        status=200,
        duration_ms=1.0,
        size_bytes=0,
        protocol="HTTP/1.1",
        source=FlowSource.PROXY,
        created_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_subscribe_receives_published_flow() -> None:
    bus = FlowEventBus()
    bus.bind_loop(asyncio.get_running_loop())
    agen = bus.subscribe()
    waiter = asyncio.create_task(agen.__anext__())
    await asyncio.sleep(0)
    await bus.publish(_flow("f-live"))
    got = await asyncio.wait_for(waiter, timeout=1.0)
    assert got.id == "f-live"
    await agen.aclose()
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_threadsafe_from_other_thread() -> None:
    bus = FlowEventBus()
    loop = asyncio.get_running_loop()
    bus.bind_loop(loop)

    async def collect_one() -> FlowRecord:
        async for item in bus.subscribe():
            return item
        raise AssertionError("subscribe ended")

    task = asyncio.create_task(collect_one())
    await asyncio.sleep(0)

    def _emit() -> None:
        bus.publish_threadsafe(_flow("from-thread"))

    await asyncio.to_thread(_emit)
    got = await asyncio.wait_for(task, timeout=1.0)
    assert got.id == "from-thread"


@pytest.mark.asyncio
async def test_full_queue_drops_oldest() -> None:
    bus = FlowEventBus(maxsize=1)
    bus.bind_loop(asyncio.get_running_loop())
    queue_holder: list[asyncio.Queue[FlowRecord]] = []

    async def hold() -> None:
        async for _item in bus.subscribe():
            # never consume after subscribe registers
            await asyncio.sleep(3600)

    # Manually register a full queue via publish after filling
    q: asyncio.Queue[FlowRecord] = asyncio.Queue(maxsize=1)
    q.put_nowait(_flow("old"))
    bus._subscribers.append(q)  # noqa: SLF001 — intentional capacity test
    queue_holder.append(q)
    await bus.publish(_flow("new"))
    assert q.qsize() == 1
    assert q.get_nowait().id == "new"
