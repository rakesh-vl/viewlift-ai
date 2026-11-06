"""MCP server. Requires the optional `mcp` extra. Tools live in tools.py."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP, Image

from viewlift_ai.mcp_server import tools
from viewlift_ai.mcp_server.client import ConsoleClient
from viewlift_ai.mcp_server.runtime import transport_choice

mcp = FastMCP(
    "viewlift-ai",
    instructions=(
        "Local Viewlift AI debug console. Several apps can share one device. "
        "Call list_devices or list_apps, then pass that app's client_id. "
        "Never assume the most recent app. Mutating tools require confirm=true. "
        "Do not enable proxy rewrite. Network profiles throttle every proxied app. "
        "Location uses a paired hardware UDID, not an app device_key. "
        "Replay and raw flow bodies need the console reveal session to be unlocked already."
    ),
)


def _api() -> ConsoleClient:
    return ConsoleClient()


@mcp.tool()
def list_devices() -> dict[str, Any]:
    """Group apps by device. One phone or simulator can host several apps."""
    return tools.list_devices(_api())


@mcp.tool()
def list_apps(
    device_key: str = "",
    bundle_id: str = "",
    online_only: bool = False,
) -> dict[str, Any]:
    """List apps. Optional filters: device_key, bundle_id, online_only."""
    return tools.list_apps(
        _api(),
        device_key=device_key,
        bundle_id=bundle_id,
        online_only=online_only,
    )


@mcp.tool()
def get_app(client_id: str) -> dict[str, Any]:
    """Identity, capabilities, url schemes, and frameworks for one app."""
    return tools.get_app(_api(), client_id)


@mcp.tool()
def list_flows(
    client_id: str,
    method: str = "",
    host: str = "",
    status: int | None = None,
    text: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Redacted HTTP summaries for one app. client_id is required."""
    return tools.list_flows(
        _api(),
        client_id=client_id,
        method=method,
        host=host,
        status=status,
        text=text,
        limit=limit,
    )


@mcp.tool()
def get_flow(flow_id: str, client_id: str, reveal: bool = False) -> dict[str, Any]:
    """One flow for that app. Raw bytes only when reveal is already unlocked."""
    return tools.get_flow(_api(), flow_id=flow_id, client_id=client_id, reveal=reveal)


@mcp.tool()
def list_logs(
    client_id: str,
    level: str = "",
    subsystem: str = "",
    text: str = "",
    process: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """SDK logs for one app, plus syslog lines that mention its bundle id or executable."""
    return tools.list_logs(
        _api(),
        client_id=client_id,
        level=level,
        subsystem=subsystem,
        text=text,
        process=process,
        limit=limit,
    )


@mcp.tool()
def list_packages(client_id: str) -> dict[str, Any]:
    """Swift packages for one app, found from its Xcode build."""
    return tools.list_packages(_api(), client_id)


@mcp.tool()
def list_debug_events(client_id: str, module: str = "", limit: int = 50) -> dict[str, Any]:
    """Debug events posted by one app."""
    return tools.list_debug_events(_api(), client_id=client_id, module=module, limit=limit)


@mcp.tool()
def app_inspect(
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | list[Any]:
    """Run one read-only on-device command and wait for the result. client_id is required.

    command: performance.snapshot, memory.snapshot, memory.graph, memory.leaks, memory.heap,
    memory.live_start, memory.live_stop, ui.hierarchy,
    ui.describe, ui.screenshot, swiftui.renders, assets.audit, accessibility.audit, storage.defaults,
    storage.keychain_metadata, storage.files, storage.file_read, storage.sqlite_schema,
    storage.sqlite_query, feature_flags.list, runtime.search, runtime.describe, macro.list,
    bug.capture, ott.snapshot, hang.list, crash.list.
    ui.screenshot and bug.capture also return a PNG.
    """
    result = tools.app_inspect(_api(), client_id=client_id, command=command, payload=payload)
    cleaned, image = tools.split_image(result)
    if image is None:
        return cleaned
    return [cleaned, Image(data=image, format="png")]


@mcp.tool()
def app_mutate(
    client_id: str,
    command: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Change one running app. confirm must be true. Does not enable proxy rewrite.

    command: ui.highlight, ui.edit, storage.defaults_set, storage.file_write,
    storage.snapshot_create, storage.snapshot_restore, feature_flags.set, feature_flags.reset,
    macro.record_start, macro.record_stop, macro.replay, push.local.
    """
    return tools.app_mutate(
        _api(),
        client_id=client_id,
        command=command,
        payload=payload,
        confirm=confirm,
    )


@mcp.tool()
def set_network_profile(profile: str, confirm: bool = False) -> dict[str, Any]:
    """Apply wifi, 4g, 3g, edge, gprs, offline, or off. Throttles every proxied app."""
    return tools.set_network_profile(_api(), profile=profile, confirm=confirm)


@mcp.tool()
def open_deeplink(url: str, client_id: str, confirm: bool = False) -> dict[str, Any]:
    """Open a URL on the simulator for this app. Uses that app's device_key as the UDID."""
    return tools.open_deeplink(_api(), url=url, client_id=client_id, confirm=confirm)


@mcp.tool()
def set_location(
    udid: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    clear: bool = False,
    confirm: bool = False,
) -> dict[str, Any]:
    """Simulate GPS on a paired iPhone. udid is a hardware id, not device_key."""
    return tools.set_location(
        _api(),
        udid=udid,
        latitude=latitude,
        longitude=longitude,
        clear=clear,
        confirm=confirm,
    )


@mcp.tool()
def replay_flow(flow_id: str, confirm: bool = False) -> dict[str, Any]:
    """Resend one captured request. Fails unless reveal is already unlocked. Does not unlock it."""
    return tools.replay_flow(_api(), flow_id=flow_id, confirm=confirm)


def main() -> None:
    try:
        transport, host, port = transport_choice()
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if port is not None:
        mcp.settings.host = host
        mcp.settings.port = port
    mcp.run(transport=transport)  # type: ignore[arg-type]
