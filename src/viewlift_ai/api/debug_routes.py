"""Localhost debug page: Phantom-style tools that do not live in the iOS app."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.api.scope import resolve_client_ids
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
from viewlift_ai.services.ott_inspect import enrich_frames
from viewlift_ai.services.package_detect import AppIdentity, detect_packages

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()

# SwiftPM identities this repository's capture SDK can be resolved under.
CAPTURE_SDK_PACKAGES = frozenset({"viewlift-ai", "viewliftnetworkcapture"})


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
    client_id: Annotated[list[str] | None, Query()] = None,
    device_key: str | None = None,
) -> dict[str, object]:
    """User/player beacons + VLAnalytics client network calls with request params."""
    _loopback(request)
    flows = _store(request).list_flows(
        limit=max(1, min(limit, 1000)),
        client_ids=resolve_client_ids(request, client_id, device_key),
    )
    redaction = _redaction(request)

    def _redact_params(params: dict) -> dict:
        return redaction.redact_json_value(params)

    return list_analytics_traffic(
        flows,
        user_beacon_url=user_beacon_url,
        player_beacon_url=player_beacon_url,
        redact_params=_redact_params,
    )


def _selected_client(request: Request, client_id: str | None) -> dict[str, object] | None:
    """
    Explicit client, else the only online app. Several online apps without a selection
    is a 409 so the dashboard never silently mixes two apps on one device.
    """
    sessions = _sessions(request)
    target = sessions.resolve_client_id(client_id)
    if not target:
        if sessions.online_count() > 1:
            raise HTTPException(
                status_code=409,
                detail="client_required: several apps are connected; choose one",
            )
        return None
    try:
        return sessions.get_client(target)
    except LookupError:
        return None


@router.get("/api/debug/app")
def debug_app(request: Request, client_id: str | None = None) -> dict[str, object]:
    """Identity of the selected app (explicit client, else the only connected one)."""
    _loopback(request)
    client = _selected_client(request, client_id)
    return {"paired": client is not None, "client": client}


@router.get("/api/debug/packages")
def debug_packages(request: Request, client_id: str | None = None) -> dict[str, object]:
    """Swift packages of the paired app, found from its Xcode build. No configured paths."""
    _loopback(request)
    client = _selected_client(request, client_id)
    identity = AppIdentity(
        bundle_id=str((client or {}).get("bundle_id") or ""),
        executable=str((client or {}).get("executable") or ""),
        app_name=str((client or {}).get("app_name") or ""),
    )
    result = detect_packages(identity)
    keys = ("id", "app_name", "bundle_id", "platform", "app_version", "build", "sdk_version")
    result["client"] = {key: client.get(key) for key in keys} if client else None
    result["runtime_frameworks"] = list((client or {}).get("frameworks") or [])
    packages = result.get("packages") or []
    result["capture_sdk"] = next(
        (pkg for pkg in packages if pkg.get("identity") in CAPTURE_SDK_PACKAGES),
        None,
    )
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


@router.post("/api/debug/invoke")
async def debug_invoke(request: Request) -> dict[str, object]:
    """Enqueue a command and wait for that app's command-result. Loopback only."""
    _loopback(request)
    body = await _json_body(request)
    sessions = _sessions(request)
    client_id = str(body.get("client_id") or "").strip()
    if not client_id or not sessions.is_online(client_id):
        raise HTTPException(
            status_code=409,
            detail={
                "message": "client_id is required and must name an online app",
                "apps": sessions.list_clients(),
            },
        )
    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")
    timeout = body.get("timeout")
    try:
        return await asyncio.to_thread(
            lambda: sessions.invoke(
                client_id=client_id,
                name=str(body.get("name") or ""),
                payload=payload,
                mutate=body.get("mutate") is True,
                timeout=None if timeout is None else float(timeout),
            )
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=409,
            detail={"message": str(exc), "apps": sessions.list_clients()},
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except (OverflowError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/debug/ott/enrich-frames")
async def debug_enrich_frames(request: Request) -> dict[str, object]:
    """Parse hang/crash frames for class/file/line (Mac-side assist)."""
    _loopback(request)
    body = await _json_body(request)
    frames = body.get("frames") or []
    if not isinstance(frames, list):
        raise HTTPException(status_code=400, detail="frames must be an array")
    enriched = enrich_frames([item for item in frames if isinstance(item, dict)])
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
    udid = str(body.get("udid") or "") or _simulator_udid(request, body.get("client_id"))
    try:
        result = open_deeplink(str(body.get("url") or ""), udid=udid or "booted")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=str(result.get("detail") or "open failed"))
    return result


def _simulator_udid(request: Request, client_id: object) -> str:
    """Target the simulator the selected app runs on instead of whichever is booted."""
    if not client_id:
        return ""
    try:
        client = _sessions(request).get_client(str(client_id))
    except LookupError:
        return ""
    if client.get("is_simulator"):
        return str(client.get("device_key") or "")
    return ""


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
            str(body.get("udid") or "") or _simulator_udid(request, body.get("client_id")),
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
