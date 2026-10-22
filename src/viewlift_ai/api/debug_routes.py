"""Localhost debug page: Phantom-style tools that do not live in the iOS app."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.services.analytics_inspect import list_analytics_traffic
from viewlift_ai.services.debug_toolkit import (
    ANALYTICS_HOST_MARKERS,
    DIAGNOSTIC_QUERIES,
    apply_network_profile,
    list_modules,
    list_network_profiles,
    open_deeplink,
    replay_http,
    set_device_location,
)
from viewlift_ai.services.ott_inspect import enrich_frames, load_spm_packages

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _engine(request: Request):
    engine = getattr(request.app.state, "rule_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Rule engine unavailable")
    return engine


def _store(request: Request):
    return request.app.state.store


def _reveal(request: Request):
    session = getattr(request.app.state, "reveal", None)
    if session is None:
        raise HTTPException(status_code=503, detail="Reveal session unavailable")
    return session


def _redaction(request: Request):
    return request.app.state.redaction


def _sessions(request: Request):
    service = getattr(request.app.state, "debug_sessions", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Debug session service unavailable")
    return service


def _loopback(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Debug actions are loopback-only")


def _mutating(request: Request) -> bool:
    engine = getattr(request.app.state, "rule_engine", None)
    return bool(engine and engine.any_mutating_enabled())


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {}
    if not isinstance(body, dict):
        return {}
    return body


@router.get("/debug", response_class=HTMLResponse)
async def debug_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "debug_suite.html",
        {
            "title": "Debug",
            "active_nav": "debug",
            "mutating_enabled": _mutating(request),
            "diagnostics": DIAGNOSTIC_QUERIES,
            "analytics_markers": ANALYTICS_HOST_MARKERS,
        },
    )


@router.get("/api/debug/modules")
def debug_modules() -> dict[str, object]:
    return {"modules": list_modules()}


@router.get("/api/debug/analytics/traffic")
def debug_analytics_traffic(
    request: Request,
    limit: int = 300,
    user_beacon_url: str | None = None,
    player_beacon_url: str | None = None,
) -> dict[str, object]:
    """User/player beacons + VLAnalytics client network calls with request params."""
    _loopback(request)
    flows = _store(request).list_flows(limit=max(1, min(limit, 1000)))
    redaction = _redaction(request)

    def _redact_params(params: dict) -> dict:
        return redaction.redact_json_value(params)

    result = list_analytics_traffic(
        flows,
        user_beacon_url=user_beacon_url,
        player_beacon_url=player_beacon_url,
        redact_params=_redact_params,
    )
    # #region agent log
    try:
        import json
        import time
        from pathlib import Path

        Path("/Users/nexg/Documents/Viewlift_AI/.cursor/debug-93a126.log").open(
            "a", encoding="utf-8"
        ).write(
            json.dumps(
                {
                    "sessionId": "93a126",
                    "hypothesisId": "H1-analytics",
                    "location": "debug_routes.py:debug_analytics_traffic",
                    "message": "analytics traffic classified",
                    "data": {
                        "scanned": len(flows),
                        "total": result.get("total"),
                        "counts": result.get("counts"),
                    },
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    return result


@router.get("/api/debug/ott/spm")
def debug_ott_spm(request: Request, root: str | None = None) -> dict[str, object]:
    _loopback(request)
    result = load_spm_packages() if not root else load_spm_packages(
        __import__("pathlib").Path(root)
    )
    # #region agent log
    try:
        import json
        import time
        from pathlib import Path

        Path("/Users/nexg/Documents/Viewlift_AI/.cursor/debug-93a126.log").open(
            "a", encoding="utf-8"
        ).write(
            json.dumps(
                {
                    "sessionId": "93a126",
                    "hypothesisId": "H4",
                    "location": "debug_routes.py:debug_ott_spm",
                    "message": "spm packages loaded",
                    "data": {
                        "ok": result.get("ok"),
                        "count": result.get("count"),
                        "path": result.get("path"),
                    },
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    return result


@router.get("/api/debug/clients")
def debug_clients(request: Request) -> dict[str, object]:
    _loopback(request)
    return {"clients": _sessions(request).list_clients()}


@router.get("/api/debug/events")
def debug_events(
    request: Request,
    client_id: str,
    module: str | None = None,
    limit: int = 200,
) -> dict[str, object]:
    _loopback(request)
    try:
        rows = _sessions(request).list_events(
            client_id=client_id,
            module=module,
            limit=max(1, min(limit, 1000)),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"events": rows}


@router.post("/api/debug/confirm")
async def debug_confirm(request: Request) -> dict[str, object]:
    _loopback(request)
    body = await _json_body(request)
    try:
        return _sessions(request).mint_confirmation(
            str(body.get("client_id") or ""),
            str(body.get("command") or ""),
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/debug/command")
async def debug_command(request: Request) -> dict[str, object]:
    _loopback(request)
    body = await _json_body(request)
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    name = str(body.get("name") or "")
    # #region agent log
    try:
        import json
        import time
        from pathlib import Path

        Path("/Users/nexg/Documents/Viewlift_AI/.cursor/debug-93a126.log").open(
            "a", encoding="utf-8"
        ).write(
            json.dumps(
                {
                    "sessionId": "93a126",
                    "hypothesisId": "H2",
                    "location": "debug_routes.py:debug_command",
                    "message": "enqueue debug command",
                    "data": {"name": name, "has_client": bool(body.get("client_id"))},
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    try:
        command = _sessions(request).enqueue(
            client_id=str(body.get("client_id") or ""),
            name=name,
            payload=payload,
            confirmation=str(body.get("confirmation") or "") or None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (OverflowError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "command": command}


@router.post("/api/debug/ott/enrich-frames")
async def debug_enrich_frames(request: Request) -> dict[str, object]:
    """Parse hang/crash frames for class/file/line (Mac-side assist)."""
    _loopback(request)
    body = await _json_body(request)
    frames = body.get("frames") or []
    if not isinstance(frames, list):
        raise HTTPException(status_code=400, detail="frames must be an array")
    enriched = enrich_frames([item for item in frames if isinstance(item, dict)])
    # #region agent log
    try:
        import json
        import time
        from pathlib import Path

        with_line = sum(1 for item in enriched if item.get("line") is not None)
        Path("/Users/nexg/Documents/Viewlift_AI/.cursor/debug-93a126.log").open(
            "a", encoding="utf-8"
        ).write(
            json.dumps(
                {
                    "sessionId": "93a126",
                    "hypothesisId": "H1",
                    "location": "debug_routes.py:debug_enrich_frames",
                    "message": "frames enriched",
                    "data": {"input": len(frames), "with_line": with_line},
                    "timestamp": int(time.time() * 1000),
                }
            )
            + "\n"
        )
    except Exception:
        pass
    # #endregion
    return {"frames": enriched}


@router.get("/api/debug/network-profiles")
def debug_profiles() -> dict[str, object]:
    return {"profiles": list_network_profiles()}


@router.post("/api/debug/network-profile")
async def set_network_profile(request: Request) -> dict[str, object]:
    _loopback(request)
    body = await _json_body(request)
    try:
        return apply_network_profile(_engine(request), str(body.get("profile") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/debug/deeplink")
async def debug_deeplink(request: Request) -> dict[str, object]:
    _loopback(request)
    body = await _json_body(request)
    try:
        result = open_deeplink(str(body.get("url") or ""), udid=str(body.get("udid") or "booted"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("detail") or "open failed"))
    return result


@router.post("/api/debug/location")
async def debug_location(request: Request) -> dict[str, object]:
    _loopback(request)
    body = await _json_body(request)
    tail = getattr(request.app.state, "log_tail", None)
    streaming = bool(
        tail and tail.status().get("running") and tail.status().get("source") == "device"
    )
    clear = bool(body.get("clear"))
    lat = body.get("latitude")
    lon = body.get("longitude")
    try:
        return set_device_location(
            str(body.get("udid") or ""),
            float(lat) if lat is not None and not clear else None,
            float(lon) if lon is not None and not clear else None,
            clear=clear,
            streaming=streaming,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/flows/{flow_id}/replay")
def replay_flow(request: Request, flow_id: str) -> dict[str, object]:
    """Resend the raw captured request. Requires an unlocked reveal session."""
    _loopback(request)
    session = _reveal(request)
    if not session.unlocked:
        raise HTTPException(status_code=403, detail="Unlock reveal before replay")
    flow = _store(request).get_flow(flow_id)
    if flow is None:
        raise HTTPException(status_code=404, detail="Flow not found")
    raw = session.get_raw(flow_id)
    if raw is None:
        raise HTTPException(status_code=404, detail="No in-memory raw payload for this flow")
    url = _flow_url(flow)
    try:
        sent = replay_http(
            method=flow.method,
            url=url,
            headers=dict(raw.request_headers),
            body=bytes(raw.request_body),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    redaction = _redaction(request)
    body = redaction.redact_body(sent["body"])
    return {
        "ok": True,
        "status": sent["status"],
        "headers": redaction.redact_headers(sent["headers"]),
        "body": body.decode("utf-8", errors="replace")[:8000],
    }


def _flow_url(flow: Any) -> str:
    host = str(flow.host or "").strip()
    if not host:
        return ""
    path = flow.path or "/"
    if not str(path).startswith("/"):
        path = "/" + str(path)
    query = f"?{flow.query}" if flow.query else ""
    scheme = "http" if str(flow.protocol).upper() == "HTTP" else "https"
    return f"{scheme}://{host}{path}{query}"
