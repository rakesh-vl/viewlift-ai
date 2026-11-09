"""Shared helpers for workflow tools: console host calls, SDK commands, and image splitting."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from typing import Any

from mcp.server.fastmcp import Image
from mcp.types import ToolAnnotations

from viewlift_ai.mcp_server import guard, tools
from viewlift_ai.mcp_server.client import ConsoleClient, ConsoleError

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
READ_WEB = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

RunFn = Callable[[str, dict[str, Any], Callable[[], Any]], Any]


def api() -> ConsoleClient:
    return ConsoleClient()


def compact(args: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in args.items() if value not in (None, "", [], {})}


def host(
    tool: str, args: dict[str, Any], *, mutating: bool = False, confirm: bool = False
) -> dict[str, Any]:
    """Call one host tool in the console. Mutating tools honour read-only mode and confirm."""
    if mutating:
        if refusal := guard.read_only_refusal():
            return refusal
        if confirm is not True:
            return {"ok": False, "detail": "confirm must be true"}
    body = {"tool": tool, "args": compact(args), "confirm": bool(mutating and confirm)}
    try:
        result = api().post("/api/host/call", body)
    except ConsoleError as exc:
        result = {"ok": False, "status": exc.status, "detail": exc.detail}
    cleaned = guard.scrub_agent(result)
    return cleaned if isinstance(cleaned, dict) else {"result": cleaned}


def sdk(
    client_id: str, command: str, payload: dict[str, Any], *, mutating: bool, confirm: bool = False
) -> dict[str, Any]:
    if mutating:
        return tools.app_mutate(
            api(), client_id=client_id, command=command, payload=payload, confirm=confirm
        )
    return tools.app_inspect(api(), client_id=client_id, command=command, payload=payload)


def client_record(client_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """The app record for client_id (or an online bundle id), or an error body."""
    target, error, clients = tools._require(api(), client_id)
    if error or not target:
        return None, dict(error or {"ok": False, "detail": "client_id is required"})
    for client in clients:
        if client.get("id") == target:
            return client, None
    return None, {"ok": False, "detail": f"No app with client_id {client_id}."}


def target_args(
    *,
    client_id: str = "",
    udid: str = "",
    simulatorId: str = "",
    simulatorName: str = "",
    deviceId: str = "",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Routing arguments for target-aware host tools: an app's record or an explicit id."""
    args = compact(
        {
            "udid": udid,
            "simulatorId": simulatorId,
            "simulatorName": simulatorName,
            "deviceId": deviceId,
        }
    )
    if client_id:
        client, error = client_record(client_id)
        if error:
            return {}, error
        args["client"] = client
    return args, None


def sdk_result(result: dict[str, Any]) -> Any:
    """The command result inside a debug invoke response, or None."""
    event = result.get("event")
    if not isinstance(event, dict):
        return None
    payload = event.get("payload")
    return payload.get("result") if isinstance(payload, dict) else None


def split_host_image(result: dict[str, Any]) -> dict[str, Any] | list[Any]:
    """Return host image results as [metadata, Image] so agents see the picture."""
    encoded = result.get("base64")
    if result.get("mime") != "image/png" or not isinstance(encoded, str):
        return result
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return result
    meta = {key: value for key, value in result.items() if key != "base64"}
    return [meta, Image(data=raw, format="png")]


def split_sdk_image(result: dict[str, Any]) -> dict[str, Any] | list[Any]:
    cleaned, image = tools.split_image(result)
    if image is None:
        return cleaned
    return [cleaned, Image(data=image, format="png")]
