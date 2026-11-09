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
# How often a command waiter checks that its app is still online and not restarted.
COMMAND_WATCH_SLICE_SECONDS = 0.25
# Read commands are safe to run twice, so they are re-sent once when the app restarts mid-command.
COMMAND_READ_RETRIES = 1
# Live dashboard pages: refresh period, slowest retry period while the app is unreachable,
# and how often the heavier diagnostics badge refreshes.
LIVE_REFRESH_SECONDS = 2.0
LIVE_BACKOFF_MAX_SECONDS = 15.0
LIVE_DIAGNOSTICS_SECONDS = 10.0
# Idle dashboard sockets get a heartbeat this often.
# The browser replaces a socket after three missed beats.
LIVE_HEARTBEAT_SECONDS = 10.0


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


def env_seconds(
    name: str, default: float, environ: Mapping[str, str] | None = None, *, minimum: float = 0.1
) -> float:
    """Seconds from the environment. Raises ValueError for a non-number or a value below minimum."""
    env = os.environ if environ is None else environ
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number of seconds") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g} seconds")
    return value


def live_refresh_seconds(environ: Mapping[str, str] | None = None) -> float:
    return env_seconds("VIEWLIFT_LIVE_REFRESH_SECONDS", LIVE_REFRESH_SECONDS, environ, minimum=0.5)


def live_backoff_max_seconds(environ: Mapping[str, str] | None = None) -> float:
    return env_seconds(
        "VIEWLIFT_LIVE_BACKOFF_MAX_SECONDS", LIVE_BACKOFF_MAX_SECONDS, environ, minimum=1.0
    )


def live_diagnostics_seconds(environ: Mapping[str, str] | None = None) -> float:
    return env_seconds(
        "VIEWLIFT_LIVE_DIAGNOSTICS_SECONDS", LIVE_DIAGNOSTICS_SECONDS, environ, minimum=1.0
    )


def limits() -> dict[str, float | int]:
    """Values the dashboard needs so its timeouts and caps match the server."""
    return {
        "max_body_bytes": MAX_BODY_BYTES,
        "job_wait_seconds": JOB_WAIT_SECONDS,
        "job_max_wait_seconds": JOB_MAX_WAIT_SECONDS,
        "command_timeout_seconds": COMMAND_TIMEOUT_SECONDS,
        "live_refresh_seconds": live_refresh_seconds(),
        "live_backoff_max_seconds": live_backoff_max_seconds(),
        "live_diagnostics_seconds": live_diagnostics_seconds(),
        "ui_wait_max_seconds": UI_WAIT_MAX_SECONDS,
        "console_port": console_port(),
        "mcp_port": mcp_port(),
        "ingest_port": ingest_port(),
    }
