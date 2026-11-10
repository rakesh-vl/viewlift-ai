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
    bundle_id: str | None = None,
) -> list[str] | None:
    """
    None means "All apps" (including unattributed proxy traffic). A device scope expands
    to every app instance that registered from that device. A bundle scope expands to
    every install of that app.
    """
    ids = [item for item in (client_id or []) if item]
    sessions = debug_sessions(request) if device_key or bundle_id else None
    if device_key:
        if sessions is not None:
            ids.extend(sessions.client_ids_for_device(device_key))
        if not ids:
            return []
    if bundle_id:
        if sessions is not None:
            ids.extend(sessions.client_ids_for_bundle(bundle_id))
        if not ids:
            return []
    if not ids and not device_key and not bundle_id:
        return None
    return list(dict.fromkeys(ids))


def _text(value: str | None) -> str | None:
    text = (value or "").strip()
    return text or None


def scope_kwargs(
    request: Request,
    client_id: list[str] | None,
    device_key: str | None,
    bundle_id: str | None = None,
    auth_state: str | None = None,
    user_id: str | None = None,
    profile_id: str | None = None,
    anonymous_id: str | None = None,
) -> dict[str, object]:
    """client_ids for install scopes. spans when the filter is who was signed in."""
    ids = resolve_client_ids(request, client_id, _text(device_key), _text(bundle_id))
    auth = _text(auth_state)
    user = _text(user_id)
    profile = _text(profile_id)
    anonymous = _text(anonymous_id)
    if not any((auth, user, profile, anonymous)):
        return {"client_ids": ids}
    sessions = debug_sessions(request)
    spans: list[tuple[str, str, str | None]] = []
    if sessions is not None:
        spans = sessions.matching_spans(
            auth_state=auth,
            user_id=user,
            profile_id=profile,
            anonymous_id=anonymous,
        )
    if ids is not None:
        allowed = set(ids)
        spans = [item for item in spans if item[0] in allowed]
    return {"spans": spans}


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
