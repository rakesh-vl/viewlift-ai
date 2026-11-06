"""How the MCP process should listen. No MCP SDK import."""

from __future__ import annotations

import os
import socket
from collections.abc import Mapping

DEFAULT_HTTP_PORT = 8742
HTTP_PATH = "/mcp"


def transport_choice(
    environ: Mapping[str, str] | None = None,
) -> tuple[str, str, int | None]:
    """Return transport, loopback host, and port. Port is None for stdio."""
    env = os.environ if environ is None else environ
    transport = env.get("VIEWLIFT_AI_MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        return "stdio", "", None
    if transport != "streamable-http":
        raise ValueError(f"Unsupported VIEWLIFT_AI_MCP_TRANSPORT: {transport}")
    raw = env.get("VIEWLIFT_AI_MCP_PORT", str(DEFAULT_HTTP_PORT))
    try:
        port = int(raw)
    except ValueError as exc:
        raise ValueError("VIEWLIFT_AI_MCP_PORT must be a TCP port") from exc
    if port < 1 or port > 65535:
        raise ValueError("VIEWLIFT_AI_MCP_PORT must be a TCP port")
    return transport, "127.0.0.1", port


def http_port(environ: Mapping[str, str] | None = None) -> int:
    """Port the start script gives the HTTP MCP server."""
    env = dict(os.environ if environ is None else environ)
    env["VIEWLIFT_AI_MCP_TRANSPORT"] = "streamable-http"
    try:
        _transport, _host, port = transport_choice(env)
    except ValueError:
        return DEFAULT_HTTP_PORT
    return port or DEFAULT_HTTP_PORT


def is_listening(port: int, *, timeout: float = 0.2) -> bool:
    """True when something accepts TCP on 127.0.0.1 at that port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False
