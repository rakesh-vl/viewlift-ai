"""Agent tools. Every app call names a client_id. None of these import FastAPI.

Every console response passes through guard.scrub in `_call`, so credentials never reach an agent.
"""

from __future__ import annotations

import base64
import binascii
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.parse import quote

from viewlift_ai.mcp_server import guard, insights
from viewlift_ai.mcp_server.catalog import COMMAND_NOTES
from viewlift_ai.mcp_server.client import ConsoleClient, ConsoleError
from viewlift_ai.services.debug_session import MUTATING_COMMANDS, READ_COMMANDS

_SYSLOG_NOTE = (
    "Device and simulator syslog rows are not tagged with client_id. "
    "syslog_matches are rows whose message contains this app's bundle id or "
    "executable and that do not belong to a different app."
)
_NETWORK_NOTE = (
    "Without client_id this throttle applies to every proxied app on this Mac. Pass client_id "
    "to shape one app's traffic through the SDK instead. It does not enable rewrite, map "
    "local, or breakpoints."
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
            result = api.get(path, kwargs.get("query"))
        else:
            result = api.post(path, kwargs.get("body"))
    except ConsoleError as exc:
        result = {"ok": False, "status": exc.status, "detail": exc.detail}
    cleaned = guard.scrub(result)
    return cleaned if isinstance(cleaned, dict) else {"result": cleaned}


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
    """One flow, redacted. Raw bodies only when reveal is allowed and already unlocked.

    Credentials stay masked either way.
    """
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    redacted = _owned_flow(api, flow_id, target)
    if redacted.get("ok") is False:
        return redacted
    if not reveal:
        redacted["note"] = "Redacted. Credentials, cookies, and signed URL parameters are masked."
        return redacted
    if not guard.reveal_allowed():
        redacted["revealed"] = False
        redacted["note"] = f"Reveal is off for agents. The user can set {guard.ALLOW_REVEAL_ENV}=1."
        return redacted
    status = _call(api, "GET", "/api/reveal/status")
    if not status.get("unlocked"):
        redacted["revealed"] = False
        redacted["note"] = _REVEAL_NOTE
        return redacted
    revealed = _strip_flow_images(
        _call(api, "GET", f"/api/flows/{flow_id}", query={"reveal": "true"})
    )
    revealed["note"] = "Raw bodies. Credentials are still masked."
    return revealed


def _strip_flow_images(detail: dict[str, Any]) -> dict[str, Any]:
    """Image bodies carry base64 for the dashboard; agents get the metadata only."""
    for side in ("request", "response"):
        block = detail.get(side)
        body = block.get("body") if isinstance(block, dict) else None
        image = body.get("image") if isinstance(body, dict) else None
        if isinstance(image, dict):
            image.pop("base64", None)
            image.pop("data_url", None)
    return detail


def _owned_flow(api: ConsoleClient, flow_id: str, target: str) -> dict[str, Any]:
    """Redacted flow detail, or an error when the flow belongs to another app."""
    detail = _call(api, "GET", f"/api/flows/{flow_id}")
    if detail.get("ok") is False:
        return detail
    owner = detail.get("client_id")
    if owner and owner != target:
        return {"ok": False, "detail": "flow belongs to a different app", "client_id": owner}
    return _strip_flow_images(detail)


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
    layers = inner.get("layers")
    has_images = isinstance(layers, list) and any(
        isinstance(item, dict) and "base64" in item for item in layers
    )
    if isinstance(layers, list) and has_images:
        # ui.layers pages carry per-view images for the dashboard 3D view; agents get sizes only.
        inner = {
            **inner,
            "layers": [
                {key: value for key, value in item.items() if key != "base64"}
                if isinstance(item, dict)
                else item
                for item in layers
            ],
        }
    if inner is not payload.get("result"):
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
    memory.object, memory.live_start, memory.live_stop, ui.hierarchy,
    ui.describe, ui.layers, ui.screenshot, swiftui.renders, assets.audit, accessibility.audit,
    storage.defaults, storage.keychain_metadata, storage.files, storage.file_read,
    storage.sqlite_schema, storage.sqlite_query, feature_flags.list, runtime.search,
    runtime.describe, macro.list, bug.capture, ott.snapshot, hang.list, crash.list,
    activity.snapshot, activity.live_start, activity.live_stop, playback.snapshot, playback.drm,
    playback.waterfall, ui.focus, runtime.libraries, runtime.image_classes, explorer.roots,
    explorer.object, explorer.instances, storage.cookies, settings.get.
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

    Commands: ui.highlight, ui.edit, ui.visual, storage.defaults_set, storage.file_write,
    storage.snapshot_create, storage.snapshot_restore, feature_flags.set, feature_flags.reset,
    macro.record_start, macro.record_stop, macro.replay, push.local, nav.open_url, explorer.set,
    explorer.call, storage.defaults_delete, storage.file_rename, storage.file_delete,
    storage.cookie_delete, settings.set.
    """
    if refusal := guard.read_only_refusal():
        return refusal
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
    api: ConsoleClient, *, profile: str, client_id: str = "", confirm: bool = False
) -> dict[str, Any]:
    """Apply a named throttle preset. confirm must be true.

    With client_id the SDK shapes that app's own traffic on any simulator or device. Without
    it the preset applies to the proxy and every app routed through it.
    """
    if refusal := guard.read_only_refusal():
        return refusal
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true", "note": _NETWORK_NOTE}
    if client_id:
        return app_mutate(
            api,
            client_id=client_id,
            command="net.profile",
            payload={"profile": profile},
            confirm=True,
        )
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
    """Open a URL for this app on its simulator or device. confirm must be true.

    Simulators use simctl. Devices use devicectl (the app relaunches with the URL); when the
    device cannot be reached that way, the SDK opens the URL inside the running app.
    """
    if refusal := guard.read_only_refusal():
        return refusal
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    return _call(api, "POST", "/api/debug/deeplink", body={"url": url, "client_id": target})


def set_location(
    api: ConsoleClient,
    *,
    client_id: str = "",
    udid: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    clear: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Set or clear simulated GPS on the simulator or device that runs client_id.

    udid targets a simulator or paired device directly (from list_targets), not a device_key.
    """
    if refusal := guard.read_only_refusal():
        return refusal
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    if not client_id and not udid:
        detail = "Pass client_id (from list_apps) or udid (from list_targets)."
        return {"ok": False, "detail": detail}
    body: dict[str, object] = {"clear": clear}
    if client_id:
        target, error, _clients = _require(api, client_id)
        if error or not target:
            return error or {"ok": False, "detail": "client_id is required"}
        body["client_id"] = target
    if udid:
        body["udid"] = udid
    if not clear:
        body["latitude"] = latitude
        body["longitude"] = longitude
    return _call(api, "POST", "/api/debug/location", body=body)


def replay_flow(api: ConsoleClient, *, flow_id: str, confirm: bool = False) -> dict[str, Any]:
    """Resend one captured request. Requires an already unlocked reveal session."""
    if refusal := guard.read_only_refusal():
        return refusal
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    status = _call(api, "GET", "/api/reveal/status")
    if not status.get("unlocked"):
        return {"ok": False, "detail": _REVEAL_NOTE}
    return _call(api, "POST", f"/api/flows/{flow_id}/replay", body={})


def _flow_rows(api: ConsoleClient, target: str, limit: int, **filters: object) -> dict[str, Any]:
    query: dict[str, object] = {"client_id": target, "limit": max(1, min(limit, 1000))}
    query.update({key: value for key, value in filters.items() if value not in (None, "")})
    return _call(api, "GET", "/api/flows", query=query)


def network_summary(
    api: ConsoleClient, *, client_id: str, host: str = "", limit: int = 300
) -> dict[str, Any]:
    """Host health, latency percentiles, slow and large calls, and repeated requests."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    found = _flow_rows(api, target, limit, host=host)
    if found.get("ok") is False:
        return found
    flows = [row for row in found.get("flows") or [] if isinstance(row, dict)]
    return {"client_id": target, **insights.network_summary(flows)}


def failed_requests(
    api: ConsoleClient, *, client_id: str, host: str = "", limit: int = 300
) -> dict[str, Any]:
    """4xx, 5xx, transport errors, and TLS failures for one app, grouped by endpoint."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    found = _flow_rows(api, target, limit, host=host)
    if found.get("ok") is False:
        return found
    flows = [row for row in found.get("flows") or [] if isinstance(row, dict)]
    return {"client_id": target, "scanned": len(flows), **insights.failed_requests(flows)}


def export_request(
    api: ConsoleClient, *, flow_id: str, client_id: str, language: str = "curl"
) -> dict[str, Any]:
    """Rebuild one captured request as curl, Swift URLSession, or Python. Secrets stay masked."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    detail = _owned_flow(api, flow_id, target)
    if detail.get("ok") is False:
        return detail
    try:
        code = insights.request_code(detail, language)
    except ValueError as exc:
        return {"ok": False, "detail": str(exc)}
    return {
        "flow_id": flow_id,
        "language": language.lower(),
        "code": guard.scrub_text(code),
        "note": f"Masked values read {guard.MASK}. Supply real credentials yourself to run it.",
    }


def compare_flows(
    api: ConsoleClient, *, flow_a: str, flow_b: str, client_id: str
) -> dict[str, Any]:
    """Differences between two flows of one app: status, timing, headers, and JSON body paths."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    left = _owned_flow(api, flow_a, target)
    if left.get("ok") is False:
        return left
    right = _owned_flow(api, flow_b, target)
    if right.get("ok") is False:
        return right
    return insights.compare_flows(left, right)


def _wait(
    probe: Callable[[], dict[str, Any] | None],
    timeout_s: float,
    *,
    interval: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0.5, min(timeout_s, 30.0))
    while True:
        hit = probe()
        if hit is not None or time.monotonic() >= deadline:
            return hit
        sleep(interval)


def wait_for_flow(
    api: ConsoleClient,
    *,
    client_id: str,
    host: str = "",
    method: str = "",
    status: int | None = None,
    text: str = "",
    timeout_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for a new matching request after this call starts. Use after a UI action."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    filters = {"host": host, "method": method, "status": status, "text": text}
    baseline = _flow_rows(api, target, 200, **filters)
    seen = {row.get("id") for row in baseline.get("flows") or [] if isinstance(row, dict)}

    def probe() -> dict[str, Any] | None:
        rows = _flow_rows(api, target, 50, **filters).get("flows") or []
        fresh = [row for row in rows if isinstance(row, dict) and row.get("id") not in seen]
        return fresh[0] if fresh else None

    hit = _wait(probe, timeout_s, sleep=sleep)
    if hit is None:
        return {"ok": False, "detail": "no matching request before timeout", "timeout_s": timeout_s}
    return {"ok": True, "flow": hit}


def wait_for_log(
    api: ConsoleClient,
    *,
    client_id: str,
    text: str,
    level: str = "",
    timeout_s: float = 15.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Wait for a new SDK log line of this app that contains text."""
    if not text:
        return {"ok": False, "detail": "text is required"}
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    query: dict[str, object] = {"client_id": target, "text": text, "limit": 200}
    if level:
        query["level"] = level
    baseline = _call(api, "GET", "/api/logs", query=query)
    seen = {row.get("id") for row in baseline.get("logs") or [] if isinstance(row, dict)}

    def probe() -> dict[str, Any] | None:
        rows = _call(api, "GET", "/api/logs", query={**query, "limit": 50}).get("logs") or []
        fresh = [row for row in rows if isinstance(row, dict) and row.get("id") not in seen]
        return fresh[0] if fresh else None

    hit = _wait(probe, timeout_s, sleep=sleep)
    if hit is None:
        return {"ok": False, "detail": "no matching log before timeout", "timeout_s": timeout_s}
    return {"ok": True, "log": hit}


def summarize_logs(
    api: ConsoleClient, *, client_id: str, level: str = "", limit: int = 500
) -> dict[str, Any]:
    """Counts by level and subsystem, plus the most frequent warning and error signatures."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    query: dict[str, object] = {"client_id": target, "limit": max(1, min(limit, 1000))}
    if level:
        query["level"] = level
    found = _call(api, "GET", "/api/logs", query=query)
    if found.get("ok") is False:
        return found
    rows = [row for row in found.get("logs") or [] if isinstance(row, dict)]
    return {"client_id": target, **insights.log_summary(rows)}


def get_log(api: ConsoleClient, *, log_id: str, client_id: str) -> dict[str, Any]:
    """Full text of one log line with its linked flow id. Refuses a line from another app."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    detail = _call(api, "GET", f"/api/logs/{log_id}")
    if detail.get("ok") is False:
        return detail
    owner = detail.get("client_id")
    if owner and owner != target:
        return {"ok": False, "detail": "log belongs to a different app", "client_id": owner}
    return detail


_TRIAGE_COMMANDS = {
    "performance": "performance.snapshot",
    "memory": "memory.snapshot",
    "hangs": "hang.list",
    "screen": "activity.snapshot",
}
# Crashes come from the console's store, so they still answer after the app has died.
_TRIAGE_SECTIONS = (*_TRIAGE_COMMANDS, "crashes", "network", "logs")


def _command_result(result: dict[str, Any]) -> Any:
    if result.get("ok") is False:
        return {"ok": False, "detail": result.get("detail")}
    event = result.get("event")
    payload = event.get("payload") if isinstance(event, dict) else None
    if isinstance(payload, dict):
        if payload.get("ok") is False:
            return {"ok": False, "detail": payload.get("error") or payload.get("detail")}
        return payload.get("result")
    return result


def triage_app(
    api: ConsoleClient, *, client_id: str, include: list[str] | None = None
) -> dict[str, Any]:
    """One-shot health report: device snapshots, failed requests, and top log errors."""
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    sections = include or list(_TRIAGE_SECTIONS)
    unknown = [name for name in sections if name not in _TRIAGE_SECTIONS]
    if unknown:
        return {
            "ok": False,
            "detail": f"unknown sections: {', '.join(unknown)}",
            "sections": list(_TRIAGE_SECTIONS),
        }
    jobs: dict[str, Callable[[], Any]] = {}
    for name in sections:
        if name in _TRIAGE_COMMANDS:
            command = _TRIAGE_COMMANDS[name]
            jobs[name] = lambda command=command: _command_result(
                _call(
                    api,
                    "POST",
                    "/api/debug/invoke",
                    body={"client_id": target, "name": command, "payload": {}},
                )
            )
    if "crashes" in sections:
        jobs["crashes"] = lambda: crash_reports(api, client_id=target, limit=2)
    if "network" in sections:
        jobs["network"] = lambda: _network_brief(api, target)
    if "logs" in sections:
        jobs["logs"] = lambda: _logs_brief(api, target)
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        report = {name: future.result() for name, future in futures.items()}
    return {"client_id": target, **report}


def crash_reports(api: ConsoleClient, *, client_id: str, limit: int = 3) -> dict[str, Any]:
    """Stored crashes for one app, newest first, with the whole stack as file:line per frame.

    Works after the app has died. Fatal signals, uncaught exceptions, and macOS crash reports
    all land here.
    """
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    found = _call(api, "GET", "/api/debug/crashes", query={"client_id": target, "limit": 20})
    if found.get("ok") is False:
        return found
    rows = [row for row in found.get("events") or [] if isinstance(row, dict)]
    return {"client_id": target, **insights.crash_summary(rows, limit=limit)}


def crash_detail(api: ConsoleClient, *, client_id: str, crash_id: str = "") -> dict[str, Any]:
    """Everything needed to fix one crash: diagnosis, code at the crash site, stack, and timeline.

    Defaults to the newest crash. `poster` holds the last screen before the crash as a JPEG; the
    MCP layer turns it into an image.
    """
    target, error, _clients = _require(api, client_id)
    if error or not target:
        return error or {"ok": False, "detail": "client_id is required"}
    if not crash_id:
        found = _call(api, "GET", "/api/debug/crashes", query={"client_id": target, "limit": 1})
        if found.get("ok") is False:
            return found
        rows = [row for row in found.get("events") or [] if isinstance(row, dict)]
        if not rows:
            return {"ok": False, "client_id": target, "detail": "no crashes stored for this app"}
        crash_id = str(rows[0].get("id") or "")
    detail = _call(
        api,
        "GET",
        f"/api/debug/crashes/{quote(crash_id, safe='')}/detail",
        query={"client_id": target},
    )
    if detail.get("ok") is False:
        return detail
    crash = detail.get("crash") if isinstance(detail.get("crash"), dict) else {}
    diagnosis = crash.get("diagnosis") if isinstance(crash.get("diagnosis"), dict) else {}
    summary = insights.crash_summary([{"id": crash_id, "payload": crash}], limit=1)["crashes"]
    replay = crash.get("replay") if isinstance(crash.get("replay"), dict) else {}
    return {
        "client_id": target,
        "crash_id": crash_id,
        "headline": diagnosis.get("headline"),
        "root_cause": diagnosis.get("root_cause"),
        "crash_site": diagnosis.get("crash_site"),
        "suggested_fix_area": diagnosis.get("suggested_fix_area"),
        "steps_to_reproduce": diagnosis.get("steps_to_reproduce"),
        "timeline": diagnosis.get("timeline"),
        "environment": diagnosis.get("environment"),
        "stack": summary[0]["stack"] if summary else [],
        "app_stack": diagnosis.get("app_stack"),
        "replay": {key: value for key, value in replay.items() if key != "timeline"},
        "replay_url": detail.get("replay_url"),
        "report_url": detail.get("report_url"),
        "brief": detail.get("brief"),
        "poster": detail.get("poster"),
    }


def split_poster(result: dict[str, Any]) -> tuple[dict[str, Any], bytes | None]:
    poster = result.get("poster")
    if not isinstance(poster, dict) or not poster.get("base64"):
        return {key: value for key, value in result.items() if key != "poster"}, None
    try:
        data = base64.b64decode(str(poster["base64"]), validate=True)
    except (binascii.Error, ValueError):
        data = None
    return {key: value for key, value in result.items() if key != "poster"}, data


def _network_brief(api: ConsoleClient, target: str) -> dict[str, Any]:
    rows = _flow_rows(api, target, 300).get("flows") or []
    flows = [row for row in rows if isinstance(row, dict)]
    summary = insights.network_summary(flows, top=3)
    failures = insights.failed_requests(flows, limit=5)
    return {
        "requests": summary["requests"],
        "failures": summary["failures"],
        "p95_ms": summary["p95_ms"],
        "hints": summary["hints"],
        "failed_groups": failures["groups"],
        "slowest": summary["slowest"],
    }


def _logs_brief(api: ConsoleClient, target: str) -> dict[str, Any]:
    found = _call(api, "GET", "/api/logs", query={"client_id": target, "limit": 500})
    rows = [row for row in found.get("logs") or [] if isinstance(row, dict)]
    summary = insights.log_summary(rows, top=8)
    return {"levels": summary["levels"], "top_issues": summary["top_issues"]}


def list_rules(api: ConsoleClient) -> dict[str, Any]:
    """Rules that can change traffic: map local, map remote, rewrite, throttle, breakpoints."""
    rules = _call(api, "GET", "/api/rules")
    pending = _call(api, "GET", "/api/breakpoints/pending")
    return {
        "rules": rules,
        "pending_breakpoints": pending,
        "note": "Read-only. Agents cannot create or toggle proxy rules.",
    }


def console_status(api: ConsoleClient) -> dict[str, Any]:
    """Health check: console reachability, apps online, reveal lock, rules, and agent policy."""
    try:
        clients = _apps(api)
    except ConsoleError as exc:
        return {"ok": False, "status": exc.status, "detail": exc.detail, "policy": guard.policy()}
    reveal = _call(api, "GET", "/api/reveal/status")
    rules = _call(api, "GET", "/api/rules/status")
    online = [item for item in clients if item.get("online")]
    return {
        "ok": True,
        "console": api.base_url,
        "apps": len(clients),
        "apps_online": len(online),
        "online": _brief(online),
        "reveal_unlocked": bool(reveal.get("unlocked")),
        "rewrite_rules_active": bool(rules.get("mutating_enabled")),
        "policy": guard.policy(),
    }


def describe_command(command: str = "") -> dict[str, Any]:
    """Payload notes for one on-device command, or every command when command is empty."""

    def row(name: str) -> dict[str, Any]:
        return {
            "name": name,
            "kind": "read" if name in READ_COMMANDS else "write",
            "tool": "app_inspect" if name in READ_COMMANDS else "app_mutate",
            "note": COMMAND_NOTES.get(name, ""),
        }

    if command:
        if command not in READ_COMMANDS | MUTATING_COMMANDS:
            matches = sorted(n for n in READ_COMMANDS | MUTATING_COMMANDS if command in n)
            return {"ok": False, "detail": "unknown command", "matches": matches}
        return row(command)
    return {"commands": [row(name) for name in sorted(READ_COMMANDS | MUTATING_COMMANDS)]}
