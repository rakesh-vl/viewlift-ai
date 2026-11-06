"""Agent tools. Every app call names a client_id. None of these import FastAPI."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from viewlift_ai.mcp_server.client import ConsoleClient, ConsoleError
from viewlift_ai.services.debug_session import MUTATING_COMMANDS, READ_COMMANDS

_SYSLOG_NOTE = (
    "Device and simulator syslog rows are not tagged with client_id. "
    "syslog_matches are rows whose message contains this app's bundle id or "
    "executable and that do not belong to a different app."
)
_NETWORK_NOTE = (
    "This throttle applies to every proxied app on this Mac, not one client. "
    "It does not enable rewrite, map local, or breakpoints."
)
_REVEAL_NOTE = "Reveal is locked. This tool does not unlock reveal or enable rewrite."


def _apps(api: ConsoleClient) -> list[dict[str, Any]]:
    payload = api.get("/api/debug/clients")
    clients = payload.get("clients")
    if not isinstance(clients, list):
        return []
    return [item for item in clients if isinstance(item, dict)]


def _brief(clients: list[dict[str, Any]]) -> list[dict[str, object]]:
    rows = []
    for client in clients:
        rows.append(
            {
                "id": client.get("id"),
                "app_name": client.get("app_name"),
                "bundle_id": client.get("bundle_id"),
                "device_key": client.get("device_key"),
                "device_name": client.get("device_name"),
                "platform": client.get("platform"),
                "online": client.get("online"),
                "is_simulator": client.get("is_simulator"),
            }
        )
    return rows


def resolve_target(
    clients: list[dict[str, Any]], client_id: str
) -> tuple[str | None, dict[str, object] | None]:
    """Return a client id, or an error body that must not enqueue a command."""
    text = (client_id or "").strip()
    if not text:
        return None, {
            "ok": False,
            "detail": "client_id is required",
            "apps": _brief(clients),
        }
    exact = [item for item in clients if item.get("id") == text]
    if len(exact) == 1:
        return text, None
    online = [
        item
        for item in clients
        if item.get("bundle_id") == text and item.get("online") and not item.get("superseded_by")
    ]
    if len(online) == 1:
        return str(online[0]["id"]), None
    return None, {
        "ok": False,
        "detail": "client_id matches more than one app or none",
        "apps": _brief(online or clients),
    }


def _call(api: ConsoleClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        if method == "GET":
            return api.get(path, kwargs.get("query"))
        return api.post(path, kwargs.get("body"))
    except ConsoleError as exc:
        return {"ok": False, "status": exc.status, "detail": exc.detail}


def _require(
    api: ConsoleClient, client_id: str
) -> tuple[str | None, dict[str, object] | None, list[dict[str, Any]]]:
    try:
        clients = _apps(api)
    except ConsoleError as exc:
        return None, {"ok": False, "status": exc.status, "detail": exc.detail}, []
    target, error = resolve_target(clients, client_id)
    return target, error, clients


def list_devices(api: ConsoleClient) -> dict[str, Any]:
    """Group connected apps by device. Several apps on one phone share a device_key."""
    return _call(api, "GET", "/api/apps")


def list_apps(
    api: ConsoleClient,
    *,
    device_key: str = "",
    bundle_id: str = "",
    online_only: bool = False,
) -> dict[str, Any]:
    """List apps. Filter by device_key, bundle_id, or online. Does not pick one."""
    try:
        clients = _apps(api)
    except ConsoleError as exc:
        return {"ok": False, "status": exc.status, "detail": exc.detail}
    rows = clients
    if device_key:
        rows = [item for item in rows if item.get("device_key") == device_key]
    if bundle_id:
        rows = [item for item in rows if item.get("bundle_id") == bundle_id]
    if online_only:
        rows = [item for item in rows if item.get("online")]
    return {"apps": _brief(rows), "count": len(rows)}


def get_app(api: ConsoleClient, client_id: str) -> dict[str, Any]:
    """Identity, capabilities, and frameworks for one app. client_id is required."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    result = _call(api, "GET", "/api/debug/app", query={"client_id": target})
    result.pop("unlock_token", None)
    return result


def list_flows(
    api: ConsoleClient,
    *,
    client_id: str,
    method: str = "",
    host: str = "",
    status: int | None = None,
    text: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Redacted flow summaries for one app. Omits rows with no client_id."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    query: dict[str, object] = {"client_id": target, "limit": max(1, min(limit, 200))}
    if method:
        query["method"] = method
    if host:
        query["host"] = host
    if status is not None:
        query["status"] = status
    if text:
        query["text"] = text
    result = _call(api, "GET", "/api/flows", query=query)
    result["note"] = "Redacted summaries for this client_id. Unattributed proxy rows are omitted."
    return result


def get_flow(
    api: ConsoleClient,
    *,
    flow_id: str,
    client_id: str,
    reveal: bool = False,
) -> dict[str, Any]:
    """One flow, redacted. Raw bytes only when reveal is already unlocked."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    redacted = _call(api, "GET", f"/api/flows/{flow_id}")
    if redacted.get("ok") is False:
        return redacted
    owner = redacted.get("client_id")
    if owner and owner != target:
        return {"ok": False, "detail": "flow belongs to a different app", "client_id": owner}
    if not reveal:
        redacted["note"] = (
            "Redacted. Pass reveal=true only after the console reveal session is unlocked."
        )
        return redacted
    status = _call(api, "GET", "/api/reveal/status")
    status.pop("unlock_token", None)
    if not status.get("unlocked"):
        redacted["revealed"] = False
        redacted["note"] = _REVEAL_NOTE
        return redacted
    revealed = _call(api, "GET", f"/api/flows/{flow_id}", query={"reveal": "true"})
    revealed.pop("unlock_token", None)
    return revealed


def list_logs(
    api: ConsoleClient,
    *,
    client_id: str,
    level: str = "",
    subsystem: str = "",
    text: str = "",
    process: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """SDK logs for one app, plus syslog lines that mention its bundle id or executable."""
    target, error, clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    client = next(item for item in clients if item.get("id") == target)
    cap = max(1, min(limit, 200))
    query: dict[str, object] = {"client_id": target, "limit": cap}
    if level:
        query["level"] = level
    if subsystem:
        query["subsystem"] = subsystem
    message_text = text or process
    if message_text:
        query["text"] = message_text
    sdk = _call(api, "GET", "/api/logs", query=query)
    needles = [
        item
        for item in (client.get("bundle_id"), client.get("executable"), process)
        if isinstance(item, str) and item
    ]
    syslog: list[dict[str, Any]] = []
    seen = {row.get("id") for row in sdk.get("logs") or [] if isinstance(row, dict)}
    for needle in needles:
        extra_query: dict[str, object] = {"text": needle, "limit": cap}
        if level:
            extra_query["level"] = level
        if subsystem:
            extra_query["subsystem"] = subsystem
        found = _call(api, "GET", "/api/logs", query=extra_query)
        for row in found.get("logs") or []:
            if not isinstance(row, dict) or row.get("id") in seen:
                continue
            owner = row.get("client_id")
            if owner and owner != target:
                continue
            if process and process not in str(row.get("message") or ""):
                continue
            seen.add(row.get("id"))
            syslog.append(row)
    return {
        "sdk_logs": sdk.get("logs") or [],
        "syslog_matches": syslog,
        "note": _SYSLOG_NOTE,
    }


def list_packages(api: ConsoleClient, client_id: str) -> dict[str, Any]:
    """Swift packages resolved for one app's Xcode build."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    return _call(api, "GET", "/api/debug/packages", query={"client_id": target})


def list_debug_events(
    api: ConsoleClient,
    *,
    client_id: str,
    module: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Recent debug events posted by one app."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    query: dict[str, object] = {"client_id": target, "limit": max(1, min(limit, 200))}
    if module:
        query["module"] = module
    return _call(api, "GET", "/api/debug/events", query=query)


def split_image(result: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    """Pull a PNG out of a command result so the MCP layer can return an image."""
    event = result.get("event")
    if not isinstance(event, dict):
        return result, None
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return result, None
    inner = payload.get("result")
    if not isinstance(inner, dict):
        return result, None
    texture = inner.get("screenshot")
    if isinstance(texture, dict) and "base64" in texture:
        # ui.hierarchy embeds a layer texture for the dashboard; keep it out of agent context.
        slim = {key: value for key, value in texture.items() if key != "base64"}
        inner = {**inner, "screenshot": slim}
        result = {**result, "event": {**event, "payload": {**payload, "result": inner}}}
        event = result["event"]
        payload = event["payload"]
    if inner.get("mime") != "image/png":
        return result, None
    encoded = inner.get("base64")
    if not isinstance(encoded, str) or not encoded:
        return result, None
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return result, None
    cleaned = {key: value for key, value in inner.items() if key != "base64"}
    cleaned["bytes"] = len(raw)
    return {
        **result,
        "event": {**event, "payload": {**payload, "result": cleaned}},
    }, raw


def app_inspect(
    api: ConsoleClient,
    *,
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one read command on one app and wait for its result.

    Commands: performance.snapshot, memory.snapshot, memory.graph, memory.leaks, memory.heap,
    memory.live_start, memory.live_stop, ui.hierarchy,
    ui.describe, ui.screenshot, swiftui.renders, assets.audit, accessibility.audit, storage.defaults,
    storage.keychain_metadata, storage.files, storage.file_read, storage.sqlite_schema,
    storage.sqlite_query, feature_flags.list, runtime.search, runtime.describe, macro.list,
    bug.capture, ott.snapshot, hang.list, crash.list.
    """
    if command not in READ_COMMANDS:
        return {
            "ok": False,
            "detail": "command is not a read command",
            "commands": sorted(READ_COMMANDS),
        }
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    return _call(
        api,
        "POST",
        "/api/debug/invoke",
        body={"client_id": target, "name": command, "payload": payload or {}},
    )


def app_mutate(
    api: ConsoleClient,
    *,
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Change one app. confirm must be true. Does not enable proxy rewrite.

    Commands: ui.highlight, ui.edit, storage.defaults_set, storage.file_write,
    storage.snapshot_create, storage.snapshot_restore, feature_flags.set, feature_flags.reset,
    macro.record_start, macro.record_stop, macro.replay, push.local.
    """
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    if command not in MUTATING_COMMANDS:
        return {
            "ok": False,
            "detail": "command is not a mutating command",
            "commands": sorted(MUTATING_COMMANDS),
        }
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    return _call(
        api,
        "POST",
        "/api/debug/invoke",
        body={
            "client_id": target,
            "name": command,
            "payload": payload or {},
            "mutate": True,
        },
    )


def set_network_profile(
    api: ConsoleClient, *, profile: str, confirm: bool = False
) -> dict[str, Any]:
    """Apply a named throttle preset. Global to the proxy. confirm must be true."""
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true", "note": _NETWORK_NOTE}
    result = _call(api, "POST", "/api/debug/network-profile", body={"profile": profile})
    result["note"] = _NETWORK_NOTE
    return result


def open_deeplink(
    api: ConsoleClient,
    *,
    url: str,
    client_id: str,
    confirm: bool = False,
) -> dict[str, Any]:
    """Open a URL on the simulator that hosts this app. confirm must be true."""
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    target, error, clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    client = next(item for item in clients if item.get("id") == target)
    if not client.get("is_simulator"):
        return {
            "ok": False,
            "detail": (
                "Deeplink open is simulator-only. A phone device_key is not a simulator UDID."
            ),
        }
    udid = str(client.get("device_key") or "")
    return _call(
        api,
        "POST",
        "/api/debug/deeplink",
        body={"url": url, "udid": udid, "client_id": target},
    )


def set_location(
    api: ConsoleClient,
    *,
    udid: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    clear: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Set or clear simulated GPS. udid is a paired hardware UDID, not an app device_key."""
    sources = _call(api, "GET", "/api/logs/sources")
    devices = sources.get("devices") if isinstance(sources, dict) else None
    if confirm is not True:
        return {
            "ok": False,
            "detail": "confirm must be true. Pass a hardware UDID from devices, not device_key.",
            "devices": devices or [],
        }
    if not udid:
        return {"ok": False, "detail": "udid is required", "devices": devices or []}
    body: dict[str, object] = {"udid": udid, "clear": clear}
    if not clear:
        body["latitude"] = latitude
        body["longitude"] = longitude
    result = _call(api, "POST", "/api/debug/location", body=body)
    result["devices"] = devices or []
    return result


def replay_flow(api: ConsoleClient, *, flow_id: str, confirm: bool = False) -> dict[str, Any]:
    """Resend one captured request. Requires an already unlocked reveal session."""
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    status = _call(api, "GET", "/api/reveal/status")
    status.pop("unlock_token", None)
    if not status.get("unlocked"):
        return {"ok": False, "detail": _REVEAL_NOTE}
    return _call(api, "POST", f"/api/flows/{flow_id}/replay", body={})
