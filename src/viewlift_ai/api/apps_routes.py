"""Connected app instances grouped by device, plus per-app data housekeeping."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from viewlift_ai.api.scope import debug_sessions
from viewlift_ai.services.proxy_clients import unmatched_proxy_rows

router = APIRouter()


def _loopback(request: Request) -> None:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="App scope APIs are loopback-only")


def _sessions(request: Request):
    sessions = debug_sessions(request)
    if sessions is None:
        raise HTTPException(status_code=503, detail="Debug session service unavailable")
    return sessions


def _counts(request: Request) -> dict[str, dict[str, int]]:
    store = request.app.state.store
    counter = getattr(store, "client_counts", None)
    return counter() if counter is not None else {}


def apps_payload(request: Request) -> dict[str, object]:
    """SDK apps by device, plus apps seen only through the proxy (no SDK linked)."""
    tree = _sessions(request).apps_tree(_counts(request))
    ingest = getattr(request.app.state, "ingest", None)
    activity = ingest.activity().get("clients", {}) if ingest is not None else {}
    sdk_apps = []
    for device in tree["devices"]:
        for app in device["apps"]:
            raw = activity.get(app["id"]) if isinstance(activity, dict) else None
            app["activity"] = raw if isinstance(raw, dict) else None
            app["peer"] = str(raw.get("peer") or "") if isinstance(raw, dict) else ""
            sdk_apps.append(app)
    tree["accounts"] = _sessions(request).session_accounts()
    registry = getattr(request.app.state, "proxy_clients", None)
    rows = registry.snapshot() if registry is not None else []
    tree["proxy_clients"] = unmatched_proxy_rows(rows, sdk_apps, activity)
    return tree


@router.get("/api/apps")
def list_apps(request: Request) -> dict[str, object]:
    _loopback(request)
    return apps_payload(request)


@router.delete("/api/apps/{client_id}/data")
def clear_app_data(request: Request, client_id: str) -> dict[str, object]:
    _loopback(request)
    sessions = _sessions(request)
    ingest = getattr(request.app.state, "ingest", None)
    if ingest is not None:
        ingest.forget_client_activity(client_id)
    deleted = sessions.purge_client(client_id)
    return {"ok": True, "deleted": deleted, "client_id": client_id}


@router.delete("/api/apps/data")
def clear_all_app_data(request: Request) -> dict[str, object]:
    _loopback(request)
    deleted = request.app.state.store.wipe_session()
    reveal = getattr(request.app.state, "reveal", None)
    if reveal is not None:
        reveal.wipe()
    ingest = getattr(request.app.state, "ingest", None)
    if ingest is not None:
        ingest.forget_client_activity()
    registry = getattr(request.app.state, "proxy_clients", None)
    if registry is not None:
        registry.clear()
    sessions = _sessions(request)
    sessions.clear_crash_state()
    sessions.reset_session()
    return {"ok": True, "deleted": deleted}
