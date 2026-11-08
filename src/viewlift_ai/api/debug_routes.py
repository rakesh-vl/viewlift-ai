"""Localhost debug page: Phantom-style tools that do not live in the iOS app."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
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
    validate_deeplink,
)
from viewlift_ai.services.host.crash_brief import brief_markdown
from viewlift_ai.services.log_tail import AppAttribution
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


@router.get("/api/debug/crashes")
def debug_crashes(request: Request, client_id: str, limit: int = 20) -> dict[str, object]:
    """Stored crashes for one app, newest first: signals, exceptions, and macOS reports."""
    _loopback(request)
    try:
        rows = _sessions(request).list_events(
            client_id=client_id,
            module="crash",
            limit=max(1, min(limit, 100)),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"events": rows}


def _crash(request: Request, client_id: str, crash_id: str) -> dict[str, Any]:
    try:
        payload = _sessions(request).crash(client_id, crash_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload is None:
        raise HTTPException(status_code=404, detail="crash not found")
    return payload


def _media_file(request: Request, client_id: str, crash_id: str, *, poster: bool) -> Path:
    _crash(request, client_id, crash_id)
    media = getattr(_sessions(request), "media", None)
    if media is None:
        raise HTTPException(status_code=404, detail="crash media is off")
    try:
        path = media.poster_path(crash_id) if poster else media.video_path(crash_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="replay not uploaded yet")
    return path


@router.get("/api/debug/crashes/{crash_id}/replay.mp4")
def debug_crash_replay(
    request: Request, crash_id: str, client_id: str, download: bool = False
) -> FileResponse:
    """The last seconds before the crash. Range requests work, so the player can seek."""
    _loopback(request)
    path = _media_file(request, client_id, crash_id, poster=False)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{crash_id}.mp4",
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/debug/crashes/{crash_id}/poster.jpg")
def debug_crash_poster(request: Request, crash_id: str, client_id: str) -> FileResponse:
    _loopback(request)
    path = _media_file(request, client_id, crash_id, poster=True)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/debug/crashes/{crash_id}/detail")
def debug_crash_detail(request: Request, crash_id: str, client_id: str) -> dict[str, object]:
    """One crash with its diagnosis and markdown brief, for the MCP `crash_detail` tool."""
    _loopback(request)
    payload = _crash(request, client_id, crash_id)
    client = next(
        (item for item in _sessions(request).list_clients() if item.get("id") == client_id), None
    )
    base = f"{request.base_url}api/debug/crashes/{crash_id}"
    replay = payload.get("replay") if isinstance(payload.get("replay"), dict) else {}
    ready = replay.get("status") == "ready"
    poster = None
    media = getattr(_sessions(request), "media", None)
    if media is not None and media.poster_path(crash_id).is_file():
        poster = base64.b64encode(media.poster_path(crash_id).read_bytes()).decode("ascii")
    return {
        "id": crash_id,
        "crash": payload,
        "brief": brief_markdown(
            crash_id,
            payload,
            client=client,
            replay_url=f"{base}/replay.mp4?client_id={client_id}" if ready else None,
        ),
        "replay_url": f"{base}/replay.mp4?client_id={client_id}" if ready else None,
        "report_url": f"{base}/report.md?client_id={client_id}",
        "poster": {"mime": "image/jpeg", "base64": poster} if poster else None,
    }


@router.get("/api/debug/crashes/{crash_id}/report.md")
def debug_crash_report(
    request: Request, crash_id: str, client_id: str, download: bool = False
) -> PlainTextResponse:
    """Markdown brief for a person or an AI agent: cause, code, stack, steps, timeline."""
    _loopback(request)
    payload = _crash(request, client_id, crash_id)
    client = next(
        (item for item in _sessions(request).list_clients() if item.get("id") == client_id), None
    )
    replay = f"{request.base_url}api/debug/crashes/{crash_id}/replay.mp4?client_id={client_id}"
    text = brief_markdown(crash_id, payload, client=client, replay_url=replay)
    headers = {"Cache-Control": "no-store"}
    if download:
        headers["Content-Disposition"] = f'attachment; filename="{crash_id}.md"'
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8", headers=headers)


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


def _client(request: Request, client_id: object) -> dict[str, object] | None:
    if not client_id:
        return None
    try:
        return _sessions(request).get_client(str(client_id))
    except LookupError:
        return None


def _host_call(request: Request, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    from viewlift_ai.api.host_routes import toolkit

    return toolkit(request).call(tool, args, confirm=True)


def _log_device_udid(request: Request, client: dict[str, object]) -> str | None:
    """Pick the paired device for a device app's log stream. None lets the collector choose.

    The SDK only knows `vendor:<idfv>`, so with several paired devices the app's hardware
    model narrows the list. Several devices of the same model is refused, never guessed.
    """
    collector = getattr(_log_tail(request), "device", None)
    if collector is None:
        return None
    rows = [row for row in collector.list_devices() if row.get("udid")]
    if len(rows) <= 1:
        return None
    model = str(client.get("device_model") or "")
    matches = [row for row in rows if model and str(row.get("product") or "") == model]
    if len(matches) == 1:
        return str(matches[0]["udid"])
    names = ", ".join(f"{row.get('name')} ({row.get('udid')})" for row in (matches or rows))
    raise HTTPException(
        status_code=409,
        detail=f"Several paired devices could run this app. Pick one on the Logs page: {names}",
    )


@router.post("/api/debug/deeplink")
async def debug_deeplink(request: Request) -> dict[str, object]:
    """Open a URL on a simulator (simctl), a device (devicectl), or inside an SDK app."""
    _loopback(request)
    body = await _json_body(request)
    url = str(body.get("url") or "")
    udid = str(body.get("udid") or "")
    client = _client(request, body.get("client_id"))
    kind = str(body.get("kind") or "")
    if kind in {"device", "simulator"}:
        on_device = kind == "device"
    else:
        on_device = bool(client and not client.get("is_simulator")) or bool(
            udid and not client and kind == "device"
        )
    if not on_device:
        sim_udid = udid or (str(client.get("device_key") or "") if client else "")
        try:
            result = open_deeplink(url, udid=sim_udid or "booted")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not result.get("ok"):
            raise HTTPException(status_code=400, detail=str(result.get("detail") or "open failed"))
        return result
    try:
        validate_deeplink(url, "booted")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    args: dict[str, Any] = {"url": url}
    if udid:
        args["udid"] = udid
    if client:
        args["client"] = client
        args["bundleId"] = client.get("bundle_id")
    result = _host_call(request, "open_url", args)
    if result.get("ok") is not False:
        return {"ok": True, "detail": "Opened on the device.", "url": url, "via": "devicectl"}
    if client and client.get("id"):
        return await _open_in_app(request, str(client["id"]), url, str(result.get("detail") or ""))
    raise HTTPException(status_code=400, detail=str(result.get("detail") or "open failed"))


async def _open_in_app(
    request: Request, client_id: str, url: str, reason: str
) -> dict[str, object]:
    """devicectl could not reach the device (older OS, no tunnel); the SDK opens it in-process."""
    try:
        await asyncio.to_thread(
            _sessions(request).invoke,
            client_id=client_id,
            name="nav.open_url",
            payload={"url": url},
            mutate=True,
        )
    except (LookupError, TimeoutError, PermissionError, ValueError) as exc:
        detail = f"{reason} The app could not open it either: {exc}".strip()
        raise HTTPException(status_code=400, detail=detail) from exc
    return {"ok": True, "detail": "Opened inside the app.", "url": url, "via": "sdk"}


def _log_capture_target(client: dict[str, object]) -> tuple[str, str | None]:
    """Simulator `device_key` is the sim UDID. A phone key is `vendor:<idfv>`, not a serial."""
    key = str(client.get("device_key") or "").strip() or None
    if client.get("is_simulator"):
        return "simulator", key
    return "device", None


@router.post("/api/debug/location")
async def debug_location(request: Request) -> dict[str, object]:
    """Simulated GPS on a simulator (simctl) or a paired iPhone, iPad, or Apple TV."""
    _loopback(request)
    body = await _json_body(request)
    clear = bool(body.get("clear"))
    lat = body.get("latitude")
    lon = body.get("longitude")
    udid = str(body.get("udid") or "")
    client = _client(request, body.get("client_id"))
    if client and client.get("is_simulator") and not udid:
        udid = str(client.get("device_key") or "")
    args: dict[str, Any] = {"clear": clear}
    if udid:
        args["udid"] = udid
    elif client:
        args["client"] = client
    else:
        raise HTTPException(status_code=400, detail="Pass udid or choose an app in Scope.")
    if not clear:
        try:
            args["latitude"] = float(lat)  # type: ignore[arg-type]
            args["longitude"] = float(lon)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            detail = "Latitude and longitude are required."
            raise HTTPException(status_code=400, detail=detail) from exc
    target = await asyncio.to_thread(_host_call, request, "resolve_target", dict(args))
    if target.get("ok") is False:
        detail = str(target.get("detail") or "Target not found.")
        raise HTTPException(status_code=400, detail=detail)
    if target.get("kind") == "device":
        tail = getattr(request.app.state, "log_tail", None)
        status = tail.status() if tail else {}
        streaming = bool(status.get("running") and status.get("source") == "device")
        try:
            return await asyncio.to_thread(
                set_device_location,
                str(target["udid"]),
                args.get("latitude"),
                args.get("longitude"),
                clear=clear,
                streaming=streaming,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    result = await asyncio.to_thread(
        _host_call, request, "set_location", {**args, "udid": target["udid"]}
    )
    if result.get("ok") is False:
        raise HTTPException(status_code=400, detail=str(result.get("detail") or "Location failed."))
    detail = "Cleared simulated location." if clear else f"Location set to {lat}, {lon}."
    return {"ok": True, "detail": f"{detail} ({target.get('name')})"}


def _log_tail(request: Request):
    tail = getattr(request.app.state, "log_tail", None)
    if tail is None:
        raise HTTPException(status_code=503, detail="Log capture unavailable")
    return tail


@router.post("/api/debug/logs/start")
async def debug_logs_start(request: Request) -> dict[str, object]:
    """Stream simulator or device logs for the selected app. Loopback only."""
    _loopback(request)
    body = await _json_body(request)
    client_id = str(body.get("client_id") or "").strip()
    if not client_id:
        raise HTTPException(status_code=400, detail="Choose an app in Scope first.")
    try:
        client = _sessions(request).get_client(client_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="App is not connected.") from exc
    source, udid = _log_capture_target(client)
    if source == "device":
        udid = await asyncio.to_thread(_log_device_udid, request, client)
    executable = str(client.get("executable") or "").strip()
    attribution = (
        AppAttribution(client_id=client_id, executable=executable) if executable else None
    )
    result = _log_tail(request).start(
        source,
        udid=udid,
        process=executable or None,
        attribution=attribution,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=400,
            detail=str(result.get("detail") or "Could not start capture."),
        )
    return result


@router.post("/api/debug/logs/stop")
def debug_logs_stop(request: Request) -> dict[str, object]:
    _loopback(request)
    return _log_tail(request).stop()


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


@router.post("/api/flows/send")
async def send_flow(request: Request) -> dict[str, object]:
    """Send one edited request from this Mac. The body is the form text, not the raw cache."""
    _loopback(request)
    payload = await _json_body(request)
    headers = payload.get("headers") or {}
    if not isinstance(headers, dict):
        raise HTTPException(status_code=400, detail="headers must be an object")
    raw_body = payload.get("body")
    if raw_body is None:
        body = b""
    elif isinstance(raw_body, str):
        body = raw_body.encode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="body must be a string")
    safe_headers = {str(key): str(value) for key, value in headers.items()}
    try:
        sent = replay_http(
            method=str(payload.get("method") or "GET"),
            url=str(payload.get("url") or ""),
            headers=safe_headers,
            body=body,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    redaction = _redaction(request)
    response_body = redaction.redact_body(sent["body"])
    return {
        "ok": True,
        "status": sent["status"],
        "headers": redaction.redact_headers(sent["headers"]),
        "body": response_body.decode("utf-8", errors="replace")[:8000],
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
