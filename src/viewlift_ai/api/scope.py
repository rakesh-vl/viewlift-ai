"""Resolve the dashboard's Device > App scope into stored client ids."""

from __future__ import annotations

from typing import Any

from fastapi import Request


def debug_sessions(request: Request) -> Any | None:
    return getattr(request.app.state, "debug_sessions", None)


def resolve_client_ids(
    request: Request,
    client_id: list[str] | None,
    device_key: str | None,
) -> list[str] | None:
    """
    None means "All apps" (including unattributed proxy traffic). A device scope expands
    to every app instance that registered from that device.
    """
    ids = [item for item in (client_id or []) if item]
    if device_key:
        sessions = debug_sessions(request)
        if sessions is not None:
            ids.extend(sessions.client_ids_for_device(device_key))
        if not ids:
            return []
    if not ids and not device_key:
        return None
    return list(dict.fromkeys(ids))


def app_tag(request_or_app: Any, client_id: str | None) -> dict[str, object]:
    """Short app identity attached to live websocket payloads."""
    if not client_id:
        return {"client_id": None, "app_name": None, "device_key": None}
    state = getattr(request_or_app, "state", None)
    sessions = getattr(state, "debug_sessions", None)
    if sessions is None:
        return {"client_id": client_id, "app_name": None, "device_key": None}
    try:
        client = sessions.get_client(client_id)
    except LookupError:
        return {"client_id": client_id, "app_name": None, "device_key": None}
    return {
        "client_id": client_id,
        "app_name": client.get("app_name"),
        "bundle_id": client.get("bundle_id"),
        "device_key": client.get("device_key"),
    }
