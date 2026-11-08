"""Single source for ports, limits, and timeouts shared by the console, MCP, and web UI.

Each value can be overridden by the environment variable named next to it. The web UI
reads the resolved values from `GET /api/config/limits` instead of keeping its own copies.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

CONSOLE_PORT = 8741
MCP_PORT = 8742
INGEST_PORT = 8743
PROXY_PORT = 8080

MAX_BODY_BYTES = 262_144
JOB_WAIT_SECONDS = 25.0
JOB_MAX_WAIT_SECONDS = 30.0
COMMAND_TIMEOUT_SECONDS = 12.0
UI_WAIT_MAX_SECONDS = 20.0


def env_port(name: str, default: int, environ: Mapping[str, str] | None = None) -> int:
    """TCP port from the environment. Raises ValueError for anything outside 1-65535."""
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a TCP port") from exc
    if port < 1 or port > 65535:
        raise ValueError(f"{name} must be a TCP port")
    return port


def console_port(environ: Mapping[str, str] | None = None) -> int:
    return env_port("VIEWLIFT_PORT", CONSOLE_PORT, environ)


def mcp_port(environ: Mapping[str, str] | None = None) -> int:
    return env_port("VIEWLIFT_AI_MCP_PORT", MCP_PORT, environ)


def ingest_port(environ: Mapping[str, str] | None = None) -> int:
    return env_port("VIEWLIFT_INGEST_PORT", INGEST_PORT, environ)


def limits() -> dict[str, float | int]:
    """Values the dashboard needs so its timeouts and caps match the server."""
    return {
        "max_body_bytes": MAX_BODY_BYTES,
        "job_wait_seconds": JOB_WAIT_SECONDS,
        "job_max_wait_seconds": JOB_MAX_WAIT_SECONDS,
        "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "ui_wait_max_seconds": UI_WAIT_MAX_SECONDS,
        "console_port": console_port(),
        "mcp_port": mcp_port(),
        "ingest_port": ingest_port(),
    }
