"""Thin mitmproxy adapters over the service-layer RuleEngine."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mitmproxy import http

from viewlift_ai.models.rules import BREAKPOINT_TIMED_OUT, BreakpointStage, RequestMatchContext
from viewlift_ai.proxy.breakpoints import (
    BreakpointRegistry,
    apply_decision_to_request,
    apply_decision_to_response,
)
from viewlift_ai.services.rule_engine import RuleEngine, throttle_delay_seconds

logger = logging.getLogger(__name__)


def request_context(flow: http.HTTPFlow) -> RequestMatchContext:
    host = ""
    try:
        host = flow.request.pretty_host or flow.request.host or ""
    except Exception:
        host = flow.request.host or ""
    path = flow.request.path or "/"
    if "?" in path:
        path = path.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return RequestMatchContext(
        method=(flow.request.method or "GET").upper(),
        host=host,
        path=path or "/",
    )


def _headers_collapsed(headers: Any) -> dict[str, str]:
    collapsed: dict[str, str] = {}
    for key, value in headers.items(multi=True):
        if key in collapsed:
            collapsed[key] = f"{collapsed[key]}, {value}"
        else:
            collapsed[key] = value
    return collapsed


async def apply_request_rules(
    engine: RuleEngine,
    flow: http.HTTPFlow,
    *,
    breakpoints: BreakpointRegistry | None = None,
) -> None:
    """Order before forward: throttle → map local → map remote → request breakpoint."""
    ctx = request_context(flow)

    throttle = engine.evaluate_throttle(ctx.host)
    if throttle.offline:
        flow.response = http.Response.make(
            503,
            b"Offline (throttle rule)",
            {"Content-Type": "text/plain; charset=utf-8"},
        )
        return

    if throttle.latency_ms > 0:
        await asyncio.sleep(throttle.latency_ms / 1000.0)

    upload_delay = throttle_delay_seconds(
        size_bytes=len(flow.request.raw_content or b""),
        kbps=throttle.upload_kbps,
    )
    if upload_delay > 0:
        await asyncio.sleep(upload_delay)

    mapped = engine.evaluate_map_local(ctx)
    if mapped is not None:
        ctype = mapped.content_type or "application/octet-stream"
        headers: dict[str, str] = {"Content-Type": ctype}
        flow.response = http.Response.make(mapped.status, mapped.body, headers)
        return

    remote = engine.evaluate_map_remote(ctx, original_url=flow.request.url)
    if remote is not None:
        try:
            flow.request.url = remote.url
        except Exception:
            logger.exception("Failed to apply map remote URL")

    if breakpoints is None or flow.response is not None:
        return

    rule = engine.evaluate_breakpoint(ctx, stage=BreakpointStage.REQUEST)
    if rule is None:
        return

    try:
        decision = await breakpoints.pause(
            flow_id=str(flow.id),
            rule_id=rule.id,
            stage=BreakpointStage.REQUEST,
            method=ctx.method,
            host=ctx.host,
            path=ctx.path,
            headers=_headers_collapsed(flow.request.headers),
            body=bytes(flow.request.raw_content or b""),
            status=None,
        )
    except asyncio.CancelledError:
        logger.info("Request breakpoint cancelled for flow %s", flow.id)
        raise

    apply_decision_to_request(flow, decision)


async def apply_response_rules(
    engine: RuleEngine,
    flow: http.HTTPFlow,
    *,
    breakpoints: BreakpointRegistry | None = None,
) -> None:
    """After upstream (or synthetic) response: download throttle → response breakpoint → rewrite."""
    if flow.response is None:
        return
    ctx = request_context(flow)

    throttle = engine.evaluate_throttle(ctx.host)
    download_delay = throttle_delay_seconds(
        size_bytes=len(flow.response.raw_content or b""),
        kbps=throttle.download_kbps,
    )
    if download_delay > 0:
        await asyncio.sleep(download_delay)

    if breakpoints is not None:
        rule = engine.evaluate_breakpoint(ctx, stage=BreakpointStage.RESPONSE)
        if rule is not None:
            try:
                decision = await breakpoints.pause(
                    flow_id=str(flow.id),
                    rule_id=rule.id,
                    stage=BreakpointStage.RESPONSE,
                    method=ctx.method,
                    host=ctx.host,
                    path=ctx.path,
                    headers=_headers_collapsed(flow.response.headers),
                    body=bytes(flow.response.raw_content or b""),
                    status=int(flow.response.status_code),
                )
            except asyncio.CancelledError:
                logger.info("Response breakpoint cancelled for flow %s", flow.id)
                raise
            apply_decision_to_response(flow, decision)
            if decision.action == "drop" or flow.response is None:
                return

    collapsed = _headers_collapsed(flow.response.headers)
    result = engine.evaluate_response_rewrite(
        ctx,
        status=int(flow.response.status_code),
        headers=collapsed,
        body=bytes(flow.response.raw_content or b""),
    )
    if result.error:
        logger.warning("Rewrite error (failed action left prior body): %s", result.error)
    flow.response.status_code = result.status
    flow.response.headers.clear()
    for key, value in result.headers.items():
        flow.response.headers[key] = value
    flow.response.content = result.body


def flow_error_with_breakpoint_mark(flow: http.HTTPFlow, existing: str | None) -> str | None:
    """Prefer exact timeout mark when a breakpoint timed out."""
    mark = flow.metadata.get("breakpoint_mark")
    if flow.metadata.get("breakpoint_timed_out") or mark == BREAKPOINT_TIMED_OUT:
        return BREAKPOINT_TIMED_OUT
    return existing
