"""WebSocket live flow, log, and breakpoint updates via event buses."""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections.abc import Awaitable, Callable

import anyio
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from viewlift_ai import defaults
from viewlift_ai.api.scope import app_tag
from viewlift_ai.models.breakpoints import pending_to_dict
from viewlift_ai.services.flow_present import flow_summary
from viewlift_ai.services.log_present import log_summary

logger = logging.getLogger(__name__)

ws_router = APIRouter()


class _Outlet:
    """Serialized sends to one socket, with the time of the last frame for heartbeats."""

    def __init__(self, websocket: WebSocket) -> None:
        self._websocket = websocket
        self._lock = asyncio.Lock()
        self.last_sent = time.monotonic()

    async def send(self, payload: dict[str, object]) -> None:
        async with self._lock:
            await self._websocket.send_json(payload)
            self.last_sent = time.monotonic()


Pump = Callable[[_Outlet], Awaitable[None]]


async def _serve(websocket: WebSocket, name: str, *pumps: Pump) -> None:
    """Run every pump until the client leaves.

    A heartbeat goes out when the socket is idle, so the browser can tell a quiet feed from a
    dead connection. A reader task notices a closed tab at once instead of on the next event.
    """
    outlet = _Outlet(websocket)
    async with anyio.create_task_group() as group:

        async def run(work: Callable[[], Awaitable[None]]) -> None:
            try:
                await work()
            except (WebSocketDisconnect, OSError):
                pass
            except Exception:
                logger.exception("WebSocket %s stream failed", name)
            finally:
                group.cancel_scope.cancel()

        for pump in pumps:
            group.start_soon(run, functools.partial(pump, outlet))
        group.start_soon(run, functools.partial(_heartbeat, outlet))
        group.start_soon(run, functools.partial(_drain_client, websocket))
    try:
        await websocket.close()
    except Exception:
        pass


async def _heartbeat(outlet: _Outlet) -> None:
    while True:
        interval = defaults.LIVE_HEARTBEAT_SECONDS
        idle = time.monotonic() - outlet.last_sent
        if idle >= interval:
            await outlet.send({"type": "heartbeat", "interval": interval, "at": time.time()})
            idle = 0.0
        await asyncio.sleep(max(0.5, interval - idle))


async def _drain_client(websocket: WebSocket) -> None:
    """Return when the client disconnects. Client frames carry no commands."""
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return


@ws_router.websocket("/ws/flows")
async def flows_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    scope = _scope(websocket)
    bus = websocket.app.state.event_bus
    breakpoints = getattr(websocket.app.state, "breakpoints", None)

    async def flows(outlet: _Outlet) -> None:
        async for flow in bus.subscribe():
            if scope is not None and flow.client_id not in scope:
                continue
            await outlet.send(
                {
                    "type": "flow",
                    "flow": flow_summary(flow),
                    "app": app_tag(websocket.app, flow.client_id),
                }
            )

    async def paused(outlet: _Outlet) -> None:
        async for event in breakpoints.subscribe():
            await outlet.send(
                {
                    "type": "breakpoint",
                    "kind": str(event.kind),
                    "pending": pending_to_dict(event.pending) if event.pending else None,
                }
            )

    pumps: list[Pump] = [flows]
    if breakpoints is not None:
        pumps.append(paused)
    await _serve(websocket, "flow", *pumps)


@ws_router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = getattr(websocket.app.state, "log_event_bus", None)
    if bus is None:
        await websocket.close(code=1011)
        return
    scope = _scope(websocket)

    async def logs(outlet: _Outlet) -> None:
        async for log in bus.subscribe():
            if scope is not None and log.client_id not in scope:
                continue
            await outlet.send(
                {
                    "type": "log",
                    "log": log_summary(log),
                    "app": app_tag(websocket.app, log.client_id),
                }
            )

    await _serve(websocket, "log", logs)


@ws_router.websocket("/ws/debug")
async def debug_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = getattr(websocket.app.state, "debug_event_bus", None)
    if bus is None:
        await websocket.close(code=1011)
        return
    scope = _scope(websocket)

    async def events(outlet: _Outlet) -> None:
        async for event in bus.subscribe():
            if scope is not None and not _debug_event_in_scope(event, scope):
                continue
            await outlet.send(event)

    await _serve(websocket, "debug", events)


def _scope(websocket: WebSocket) -> set[str] | None:
    """Optional `?client_id=` filter (repeatable). Absent means every app."""
    ids = [item for item in websocket.query_params.getlist("client_id") if item]
    return set(ids) if ids else None


def _debug_event_in_scope(event: dict, scope: set[str]) -> bool:
    if event.get("type") == "apps-changed":
        return True
    # The shell on every page watches `__apps__` and only wants problems, not samples.
    if "__apps__" in scope:
        return _is_page_alert(event)
    nested = event.get("event") if isinstance(event.get("event"), dict) else {}
    client = event.get("client") if isinstance(event.get("client"), dict) else {}
    client_id = event.get("client_id") or nested.get("client_id") or client.get("id")
    return client_id in scope


def _is_page_alert(event: dict) -> bool:
    """Crashes, hangs, leaks, and playback failures. Samples stay on the app socket."""
    if event.get("type") == "debug-crash":
        return True
    if event.get("type") != "debug-event":
        return False
    nested = event.get("event") if isinstance(event.get("event"), dict) else {}
    module = nested.get("module")
    kind = nested.get("kind")
    if (module, kind) in {("hang", "detected"), ("memory", "leak")}:
        return True
    if (module, kind) != ("playback", "event"):
        return False
    payload = nested.get("payload") if isinstance(nested.get("payload"), dict) else {}
    play = payload.get("kind")
    if play in {"item_failed", "item_error_log"}:
        return True
    return play == "drm" and payload.get("phase") == "failed"


@ws_router.get("/api/ws/ping")
async def ws_ping(request: Request) -> dict[str, object]:
    """Tiny readiness helper for tests (not a browser asset)."""
    bus = request.app.state.event_bus
    log_bus = getattr(request.app.state, "log_event_bus", None)
    return {
        "ok": True,
        "subscribers": bus.subscriber_count,
        "log_subscribers": getattr(log_bus, "subscriber_count", 0),
        "debug_subscribers": getattr(
            getattr(request.app.state, "debug_event_bus", None),
            "subscriber_count",
            0,
        ),
    }
