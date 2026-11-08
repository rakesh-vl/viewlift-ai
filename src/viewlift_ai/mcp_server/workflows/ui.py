"""UI automation by meaning: accessibility snapshots, screen summaries, taps, typing, gestures.

Pass client_id for an app that runs the SDK (simulator, iPhone, iPad, or Apple TV), or for a
UI runner (ui_runner_start), which drives any app through XCUITest on a simulator or device.
Without one, the simulator tools drive the screen through AXe or idb. When the SDK cannot make
a gesture from inside the app (drag, raw touch, hardware button), the call goes to a running
UI runner for the same app and device. Elements are found by label, identifier, role, or text,
so agents do not have to guess coordinates.
"""

from __future__ import annotations

import time
from typing import Any

from mcp.server.fastmcp import FastMCP

from viewlift_ai import defaults
from viewlift_ai.mcp_server import guard, tools
from viewlift_ai.mcp_server.client import ConsoleError
from viewlift_ai.mcp_server.workflows import common
from viewlift_ai.mcp_server.workflows.common import (
    READ,
    WRITE,
    RunFn,
    compact,
    host,
    sdk,
    sdk_result,
    target_args,
)
from viewlift_ai.services.host.ui_driver import screen_map as summarize

MAX_WAIT_SECONDS = defaults.UI_WAIT_MAX_SECONDS
SDK_WAIT_SLICE = 8.0
_BATCH_TOOLS = frozenset(
    {
        "tap",
        "long_press",
        "swipe",
        "drag",
        "gesture",
        "touch",
        "type_text",
        "key_press",
        "key_sequence",
        "button",
        "wait_for_ui",
        "tv_remote",
        "sleep",
    }
)


def _query(label: str, identifier: str, text: str, role: str, view_id: str = "") -> dict[str, Any]:
    return compact(
        {"label": label, "identifier": identifier, "text": text, "role": role, "view_id": view_id}
    )


def _sim(simulatorId: str, simulatorName: str) -> dict[str, Any]:
    return compact({"simulatorId": simulatorId, "simulatorName": simulatorName})


def _gate(confirm: bool) -> dict[str, Any] | None:
    if refusal := guard.read_only_refusal():
        return refusal
    if confirm is not True:
        return {"ok": False, "detail": "confirm must be true"}
    return None


def _sdk_elements(client_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    result = sdk(client_id, "ui.accessibility_tree", payload, mutating=False)
    return result, sdk_result(result)


def snapshot(
    client_id: str, sim: dict[str, Any], query: dict[str, Any], limit: int
) -> dict[str, Any]:
    if client_id:
        command = "ui.find" if query else "ui.accessibility_tree"
        return sdk(client_id, command, {**query, "limit": limit}, mutating=False)
    return host("sim_ui_describe", {**sim, **query, "limit": limit})


def screen_summary(client_id: str, sim: dict[str, Any]) -> dict[str, Any]:
    if not client_id:
        return host("sim_ui_describe", {**sim, "summary": True})
    raw, inner = _sdk_elements(client_id, {"limit": 400})
    if not isinstance(inner, dict):
        return raw
    elements = inner.get("elements") if isinstance(inner.get("elements"), list) else []
    out = summarize(elements, screen=str(inner.get("screen") or ""))
    if inner.get("focused"):
        out["focused"] = inner["focused"]
    return out


def _is_runner(client: dict[str, Any]) -> bool:
    capabilities = client.get("capabilities")
    return isinstance(capabilities, dict) and capabilities.get("runner") == "available"


def runner_for(client: dict[str, Any], clients: list[dict[str, Any]]) -> str | None:
    """The online UI runner driving the same app on the same simulator or device."""
    if _is_runner(client):
        return str(client.get("id"))
    matches = []
    for other in clients:
        if not _is_runner(other) or not other.get("online"):
            continue
        if other.get("bundle_id") != client.get("bundle_id"):
            continue
        if client.get("is_simulator"):
            same = other.get("device_key") == f"runner:{client.get('device_key')}"
        elif client.get("device_hint") and other.get("device_hint"):
            same = other.get("device_hint") == client.get("device_hint")
        else:
            same = not other.get("is_simulator") and (
                other.get("device_model") == client.get("device_model")
            )
        if same:
            matches.append(str(other.get("id")))
    return matches[0] if len(matches) == 1 else None


def _fallback(name: str, client_id: str, command: str | None) -> tuple[str | None, dict[str, Any]]:
    """A runner client for a gesture the SDK cannot make, or the refusal to return."""
    try:
        clients = tools._apps(common.api())
    except ConsoleError as exc:
        return None, {"ok": False, "status": exc.status, "detail": exc.detail}
    client = next((item for item in clients if item.get("id") == client_id), None)
    runner = runner_for(client, clients) if client and command else None
    if runner:
        return runner, {}
    hint = "start ui_runner_start for this app and device" if command else "omit client_id"
    simulator = (client or {}).get("is_simulator")
    where = f"; or pass simulatorId={client.get('device_key')}" if client and simulator else ""
    return None, {"ok": False, "detail": f"{name} is outside the SDK: {hint}{where}."}


def action(
    name: str,
    client_id: str,
    sim: dict[str, Any],
    sdk_command: str | None,
    sdk_payload: dict[str, Any],
    host_args: dict[str, Any] | None,
    confirm: bool,
    runner_command: str | None = None,
) -> dict[str, Any]:
    if blocked := _gate(confirm):
        return blocked
    if client_id:
        if sdk_command is None:
            runner, refusal = _fallback(name, client_id, runner_command)
            if runner is None or runner_command is None:
                return refusal
            payload = compact({**sdk_payload, **(host_args or {})})
            return sdk(runner, runner_command, payload, mutating=True, confirm=True)
        return sdk(client_id, sdk_command, compact(sdk_payload), mutating=True, confirm=True)
    if host_args is None:
        return {"ok": False, "detail": f"{name} needs an app with the SDK; pass client_id."}
    return host("sim_ui_action", {**sim, **host_args, "action": name}, mutating=True, confirm=True)


def _sdk_wait(client_id: str, query: dict[str, Any], gone: bool, deadline: float) -> dict[str, Any]:
    """The SDK waits in slices shorter than the debug invoke timeout."""
    while True:
        remaining = deadline - time.monotonic()
        payload = {**query, "gone": gone, "timeout": max(0.2, min(remaining, SDK_WAIT_SLICE))}
        result = sdk(client_id, "ui.wait_for", payload, mutating=False)
        inner = sdk_result(result)
        timed_out = isinstance(inner, dict) and inner.get("detail") == "timed out"
        if not timed_out or deadline - time.monotonic() <= 0.2:
            return result


def wait_for(
    client_id: str, sim: dict[str, Any], query: dict[str, Any], gone: bool, timeout_s: float
) -> dict[str, Any]:
    timeout = max(0.5, min(float(timeout_s), MAX_WAIT_SECONDS))
    if not query:
        return {"ok": False, "detail": "Pass label, identifier, text, or role."}
    deadline = time.monotonic() + timeout
    started = time.monotonic()
    if client_id:
        return _sdk_wait(client_id, query, gone, deadline)
    while True:
        result = host("sim_ui_describe", {**sim, **query, "limit": 5})
        if result.get("ok") is False:
            return result
        found = bool(result.get("count"))
        if found != gone:
            elements = result.get("elements") or []
            return {
                "found": found,
                "element": elements[0] if elements else None,
                "waited_ms": int((time.monotonic() - started) * 1000),
            }
        if time.monotonic() >= deadline:
            return {
                "ok": False,
                "found": found,
                "detail": "timed out",
                "waited_ms": int(timeout * 1000),
            }
        time.sleep(0.5)


def register(mcp: FastMCP, run: RunFn) -> None:  # noqa: C901 - one block of tool definitions
    def logged(name: str, args: dict[str, Any], call: Any) -> Any:
        return run(name, compact(args), call)

    @mcp.tool(annotations=READ)
    def snapshot_ui(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        label: str = "",
        identifier: str = "",
        text: str = "",
        role: str = "",
        limit: int = 150,
    ) -> dict[str, Any]:
        """Accessibility elements on screen: role, label, identifier, value, frame (points).

        Filter with label, identifier, text, or role. Cheaper than a screenshot.
        """
        sim = _sim(simulatorId, simulatorName)
        query = _query(label, identifier, text, role)
        args = {"client_id": client_id, **sim, **query, "limit": limit}
        return logged("snapshot_ui", args, lambda: snapshot(client_id, sim, query, limit))

    @mcp.tool(annotations=READ)
    def screen_map(
        client_id: str = "", simulatorId: str = "", simulatorName: str = ""
    ) -> dict[str, Any]:
        """Three to five lines: screen name, tappable items, inputs, and visible text.

        Use this first to see a screen; it costs a few dozen tokens.
        """
        sim = _sim(simulatorId, simulatorName)
        return logged(
            "screen_map", {"client_id": client_id, **sim}, lambda: screen_summary(client_id, sim)
        )

    @mcp.tool(annotations=READ)
    def find_element(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        label: str = "",
        identifier: str = "",
        text: str = "",
        role: str = "",
    ) -> dict[str, Any]:
        """Elements matching an exact label, an identifier, a role, or text anywhere in them."""
        sim = _sim(simulatorId, simulatorName)
        query = _query(label, identifier, text, role)
        if not query:
            return {"ok": False, "detail": "Pass label, identifier, text, or role."}
        return logged(
            "find_element",
            {"client_id": client_id, **sim, **query},
            lambda: snapshot(client_id, sim, query, 25),
        )

    @mcp.tool(annotations=WRITE)
    def tap(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        label: str = "",
        identifier: str = "",
        text: str = "",
        role: str = "",
        view_id: str = "",
        index: int = 0,
        x: float | None = None,
        y: float | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Tap an element by label, identifier, text, role, or view_id (x/y as a last resort)."""
        sim = _sim(simulatorId, simulatorName)
        query = _query(label, identifier, text, role, view_id)
        point = compact({"x": x, "y": y})
        payload = {**query, **point, "index": index}
        args = {"client_id": client_id, **sim, **payload, "confirm": confirm}
        return logged(
            "tap",
            args,
            lambda: action("tap", client_id, sim, "ui.tap", payload, payload, confirm),
        )

    @mcp.tool(annotations=WRITE)
    def long_press(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        label: str = "",
        identifier: str = "",
        text: str = "",
        view_id: str = "",
        x: float | None = None,
        y: float | None = None,
        duration: float = 1.0,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Press and hold an element (context menus, drag handles)."""
        sim = _sim(simulatorId, simulatorName)
        payload = {
            **_query(label, identifier, text, "", view_id),
            **compact({"x": x, "y": y}),
            "duration": duration,
        }
        args = {"client_id": client_id, **sim, **payload, "confirm": confirm}
        return logged(
            "long_press",
            args,
            lambda: action(
                "long_press", client_id, sim, "ui.long_press", payload, payload, confirm
            ),
        )

    @mcp.tool(annotations=WRITE)
    def swipe(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        direction: str = "",
        identifier: str = "",
        view_id: str = "",
        startX: float | None = None,
        startY: float | None = None,
        endX: float | None = None,
        endY: float | None = None,
        duration: float = 0.3,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Swipe up, down, left, or right (SDK: scrolls the nearest scroll view), or between
        two points on a simulator."""
        sim = _sim(simulatorId, simulatorName)
        sdk_payload = compact(
            {"direction": direction, "identifier": identifier, "view_id": view_id}
        )
        coords = compact({"startX": startX, "startY": startY, "endX": endX, "endY": endY})
        host_args = {**coords, "duration": duration} if len(coords) == 4 else None
        args = {"client_id": client_id, **sim, **sdk_payload, **coords, "confirm": confirm}
        command = "ui.swipe" if direction else None
        return logged(
            "swipe",
            args,
            lambda: action(
                "swipe", client_id, sim, command, sdk_payload, host_args, confirm, "ui.drag"
            ),
        )

    @mcp.tool(annotations=WRITE)
    def drag(
        startX: float,
        startY: float,
        endX: float,
        endY: float,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        duration: float = 1.0,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Slow drag between two points (sliders, reordering). client_id uses the UI runner."""
        sim = _sim(simulatorId, simulatorName)
        host_args = {
            "startX": startX,
            "startY": startY,
            "endX": endX,
            "endY": endY,
            "duration": duration,
        }
        args = {"client_id": client_id, **sim, **host_args, "confirm": confirm}
        return logged(
            "drag",
            args,
            lambda: action("drag", client_id, sim, None, {}, host_args, confirm, "ui.drag"),
        )

    @mcp.tool(annotations=WRITE)
    def gesture(
        preset: str,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Preset gesture: scroll-up/down/left/right or swipe-from-left/right/top/bottom-edge."""
        sim = _sim(simulatorId, simulatorName)
        direction = preset.removeprefix("scroll-") if preset.startswith("scroll-") else ""
        sdk_payload = {"direction": direction, "scroll": True}
        command = "ui.scroll" if direction else None
        args = {"client_id": client_id, **sim, "preset": preset, "confirm": confirm}
        return logged(
            "gesture",
            args,
            lambda: action(
                "gesture", client_id, sim, command, sdk_payload, {"preset": preset}, confirm
            ),
        )

    @mcp.tool(annotations=WRITE)
    def touch(
        x: float,
        y: float,
        down: bool = True,
        up: bool = True,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Raw touch at a point. Simulator: down and/or up. client_id: a tap through the runner."""
        sim = _sim(simulatorId, simulatorName)
        host_args = {"x": x, "y": y, "down": down, "up": up}
        return logged(
            "touch",
            {"client_id": client_id, **sim, **host_args, "confirm": confirm},
            lambda: action("touch", client_id, sim, None, {}, host_args, confirm, "ui.touch"),
        )

    @mcp.tool(annotations=WRITE)
    def type_text(
        text: str,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        identifier: str = "",
        label: str = "",
        view_id: str = "",
        replace: bool = False,
        submit: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Type into the focused field, or into the field matched by identifier/label/view_id.

        Never type real passwords or tokens; use test accounts.
        """
        sim = _sim(simulatorId, simulatorName)
        payload = {
            "text": text,
            **_query(label, identifier, "", "", view_id),
            "replace": replace,
            "submit": submit,
        }
        args = {"client_id": client_id, **sim, "chars": len(text), "confirm": confirm}
        return logged(
            "type_text",
            args,
            lambda: action(
                "type_text", client_id, sim, "ui.type_text", payload, {"text": text}, confirm
            ),
        )

    @mcp.tool(annotations=WRITE)
    def key_press(
        client_id: str = "",
        key: str = "",
        keyCode: int | None = None,
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Press one key. SDK: key is return, delete, tab, or escape. Simulator: HID keyCode."""
        sim = _sim(simulatorId, simulatorName)
        host_args = {"keyCode": keyCode} if keyCode is not None else None
        args = {"client_id": client_id, **sim, "key": key, "keyCode": keyCode, "confirm": confirm}
        return logged(
            "key_press",
            args,
            lambda: action("key_press", client_id, sim, "ui.key", {"key": key}, host_args, confirm),
        )

    @mcp.tool(annotations=WRITE)
    def key_sequence(
        keyCodes: list[int], simulatorId: str = "", simulatorName: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Press several HID key codes in order on a simulator."""
        sim = _sim(simulatorId, simulatorName)
        host_args = {"keyCodes": keyCodes}
        return logged(
            "key_sequence",
            {**sim, **host_args, "confirm": confirm},
            lambda: action("key_sequence", "", sim, None, {}, host_args, confirm),
        )

    @mcp.tool(annotations=WRITE)
    def button(
        button: str,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Press a hardware button. Simulator: home, lock, side-button, siri, apple-pay.
        client_id (UI runner on a device): home, volume-up, volume-down."""
        sim = _sim(simulatorId, simulatorName)
        host_args = {"button": button}
        return logged(
            "button",
            {"client_id": client_id, **sim, **host_args, "confirm": confirm},
            lambda: action("button", client_id, sim, None, {}, host_args, confirm, "ui.button"),
        )

    @mcp.tool(annotations=WRITE)
    def tv_remote(
        press: str,
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        repeat: int = 1,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Apple TV remote: up, down, left, right, select, menu, playpause (home on simulators).

        With client_id the SDK or UI runner presses inside the app on a simulator or Apple TV.
        Without it, the keys go to a booted Apple TV simulator.
        """
        sim = _sim(simulatorId, simulatorName)
        payload = {"press": press, "repeat": max(1, min(repeat, 20))}
        args = {"client_id": client_id, **sim, **payload, "confirm": confirm}

        def press_remote() -> dict[str, Any]:
            if client_id:
                return action("tv_remote", client_id, {}, "ui.remote", payload, None, confirm)
            if blocked := _gate(confirm):
                return blocked
            return host("sim_tv_remote", {**sim, **payload}, mutating=True, confirm=True)

        return logged("tv_remote", args, press_remote)

    @mcp.tool(annotations=WRITE)
    def ui_runner_start(
        bundleId: str = "",
        client_id: str = "",
        udid: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        deviceId: str = "",
        developmentTeam: str = "",
        runnerBundleId: str = "",
        rebuild: bool = False,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Start the XCUITest UI runner for one app on a simulator, iPhone, iPad, or Apple TV.

        The first start builds the runner (minutes; devices need developmentTeam or the
        VIEWLIFT_DEVELOPMENT_TEAM setting). It registers as its own client in list_apps;
        pass that client_id to the UI tools. Needs the SDK receiver enabled on Setup.
        """
        routing = {
            "client_id": client_id,
            "udid": udid,
            "simulatorId": simulatorId,
            "simulatorName": simulatorName,
            "deviceId": deviceId,
        }
        more = compact(
            {
                "bundleId": bundleId,
                "developmentTeam": developmentTeam,
                "runnerBundleId": runnerBundleId,
                "rebuild": rebuild or None,
            }
        )

        def start() -> dict[str, Any]:
            if blocked := _gate(confirm):
                return blocked
            args, error = target_args(**routing)
            if error:
                return error
            return host("ui_runner_start", {**args, **more}, mutating=True, confirm=True)

        return logged("ui_runner_start", {**routing, **more, "confirm": confirm}, start)

    @mcp.tool(annotations=WRITE)
    def ui_runner_stop(
        udid: str = "", simulatorId: str = "", deviceId: str = "", confirm: bool = False
    ) -> dict[str, Any]:
        """Stop the UI runner on one simulator or device."""
        args = {"udid": udid, "simulatorId": simulatorId, "deviceId": deviceId}
        return logged(
            "ui_runner_stop",
            {**args, "confirm": confirm},
            lambda: host("ui_runner_stop", args, mutating=True, confirm=confirm),
        )

    @mcp.tool(annotations=READ)
    def ui_runner_status() -> dict[str, Any]:
        """Running UI runners with their client_id, target, and job."""
        return logged("ui_runner_status", {}, lambda: host("ui_runner_status", {}))

    @mcp.tool(annotations=READ)
    def wait_for_ui(
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        label: str = "",
        identifier: str = "",
        text: str = "",
        role: str = "",
        gone: bool = False,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        """Wait (max 20 s) until an element appears, or disappears with gone=true."""
        sim = _sim(simulatorId, simulatorName)
        query = _query(label, identifier, text, role)
        args = {"client_id": client_id, **sim, **query, "gone": gone, "timeout_s": timeout_s}
        return logged("wait_for_ui", args, lambda: wait_for(client_id, sim, query, gone, timeout_s))

    tools_by_name = {
        "tap": tap,
        "long_press": long_press,
        "swipe": swipe,
        "drag": drag,
        "gesture": gesture,
        "touch": touch,
        "type_text": type_text,
        "key_press": key_press,
        "key_sequence": key_sequence,
        "button": button,
        "wait_for_ui": wait_for_ui,
        "tv_remote": tv_remote,
    }

    @mcp.tool(annotations=WRITE)
    def batch(
        steps: list[dict[str, Any]],
        client_id: str = "",
        simulatorId: str = "",
        simulatorName: str = "",
        stopOnError: bool = True,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Run several UI steps on one screen flow: [{"tool": "tap", "args": {...}}, ...].

        Tools: tap, long_press, swipe, drag, gesture, touch, type_text, key_press, key_sequence,
        button, wait_for_ui, tv_remote, sleep (args.seconds max 5). One confirm covers the batch.
        """
        if blocked := _gate(confirm):
            return blocked
        if not steps or len(steps) > 30:
            return {"ok": False, "detail": "Pass 1 to 30 steps."}
        results = []
        for number, step in enumerate(steps, start=1):
            name = str(step.get("tool", "")) if isinstance(step, dict) else ""
            step_args = step.get("args") if isinstance(step, dict) else None
            step_args = dict(step_args) if isinstance(step_args, dict) else {}
            if name not in _BATCH_TOOLS:
                results.append(
                    {"step": number, "ok": False, "detail": f"{name} is not a batch tool"}
                )
                if stopOnError:
                    break
                continue
            if name == "sleep":
                time.sleep(max(0.0, min(float(step_args.get("seconds", 0.5)), 5.0)))
                results.append({"step": number, "tool": name, "ok": True})
                continue
            fn = tools_by_name[name]
            shared = {
                "client_id": client_id,
                "simulatorId": simulatorId,
                "simulatorName": simulatorName,
            }
            params = fn.__code__.co_varnames[: fn.__code__.co_argcount]
            for key, value in shared.items():
                if key in params and value and key not in step_args:
                    step_args[key] = value
            if "confirm" in params:
                step_args["confirm"] = True
            try:
                outcome = fn(**step_args)
            except TypeError as exc:
                outcome = {"ok": False, "detail": f"bad args: {exc}"}
            ok = not (isinstance(outcome, dict) and outcome.get("ok") is False)
            results.append({"step": number, "tool": name, "ok": ok, "result": outcome})
            if not ok and stopOnError:
                break
        return {"ok": all(item["ok"] for item in results), "steps": results}
