"""Mac-side developer tools: Xcode, simulators, devices, LLDB, SwiftPM, Apple docs, previews.

Loopback only. Handlers run in the thread pool because they wait on subprocesses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from viewlift_ai import defaults
from viewlift_ai.services.host import HostToolkit
from viewlift_ai.services.host.runner import HostError

MAX_BODY_BYTES = 16 * 1024 * 1024

router = APIRouter()


def toolkit(request: Request) -> HostToolkit:
    service = getattr(request.app.state, "host_toolkit", None)
    if service is None:
        config = getattr(request.app.state, "config", None)
        if config is None:
            raise HTTPException(status_code=503, detail="Host tools unavailable")
        service = HostToolkit(config.data_dir)
        app = request.app
        service.ctx.receiver = lambda: _receiver_settings(app)
        request.app.state.host_toolkit = service
    return service


def _receiver_settings(app: Any) -> dict[str, Any]:
    """Address, pin, and debug key the UI runner needs; empty when the receiver is off."""
    ingest = getattr(app.state, "ingest", None)
    if ingest is None or not ingest.enabled:
        return {}
    status = ingest.status()
    return {
        "url": status.get("receiver_url") or "",
        "fingerprint": status.get("pinned_fingerprint") or "",
        "debug_key": status.get("debug_key") or "",
    }


def _require_local(request: Request, *, json_body: bool) -> None:
    # Starlette TestClient reports host "testclient"; production binds 127.0.0.1 only.
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Host tools are loopback-only")
    if not json_body:
        return
    # A JSON content type forces a CORS preflight, so other sites cannot post here.
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status_code=415, detail="Content-Type must be application/json")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Request is larger than 16 MB.")


@router.get("/api/host/tools")
def host_tools(request: Request) -> dict[str, Any]:
    _require_local(request, json_body=False)
    service = toolkit(request)
    return {
        "tools": [{"name": name, "mutating": service.is_mutating(name)} for name in service.names()]
    }


@router.post("/api/host/call")
def host_call(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request, json_body=True)
    name = body.get("tool")
    args = body.get("args") or {}
    if not isinstance(name, str) or not isinstance(args, dict):
        raise HTTPException(status_code=400, detail="tool (string) and args (object) are required")
    return toolkit(request).call(name, args, confirm=body.get("confirm") is True)


@router.get("/api/config/limits")
def config_limits() -> dict[str, Any]:
    return defaults.limits()


@router.get("/api/host/environment")
def host_environment(request: Request) -> dict[str, Any]:
    """Installed Xcode, simulators, and paired devices, for forms that need real destinations."""
    _require_local(request, json_body=False)
    return toolkit(request).call("host_environment", {})


@router.get("/api/host/targets")
def host_targets(request: Request) -> dict[str, Any]:
    """Every simulator and device with the capabilities each one supports."""
    _require_local(request, json_body=False)
    return toolkit(request).call("list_targets", {})


@router.get("/api/host/roots")
def host_roots(request: Request) -> dict[str, Any]:
    _require_local(request, json_body=False)
    return toolkit(request).ctx.roots.status()


@router.post("/api/host/roots")
def set_host_roots(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    _require_local(request, json_body=True)
    roots = body.get("roots")
    if not isinstance(roots, list):
        raise HTTPException(status_code=400, detail="roots must be a list of folder paths")
    try:
        return toolkit(request).ctx.roots.set_saved(str(item) for item in roots)
    except HostError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
