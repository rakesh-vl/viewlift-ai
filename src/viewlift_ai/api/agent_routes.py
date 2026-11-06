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


def _mutating(request: Request) -> bool:
    engine = getattr(request.app.state, "rule_engine", None)
    return bool(engine and engine.any_mutating_enabled())


def _mcp_status() -> dict[str, object]:
    port = http_port()
    return {
        "port": port,
        "url": f"http://127.0.0.1:{port}{HTTP_PATH}",
        "listening": is_listening(port),
    }


@router.get("/agent", response_class=HTMLResponse)
async def agent_page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "agent.html",
        {
            "title": "Agent",
            "active_nav": "agent",
            "mutating_enabled": _mutating(request),
            "mcp": _mcp_status(),
            "console_url": str(request.base_url).rstrip("/"),
            "tool_groups": TOOL_GROUPS,
            "tool_count": len(tool_names()),
            "read_commands": read_commands(),
            "write_commands": write_commands(),
        },
    )


@router.get("/api/agent/status")
def agent_status(request: Request) -> dict[str, object]:
    client = request.client.host if request.client else ""
    if client not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=403, detail="Agent status is loopback-only")
    return {"mcp": _mcp_status()}
