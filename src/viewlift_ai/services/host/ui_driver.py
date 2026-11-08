"""Simulator UI input for apps without the SDK, through AXe or idb, plus element summaries.

Apps that run ViewliftNetworkCapture get UI input through SDK commands instead (see the MCP ui
workflow). Here, taps by label or id are resolved from the simulator's accessibility tree, so an
agent never has to guess coordinates.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host import simctl
from viewlift_ai.services.host.runner import HostError, which

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_BUTTONS = {"home", "lock", "side-button", "siri", "apple-pay"}
_IDB_BUTTONS = {
    "home": "HOME",
    "lock": "LOCK",
    "side-button": "SIDE_BUTTON",
    "siri": "SIRI",
    "apple-pay": "APPLE_PAY",
}


def _driver() -> str:
    if which("axe"):
        return "axe"
    if which("idb"):
        return "idb"
    raise HostError(
        "No simulator UI driver. Install AXe (`brew install cameroncooke/axe/axe`) or idb, "
        "or run the app with the ViewliftNetworkCapture SDK and pass client_id."
    )


def normalize_axe(nodes: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(items: Any) -> None:
        for node in items if isinstance(items, list) else [items]:
            if not isinstance(node, dict):
                continue
            frame = node.get("frame") or {}
            role = str(node.get("role_description") or node.get("type") or node.get("role") or "")
            out.append(
                {
                    "role": role.lower().replace("ax", "", 1)
                    if role.startswith("AX")
                    else role.lower(),
                    "label": node.get("AXLabel") or node.get("label") or "",
                    "identifier": node.get("AXUniqueId") or node.get("identifier") or "",
                    "value": node.get("AXValue") or node.get("value") or "",
                    "frame": {
                        "x": frame.get("x", 0),
                        "y": frame.get("y", 0),
                        "width": frame.get("width", 0),
                        "height": frame.get("height", 0),
                    },
                    "enabled": node.get("enabled", True),
                }
            )
            walk(node.get("children") or [])

    walk(nodes)
    return [item for item in out if item["label"] or item["identifier"] or item["value"]]


def screen_map(elements: list[dict[str, Any]], *, screen: str = "") -> dict[str, Any]:
    """A few lines an agent can read instead of a full tree or a screenshot."""
    buttons, fields, texts, other = [], [], [], []
    for item in elements:
        role = str(item.get("role", "")).lower().replace(" ", "")
        name = item.get("label") or item.get("identifier") or item.get("value")
        if not name:
            continue
        label = str(name)[:40]
        if role in {"textfield", "securetextfield", "searchfield", "textview"}:
            fields.append(label)
        elif role in {
            "button",
            "link",
            "tab",
            "cell",
            "switch",
            "toggle",
            "menuitem",
            "segmentedcontrol",
        }:
            buttons.append(label)
        elif role in {"statictext", "text", "label", "heading"}:
            texts.append(label)
        else:
            other.append(label)
    lines = []
    if screen:
        lines.append(f"Screen: {screen}")
    if buttons:
        lines.append(f"Tappable ({len(buttons)}): " + ", ".join(list(dict.fromkeys(buttons))[:15]))
    if fields:
        lines.append(f"Inputs ({len(fields)}): " + ", ".join(list(dict.fromkeys(fields))[:8]))
    if texts:
        lines.append(f"Text ({len(texts)}): " + ", ".join(list(dict.fromkeys(texts))[:10]))
    if other:
        lines.append(f"Other ({len(other)}): " + ", ".join(list(dict.fromkeys(other))[:6]))
    return {"summary": "\n".join(lines) or "No labelled elements.", "element_count": len(elements)}


def find_elements(elements: list[dict[str, Any]], query: dict[str, Any]) -> list[dict[str, Any]]:
    label = str(query.get("label") or "").lower()
    identifier = str(query.get("identifier") or query.get("id") or "")
    role = str(query.get("role") or "").lower()
    text = str(query.get("text") or "").lower()
    out = []
    for item in elements:
        if identifier and item.get("identifier") != identifier:
            continue
        if label and str(item.get("label", "")).lower() != label:
            continue
        if role and role not in str(item.get("role", "")).lower():
            continue
        if (
            text
            and text
            not in " ".join(
                str(item.get(key, "")) for key in ("label", "identifier", "value")
            ).lower()
        ):
            continue
        out.append(item)
    return out


def center(item: dict[str, Any]) -> tuple[float, float]:
    frame = item.get("frame") or {}
    return (
        float(frame.get("x", 0)) + float(frame.get("width", 0)) / 2,
        float(frame.get("y", 0)) + float(frame.get("height", 0)) / 2,
    )


class SimulatorUI:
    def __init__(self, ctx: HostContext) -> None:
        self.ctx = ctx

    def _udid(self, args: dict[str, Any]) -> str:
        return simctl.resolve_simulator(self.ctx, args)["udid"]

    def elements(self, udid: str) -> list[dict[str, Any]]:
        driver = _driver()
        if driver == "axe":
            result = self.ctx.run_ok(["axe", "describe-ui", "--udid", udid], timeout=30)
        else:
            result = self.ctx.run_ok(
                ["idb", "ui", "describe-all", "--udid", udid, "--json"], timeout=30
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HostError("UI driver did not return JSON.") from exc
        return normalize_axe(data)

    def describe(self, args: dict[str, Any]) -> dict[str, Any]:
        udid = self._udid(args)
        elements = self.elements(udid)
        if args.get("summary"):
            return {"udid": udid, **screen_map(elements)}
        query = {
            key: args.get(key) for key in ("label", "identifier", "role", "text") if args.get(key)
        }
        if query:
            elements = find_elements(elements, query)
        limit = max(1, min(int(args.get("limit", 150)), 600))
        return {
            "udid": udid,
            "elements": elements[:limit],
            "count": len(elements),
            "driver": _driver(),
        }

    def _point(self, udid: str, args: dict[str, Any]) -> tuple[float, float, dict[str, Any] | None]:
        query = {
            key: args.get(key) for key in ("label", "identifier", "role", "text") if args.get(key)
        }
        if query:
            matches = find_elements(self.elements(udid), query)
            if not matches:
                raise HostError(f"No element matches {query}. Call snapshot_ui.")
            index = int(args.get("index", 0))
            if index >= len(matches):
                raise HostError(f"Only {len(matches)} elements match.")
            x, y = center(matches[index])
            return x, y, matches[index]
        if "x" in args and "y" in args:
            return float(args["x"]), float(args["y"]), None
        raise HostError("Pass label, identifier, role, or text; or x and y from snapshot_ui.")

    def act(self, args: dict[str, Any]) -> dict[str, Any]:
        udid = self._udid(args)
        driver = _driver()
        action = str(args.get("action") or "")
        out: dict[str, Any] = {"udid": udid, "action": action, "driver": driver}
        argv: list[str]
        if action in {"tap", "long_press"}:
            x, y, element = self._point(udid, args)
            out["point"] = [round(x, 1), round(y, 1)]
            if element:
                out["element"] = {k: element.get(k) for k in ("role", "label", "identifier")}
            hold = str(float(args.get("duration", 1.0)))
            if driver == "axe" and action == "long_press":
                argv = [
                    "axe",
                    "touch",
                    "-x",
                    str(x),
                    "-y",
                    str(y),
                    "--down",
                    "--up",
                    "--delay",
                    hold,
                    "--udid",
                    udid,
                ]
            elif driver == "axe":
                argv = ["axe", "tap", "-x", str(x), "-y", str(y), "--udid", udid]
            else:
                argv = ["idb", "ui", "tap", str(x), str(y), "--udid", udid]
                if action == "long_press":
                    argv += ["--duration", hold]
        elif action in {"swipe", "drag"}:
            try:
                coords = [str(float(args[key])) for key in ("startX", "startY", "endX", "endY")]
            except (KeyError, TypeError, ValueError) as exc:
                raise HostError("swipe needs startX, startY, endX, endY.") from exc
            duration = str(float(args.get("duration", 0.3 if action == "swipe" else 1.0)))
            if driver == "axe":
                argv = [
                    "axe",
                    "swipe",
                    "--start-x",
                    coords[0],
                    "--start-y",
                    coords[1],
                    "--end-x",
                    coords[2],
                    "--end-y",
                    coords[3],
                    "--duration",
                    duration,
                    "--udid",
                    udid,
                ]
            else:
                argv = ["idb", "ui", "swipe", *coords, "--duration", duration, "--udid", udid]
        elif action == "gesture":
            preset = str(args.get("preset") or "")
            allowed = {
                "scroll-up",
                "scroll-down",
                "scroll-left",
                "scroll-right",
                "swipe-from-left-edge",
                "swipe-from-right-edge",
                "swipe-from-top-edge",
                "swipe-from-bottom-edge",
            }
            if preset not in allowed:
                raise HostError(f"preset is one of {', '.join(sorted(allowed))}.")
            if driver != "axe":
                raise HostError("Gesture presets need AXe.")
            argv = ["axe", "gesture", preset, "--udid", udid]
        elif action == "touch":
            x, y, _element = self._point(udid, args)
            phase = [flag for flag in ("--down", "--up") if args.get(flag.strip("-"))] or [
                "--down",
                "--up",
            ]
            if driver != "axe":
                raise HostError("touch down/up needs AXe.")
            argv = ["axe", "touch", "-x", str(x), "-y", str(y), *phase, "--udid", udid]
        elif action == "type_text":
            text = str(args.get("text") or "")
            if not text or len(text) > 2000:
                raise HostError("text is required (max 2000 chars).")
            argv = (
                ["axe", "type", text, "--udid", udid]
                if driver == "axe"
                else ["idb", "ui", "text", text, "--udid", udid]
            )
            out["chars"] = len(text)
        elif action == "key_press":
            code = int(args.get("keyCode", -1))
            if not 0 <= code <= 255:
                raise HostError("keyCode is a HID usage code 0-255.")
            argv = (
                ["axe", "key", str(code), "--udid", udid]
                if driver == "axe"
                else ["idb", "ui", "key", str(code), "--udid", udid]
            )
        elif action == "key_sequence":
            codes = [int(item) for item in args.get("keyCodes") or []]
            if not codes or any(not 0 <= code <= 255 for code in codes):
                raise HostError("keyCodes is a list of HID usage codes.")
            if driver == "axe":
                argv = [
                    "axe",
                    "key-sequence",
                    "--keycodes",
                    ",".join(map(str, codes)),
                    "--udid",
                    udid,
                ]
            else:
                argv = ["idb", "ui", "key-sequence", *map(str, codes), "--udid", udid]
        elif action == "button":
            name = str(args.get("button") or "").lower()
            if name not in _BUTTONS:
                raise HostError(f"button is one of {', '.join(sorted(_BUTTONS))}.")
            argv = (
                ["axe", "button", name, "--udid", udid]
                if driver == "axe"
                else ["idb", "ui", "button", _IDB_BUTTONS[name], "--udid", udid]
            )
        else:
            raise HostError("Unknown UI action.")
        self.ctx.run_ok(argv, timeout=30)
        return out


def register(toolkit: HostToolkit) -> None:
    ui = SimulatorUI(toolkit.ctx)
    toolkit.add("sim_ui_describe", ui.describe)
    toolkit.add("sim_ui_action", ui.act, mutating=True)
