"""Localhost debug tools. Nothing here is injected into the iOS app."""

from __future__ import annotations

import asyncio
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

from viewlift_ai.services.rule_engine import RuleEngine, throttle_to_api

PROFILE_RULE_ID = "debug-network-profile"

# kbps is kilobits/s. Packet loss is listed for the UI only; the proxy has no loss model.
NETWORK_PROFILES: dict[str, dict[str, object]] = {
    "wifi": {
        "latency_ms": 2,
        "download_kbps": None,
        "upload_kbps": None,
        "offline": False,
        "loss_pct": 0,
    },
    "4g": {
        "latency_ms": 50,
        "download_kbps": 12_000,
        "upload_kbps": 12_000,
        "offline": False,
        "loss_pct": 1,
    },
    "3g": {
        "latency_ms": 200,
        "download_kbps": 1_600,
        "upload_kbps": 1_600,
        "offline": False,
        "loss_pct": 2,
    },
    "edge": {
        "latency_ms": 400,
        "download_kbps": 240,
        "upload_kbps": 240,
        "offline": False,
        "loss_pct": 5,
    },
    "gprs": {
        "latency_ms": 500,
        "download_kbps": 50,
        "upload_kbps": 50,
        "offline": False,
        "loss_pct": 10,
    },
    "offline": {
        "latency_ms": 0,
        "download_kbps": None,
        "upload_kbps": None,
        "offline": True,
        "loss_pct": 100,
    },
}

DIAGNOSTIC_QUERIES: dict[str, str] = {
    "layout": "Unable to simultaneously satisfy constraints",
    "hang": "Hang detected",
    "main-thread": "Main Thread Checker",
}

ANALYTICS_HOST_MARKERS: tuple[str, ...] = (
    "firebase",
    "amplitude",
    "mixpanel",
    "segment.io",
    "google-analytics",
    "app-measurement",
    "beacon",
    "appsflyer",
    "clevertap",
    "braze",
    "demdex",
    "omtrdc",
    "comscore",
    "conviva",
    "airship",
    "urbanairship",
    "facebook",
    "datadog",
)

_DEEPLINK = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://\S+$")
_UDID = re.compile(r"^(booted|[A-Za-z0-9-]{8,80})$")
_BLOCKED_SCHEMES = {"javascript", "file", "data", "about", "blob"}
_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "keep-alive"}

CommandRunner = Callable[..., Any]
LocationRunner = Callable[[str, float | None, float | None, bool], None]


@dataclass(frozen=True, slots=True)
class DebugModule:
    id: str
    group: str
    title: str
    summary: str
    status: str
    href: str | None = None
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "group": self.group,
            "title": self.title,
            "summary": self.summary,
            "status": self.status,
            "href": self.href,
            "note": self.note,
        }


_APP_ONLY = "Needs the iOS process. Not added to the app."

DEBUG_MODULES: tuple[DebugModule, ...] = (
    DebugModule(
        "network",
        "Connectivity",
        "Network Trace",
        "Captured HTTP list, detail, search.",
        "ready",
        "/",
    ),
    DebugModule(
        "interceptor",
        "Connectivity",
        "Interceptor",
        "Mock, block, redirect, and delay via Rules.",
        "ready",
        "/rules",
    ),
    DebugModule(
        "bad-network",
        "Connectivity",
        "Bad Network",
        "Wi-Fi, 4G, 3G, Edge, GPRS, offline presets.",
        "ready",
        "/debug#network",
    ),
    DebugModule(
        "waterfall",
        "Connectivity",
        "Network Waterfall",
        "Timeline of captured request durations.",
        "ready",
        "/debug#waterfall",
    ),
    DebugModule(
        "replay",
        "Connectivity",
        "Request Replay",
        "Resend a captured request from this Mac.",
        "ready",
        "/",
    ),
    DebugModule(
        "har",
        "Connectivity",
        "HAR Export",
        "Download HAR for one flow or the current filter.",
        "ready",
        "/",
    ),
    DebugModule(
        "performance",
        "Performance",
        "Performance Monitor",
        "CPU, FPS, and RAM graphs.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "hang",
        "Performance",
        "Hang Detector",
        "Device logs that mention a hang.",
        "ready",
        "/debug#diagnostics",
    ),
    DebugModule(
        "main-thread",
        "Performance",
        "Main Thread Checker",
        "Device logs from the main-thread checker.",
        "ready",
        "/debug#diagnostics",
    ),
    DebugModule(
        "leaks",
        "Performance",
        "Memory Leak Tracker",
        "Retain-cycle tracking.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "memory-graph",
        "Performance",
        "Memory Graph",
        "Heap snapshot diff.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "ui-inspector", "UI", "UI Inspector", "Live view properties.", "app-only", note=_APP_ONLY
    ),
    DebugModule(
        "hierarchy", "UI", "3D View Hierarchy", "Exploded view tree.", "app-only", note=_APP_ONLY
    ),
    DebugModule(
        "swiftui",
        "UI",
        "SwiftUI Render Tracker",
        "Per-view re-render counts.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "assets", "UI", "Asset Inspector", "Image size and scale audit.", "app-only", note=_APP_ONLY
    ),
    DebugModule(
        "a11y",
        "UI",
        "Accessibility Audit",
        "Labels and touch-target scan.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "layout",
        "UI",
        "Layout Conflict",
        "Auto Layout conflict lines from device logs.",
        "ready",
        "/debug#diagnostics",
    ),
    DebugModule(
        "storage",
        "Storage",
        "Storage Inspector",
        "UserDefaults, Keychain, sandbox, SQLite.",
        "app-only",
        note="Not opened from the dashboard. Keychain stays on the device.",
    ),
    DebugModule(
        "snapshot",
        "Storage",
        "State Snapshot",
        "Save and restore app defaults.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "console",
        "Toolkit",
        "Console Logger",
        "Level, subsystem, and text filters.",
        "ready",
        "/logs",
    ),
    DebugModule(
        "analytics",
        "Toolkit",
        "Analytics Interceptor",
        "Captured calls to known analytics hosts.",
        "ready",
        "/debug#analytics",
    ),
    DebugModule(
        "beacons",
        "OTT",
        "Beacons & VLAnalytics",
        "User/player beacons and registered analytics client network calls.",
        "ready",
        "/debug",
        note="Uses captured proxy/SDK flows. Device ott.snapshot lists registered clients.",
    ),
    DebugModule(
        "flags",
        "Toolkit",
        "Feature Flags",
        "Toggle flags inside the app.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "bug", "Toolkit", "Bug Reporter", "Annotated screenshots.", "app-only", note=_APP_ONLY
    ),
    DebugModule(
        "macro",
        "Toolkit",
        "Macro Recorder",
        "Record and replay touches.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "remote",
        "Toolkit",
        "Remote Server",
        "This dashboard is the remote inspector.",
        "ready",
        "/",
    ),
    DebugModule(
        "security",
        "Toolkit",
        "Security Audit",
        "Dashboard bind, reveal lock, and allowlist.",
        "ready",
        "/debug#security",
    ),
    DebugModule(
        "environment",
        "Toolkit",
        "Environment Swapper",
        "Simulated GPS on a paired iPhone. Locale stays on device.",
        "ready",
        "/debug#location",
    ),
    DebugModule(
        "runtime",
        "Toolkit",
        "Runtime Browser",
        "Objective-C class and method browser.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "push",
        "Toolkit",
        "Push Notification Tester",
        "Local or APNs pushes.",
        "app-only",
        note=_APP_ONLY,
    ),
    DebugModule(
        "deeplink",
        "Toolkit",
        "Deeplink Tester",
        "Open a URL on a booted simulator.",
        "ready",
        "/debug#deeplink",
    ),
    DebugModule(
        "ott",
        "OTT",
        "OTT Runtime Snapshot",
        "User, parental, plan, TVE, theme, modules, metadataMap.",
        "app-only",
        "/debug",
        note="Needs AppCMS debug provider wired into ViewliftNetworkCapture.",
    ),
    DebugModule(
        "hang-stacks",
        "OTT",
        "Hang Stacks",
        "Main-thread hang events with class/file/line frames.",
        "app-only",
        "/debug",
        note=_APP_ONLY,
    ),
    DebugModule(
        "crash-stacks",
        "OTT",
        "Crash / Exception Stacks",
        "In-process exceptions with symbolicated frames.",
        "app-only",
        "/debug",
        note=_APP_ONLY,
    ),
    DebugModule(
        "spm",
        "OTT",
        "SPM Packages",
        "Package.resolved pins from the AppCMS workspace.",
        "ready",
        "/debug",
    ),
)


def list_modules() -> list[dict[str, object]]:
    return [item.as_dict() for item in DEBUG_MODULES]


def list_network_profiles() -> list[dict[str, object]]:
    rows = []
    for name, spec in NETWORK_PROFILES.items():
        rows.append({"id": name, **spec, "packet_loss": "not applied"})
    return rows


def apply_network_profile(engine: RuleEngine, profile: str) -> dict[str, object]:
    name = (profile or "").strip().lower()
    if name in {"off", "clear"}:
        removed = engine.delete_throttle_if_present(PROFILE_RULE_ID)
        return {"ok": True, "profile": "off", "removed": removed, "rule": None}
    spec = NETWORK_PROFILES.get(name)
    if spec is None:
        known = ", ".join((*NETWORK_PROFILES.keys(), "off"))
        raise ValueError(f"Unknown profile. Use one of: {known}")
    rule = engine.upsert_throttle(
        PROFILE_RULE_ID,
        {
            "enabled": True,
            "host": "*",
            "latency_ms": spec["latency_ms"],
            "download_kbps": spec["download_kbps"],
            "upload_kbps": spec["upload_kbps"],
            "offline": spec["offline"],
        },
    )
    return {
        "ok": True,
        "profile": name,
        "rule": throttle_to_api(rule),
        "loss_pct": spec["loss_pct"],
        "packet_loss": "not applied",
    }


def validate_deeplink(url: str, udid: str) -> tuple[str, str]:
    target = (url or "").strip()
    device = (udid or "booted").strip() or "booted"
    scheme = target.split(":", 1)[0].lower()
    if scheme in _BLOCKED_SCHEMES:
        raise ValueError(f"Scheme {scheme} is blocked.")
    if not _DEEPLINK.fullmatch(target) or len(target) > 2048:
        raise ValueError("URL must be a scheme plus :// and contain no spaces.")
    if not _UDID.fullmatch(device):
        raise ValueError("UDID must be 'booted' or a simulator identifier.")
    return target, device


def open_deeplink(
    url: str, *, udid: str = "booted", runner: CommandRunner | None = None
) -> dict[str, object]:
    """Open a URL in a booted simulator. argv only, never a shell."""
    target, device = validate_deeplink(url, udid)
    argv = ["xcrun", "simctl", "openurl", device, target]
    run = runner or _subprocess_run
    try:
        completed = run(argv, check=False, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return {"ok": False, "detail": "xcrun not found."}
    except OSError as exc:
        return {"ok": False, "detail": str(exc)}
    stderr = (getattr(completed, "stderr", "") or "").strip()
    if getattr(completed, "returncode", 1) != 0:
        return {"ok": False, "detail": stderr or "simctl openurl failed."}
    return {"ok": True, "detail": f"Opened on simulator {device}.", "url": target}


def replay_http(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    opener: Callable[[str, str, dict[str, str], bytes | None], tuple[int, dict[str, str], bytes]]
    | None = None,
) -> dict[str, object]:
    """Send one captured request from this Mac. Caller must pass raw, unredacted bytes."""
    verb = (method or "GET").upper()
    if verb not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}:
        raise ValueError("Unsupported method.")
    target = (url or "").strip()
    if not target.startswith(("http://", "https://")) or len(target) > 4096:
        raise ValueError("Replay URL must be http or https.")
    if len(body) > 1_048_576:
        raise ValueError("Request body is over 1 MB.")
    safe = {k: v for k, v in headers.items() if k.lower() not in _HOP_HEADERS}
    send = opener or _urllib_open
    payload = body if verb not in {"GET", "HEAD"} else None
    status, resp_headers, resp_body = send(verb, target, safe, payload)
    return {
        "ok": True,
        "status": status,
        "headers": resp_headers,
        "body": resp_body[:65_536],
    }


def validate_coordinates(latitude: float, longitude: float) -> tuple[float, float]:
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise ValueError("Latitude must be -90..90 and longitude -180..180.")
    return latitude, longitude


def set_device_location(
    udid: str,
    latitude: float | None,
    longitude: float | None,
    *,
    clear: bool = False,
    streaming: bool = False,
    runner: LocationRunner | None = None,
) -> dict[str, object]:
    """Simulate GPS on a paired iPhone. Refuses while a log stream holds USB."""
    device = (udid or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{8,80}", device):
        raise ValueError("A paired device UDID is required.")
    if streaming:
        raise RuntimeError(
            "Stop the device log stream first. A second USB connection drops the phone."
        )
    if not clear:
        if latitude is None or longitude is None:
            raise ValueError("Latitude and longitude are required.")
        latitude, longitude = validate_coordinates(latitude, longitude)
    apply = runner or _dvt_location
    apply(device, latitude, longitude, clear)
    if clear:
        return {"ok": True, "detail": f"Cleared simulated location on {device}."}
    return {"ok": True, "detail": f"Location set to {latitude}, {longitude} on {device}."}


def _subprocess_run(argv: list[str], **kwargs: Any) -> Any:
    import subprocess

    return subprocess.run(argv, **kwargs)


def _urllib_open(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
) -> tuple[int, dict[str, str], bytes]:
    req = urlrequest.Request(url, data=body, headers=headers, method=method)
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:  # noqa: S310 - scheme checked by caller
            raw = resp.read(65_537)
            return int(resp.status), dict(resp.headers.items()), raw
    except urlerror.HTTPError as exc:
        raw = exc.read(65_537)
        return int(exc.code), dict(exc.headers.items()), raw


def _dvt_location(udid: str, latitude: float | None, longitude: float | None, clear: bool) -> None:
    async def _run() -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

        lockdown = await create_using_usbmux(serial=udid, connection_type="USB", autopair=False)
        try:
            async with DvtProvider(lockdown) as dvt, LocationSimulation(dvt) as simulation:
                if clear:
                    await simulation.clear()
                else:
                    await simulation.set(float(latitude), float(longitude))
        finally:
            await lockdown.close()

    _run_coro(_run())


def _run_coro(coro: Any) -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(coro)
        return
    box: list[BaseException] = []

    def _worker() -> None:
        try:
            asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join()
    if box:
        raise box[0]
