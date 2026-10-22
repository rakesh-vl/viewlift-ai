"""WebSocket live flow, log, and breakpoint updates via event buses."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect

from viewlift_ai.models.breakpoints import pending_to_dict
from viewlift_ai.services.flow_present import flow_summary
from viewlift_ai.services.log_present import log_summary

logger = logging.getLogger(__name__)

ws_router = APIRouter()


@ws_router.websocket("/ws/flows")
async def flows_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = websocket.app.state.event_bus
    breakpoints = getattr(websocket.app.state, "breakpoints", None)
    flow_task = asyncio.create_task(_pump_flows(websocket, bus))
    bp_task: asyncio.Task | None = None
    if breakpoints is not None:
        bp_task = asyncio.create_task(_pump_breakpoints(websocket, breakpoints))
    try:
        tasks = {flow_task}
        if bp_task is not None:
            tasks.add(bp_task)
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("WebSocket flow stream failed")
        try:
            await websocket.close()
        except Exception:
            return
    finally:
        flow_task.cancel()
        if bp_task is not None:
            bp_task.cancel()
            await asyncio.gather(flow_task, bp_task, return_exceptions=True)
        else:
            await asyncio.gather(flow_task, return_exceptions=True)


@ws_router.websocket("/ws/logs")
async def logs_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = getattr(websocket.app.state, "log_event_bus", None)
    if bus is None:
        await websocket.close(code=1011)
        return
    try:
        async for log in bus.subscribe():
            await websocket.send_json({"type": "log", "log": log_summary(log)})
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("WebSocket log stream failed")
        try:
            await websocket.close()
        except Exception:
            return


@ws_router.websocket("/ws/debug")
async def debug_ws(websocket: WebSocket) -> None:
    await websocket.accept()
    bus = getattr(websocket.app.state, "debug_event_bus", None)
    if bus is None:
        await websocket.close(code=1011)
        return
    try:
        async for event in bus.subscribe():
            await websocket.send_json(event)
    except WebSocketDisconnect:
        return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("WebSocket debug stream failed")
        try:
            await websocket.close()
        except Exception:
            return


async def _pump_flows(websocket: WebSocket, bus) -> None:
    async for flow in bus.subscribe():
        await websocket.send_json(
            {
                "type": "flow",
                "flow": flow_summary(flow),
            }
        )


async def _pump_breakpoints(websocket: WebSocket, registry) -> None:
    async for event in registry.subscribe():
        payload = {
            "type": "breakpoint",
            "kind": str(event.kind),
            "pending": pending_to_dict(event.pending) if event.pending else None,
        }
        await websocket.send_json(payload)


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
