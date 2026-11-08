"""Prefire-style previews: list #Preview in source, render registered previews inside the running
app, and compare renders with stored baselines."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai.mcp_server import tools
from viewlift_ai.mcp_server.workflows.common import (
    READ,
    WRITE,
    RunFn,
    api,
    compact,
    host,
    sdk,
    sdk_result,
    split_sdk_image,
)

MAX_SNAPSHOTS = 40


def _render_payload(
    name: str, state: str, color_scheme: str, width: float, height: float, scale: float
) -> dict[str, Any]:
    return compact(
        {
            "name": name,
            "state": state,
            "color_scheme": color_scheme,
            "width": width or None,
            "height": height or None,
            "scale": scale or None,
        }
    )


def _suite(client_id: str, suite: str) -> str:
    if suite:
        return suite
    listing = tools.list_apps(api())
    for app in listing.get("apps", []) if isinstance(listing, dict) else []:
        if app.get("id") == client_id and app.get("bundle_id"):
            return f"{app['bundle_id']}-{app.get('platform') or 'ios'}"
    return client_id


def snapshot_all(
    client_id: str,
    names: list[str],
    color_schemes: list[str],
    suite: str,
    tolerance: int,
    max_changed_percent: float,
) -> dict[str, Any]:
    listing = sdk(client_id, "preview.list", {}, mutating=False)
    inner = sdk_result(listing)
    if not isinstance(inner, dict):
        return listing
    previews = [item for item in inner.get("previews", []) if isinstance(item, dict)]
    if names:
        previews = [item for item in previews if item.get("name") in names]
    suite_key = _suite(client_id, suite)
    rows = []
    for preview in previews[:MAX_SNAPSHOTS]:
        states = preview.get("states") or [""]
        for state in states:
            for scheme in color_schemes or ["light"]:
                payload = _render_payload(str(preview.get("name")), str(state), scheme, 0, 0, 0)
                rendered = sdk(client_id, "preview.render", payload, mutating=False)
                image = sdk_result(rendered)
                variant = f"{state or 'default'}-{scheme}"
                if not isinstance(image, dict) or not image.get("base64"):
                    rows.append(
                        {
                            "name": preview.get("name"),
                            "variant": variant,
                            "status": "error",
                            "detail": rendered.get("detail") or (image or {}).get("error"),
                        }
                    )
                    continue
                compared = host(
                    "preview_compare",
                    {
                        "name": preview.get("name"),
                        "variant": variant,
                        "suite": suite_key,
                        "base64": image["base64"],
                        "tolerance": tolerance,
                        "maxChangedPercent": max_changed_percent,
                    },
                )
                compared.pop("ok", None)
                rows.append(compared)
    counts: dict[str, int] = {}
    for row in rows:
        counts[str(row.get("status"))] = counts.get(str(row.get("status")), 0) + 1
    return {
        "suite": suite_key,
        "counts": counts,
        "results": rows,
        "next": "Check changed renders with render_preview; approve_preview_baseline accepts them.",
    }


def register(mcp: FastMCP, run: RunFn) -> None:
    @mcp.tool(annotations=READ)
    def list_previews(path: str = "", filter: str = "") -> dict[str, Any]:  # noqa: A002
        """#Preview and PreviewProvider declarations in Swift source under the project roots."""
        args = {"path": path, "filter": filter}
        return run("list_previews", args, lambda: host("list_previews", args))

    @mcp.tool(annotations=READ)
    def list_app_previews(client_id: str) -> dict[str, Any]:
        """Previews the running app registered with ViewliftPreviews.register, with states."""
        return run(
            "list_app_previews",
            {"client_id": client_id},
            lambda: sdk(client_id, "preview.list", {}, mutating=False),
        )

    @mcp.tool(annotations=READ)
    def render_preview(
        client_id: str,
        name: str,
        state: str = "",
        color_scheme: str = "light",
        width: float = 0,
        height: float = 0,
        scale: float = 0,
    ) -> dict[str, Any] | list[Any]:
        """Render one registered preview offscreen in the running app and return the PNG.

        Works on simulators, iPhone, iPad, and Apple TV. Size defaults to the screen width
        and the view's fitting height.
        """
        payload = _render_payload(name, state, color_scheme, width, height, scale)
        result = run(
            "render_preview",
            {"client_id": client_id, **payload},
            lambda: sdk(client_id, "preview.render", payload, mutating=False),
        )
        return split_sdk_image(result)

    @mcp.tool(annotations=READ)
    def snapshot_previews(
        client_id: str,
        names: list[str] | None = None,
        color_schemes: list[str] | None = None,
        suite: str = "",
        tolerance: int = 8,
        maxChangedPercent: float = 0.0,
    ) -> dict[str, Any]:
        """Render every registered preview (or names) and diff with stored baselines.

        First run records baselines. Status per render: recorded, passed, changed, or error.
        """
        args = {
            "client_id": client_id,
            "names": names or [],
            "color_schemes": color_schemes or ["light"],
            "suite": suite,
        }
        return run(
            "snapshot_previews",
            args,
            lambda: snapshot_all(
                client_id,
                names or [],
                color_schemes or ["light"],
                suite,
                tolerance,
                maxChangedPercent,
            ),
        )

    @mcp.tool(annotations=WRITE)
    def approve_preview_baseline(
        client_id: str = "",
        suite: str = "",
        names: list[dict[str, str]] | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Accept the latest renders as baselines: all, or [{"name", "variant"}]. confirm=true."""
        suite_key = _suite(client_id, suite) if client_id or suite else ""
        args = {"suite": suite_key, "names": names or []}
        return run(
            "approve_preview_baseline",
            {**args, "confirm": confirm},
            lambda: host("approve_preview_baseline", args, mutating=True, confirm=confirm),
        )
