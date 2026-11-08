"""Devices page: build, run, and drive a simulator or a paired device from the console.

The page calls the loopback host API (`/api/host/call`), the same toolkit the MCP server uses.
This module only serves the HTML shell.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))

router = APIRouter()


def _page(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "simulator.html",
        {"title": "Devices", "active_nav": "simulator"},
    )


@router.get("/simulator", response_class=HTMLResponse)
async def simulator_page(request: Request) -> HTMLResponse:
    return _page(request)


@router.get("/devices", response_class=HTMLResponse)
async def devices_page(request: Request) -> HTMLResponse:
    return _page(request)
