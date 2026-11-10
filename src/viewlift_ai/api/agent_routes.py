"""Agent page: how an AI agent connects to and uses the Viewlift AI MCP server."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from viewlift_ai.mcp_server.catalog import (
    TOOL_GROUPS,
    read_commands,
    tool_names,
    write_commands,
)
from viewlift_ai.mcp_server.runtime import HTTP_PATH, http_port, is_listening

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _mcp_status() -> dict[str, object]:
    port = http_port()
    return {
        "port": port,
        "url": f"http://127.0.0.1:{port}{HTTP_PATH}",
        "listening": is_listening(port),
    }


def _connected_device(request: Request) -> dict[str, object] | None:
    """The device with the most connected apps, so the diagram shows the user's own setup."""
    sessions = getattr(request.app.state, "debug_sessions", None)
    if sessions is None:
        return None
    groups: dict[str, dict[str, object]] = {}
    for row in sessions.list_clients():
        key = str(row.get("device_key") or row.get("id"))
        group = groups.setdefault(
            key,
            {
                "name": row.get("device_name") or row.get("device_model") or "Device",
                "is_simulator": bool(row.get("is_simulator")),
                "apps": [],
            },
        )
        apps = group["apps"]
        if isinstance(apps, list) and len(apps) < 3:
            apps.append({"name": row.get("app_name") or row.get("bundle_id"), "id": row.get("id")})
    if not groups:
        return None
    return max(groups.values(), key=lambda item: len(item["apps"]))  # type: ignore[arg-type]


@router.get("/agent", response_class=HTMLResponse)
async def agent_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "agent.html",
        {
            "title": "Agent",
            "active_nav": "agent",
            "mcp": _mcp_status(),
            "console_url": str(request.base_url).rstrip("/"),
            "tool_groups": TOOL_GROUPS,
            "tool_count": len(tool_names()),
            "read_commands": read_commands(),
            "write_commands": write_commands(),
            "device": _connected_device(request),
        },
    )


@router.get("/api/agent/status")
def agent_status(request: Request) -> dict[str, object]:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Agent status is loopback-only")
    return {"mcp": _mcp_status()}
