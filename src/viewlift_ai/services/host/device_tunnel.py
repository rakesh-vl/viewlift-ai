"""Developer services on physical devices: location, screenshots, and the iOS 17+ tunnel.

iOS, iPadOS, and tvOS 17 and later expose developer services only over a RemoteXPC tunnel
(RSD). Creating that tunnel needs root, so the console never does it itself. Setup starts
pymobiledevice3's `tunneld` once through the macOS administrator prompt, bound to loopback.
Everything else talks to that tunneld over HTTP as the normal user.

Devices on iOS 16 and earlier do not need the tunnel. Their services are reached through
usbmux lockdown (USB first, then Wi-Fi), which is the fallback for every call here.
"""

from __future__ import annotations

import asyncio
import os
import queue
import shlex
import sys
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib import error as urlerror
from urllib import request as urlrequest

from viewlift_ai.services.host.context import png_payload
from viewlift_ai.services.host.runner import HostError, check_udid

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit


TUNNELD_ENV = "VIEWLIFT_TUNNELD_ADDRESS"
PAIRING_PIN_TIMEOUT_SECONDS = 180.0
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def available() -> bool:
    import importlib.util

    return importlib.util.find_spec("pymobiledevice3") is not None


def _require_pymobiledevice3() -> None:
    if not available():
        raise HostError("pymobiledevice3 is not installed. Run `uv sync` in the console repo.")


def tunneld_address() -> tuple[str, int]:
    """`VIEWLIFT_TUNNELD_ADDRESS` (host:port, loopback only), else pymobiledevice3's default."""
    raw = (os.environ.get(TUNNELD_ENV) or "").strip()
    if not raw:
        _require_pymobiledevice3()
        from pymobiledevice3.tunneld.api import TUNNELD_DEFAULT_ADDRESS

        return TUNNELD_DEFAULT_ADDRESS
    host, _, port = raw.rpartition(":")
    host = host.strip("[]") or "127.0.0.1"
    if host not in _LOOPBACK or not port.isdigit():
        raise HostError(f"{TUNNELD_ENV} must be a loopback host:port.")
    return host, int(port)


def _tunneld_url(path: str) -> str:
    host, port = tunneld_address()
    shown = f"[{host}]" if ":" in host else host
    return f"http://{shown}:{port}{path}"


def _tunneld_get(path: str, timeout: float = 5.0) -> Any:
    import json

    try:
        with urlrequest.urlopen(_tunneld_url(path), timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except (urlerror.URLError, OSError, TimeoutError):
        return None
    try:
        return json.loads(raw.decode("utf-8") or "null")
    except (UnicodeError, ValueError):
        return None


def tunnels() -> dict[str, list[dict[str, Any]]] | None:
    """Active tunnels by UDID, or None when tunneld is not running."""
    data = _tunneld_get("/")
    return data if isinstance(data, dict) else None


def run_sync[T](factory: Callable[[], Awaitable[T]]) -> T:
    """Run one coroutine to completion from sync code, on a private loop when one is running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())  # type: ignore[arg-type]
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = asyncio.run(factory())  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread
            box["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


@asynccontextmanager
async def service_provider(udid: str | None) -> AsyncIterator[Any]:
    """An RSD provider through tunneld when it serves this device, else usbmux lockdown."""
    from pymobiledevice3.exceptions import TunneldConnectionError
    from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid, get_tunneld_devices

    rsd = None
    try:
        if udid:
            rsd = await get_tunneld_device_by_udid(udid, tunneld_address())
        else:
            found = await get_tunneld_devices(tunneld_address())
            if len(found) == 1:
                rsd = found[0]
            else:
                for extra in found:
                    await extra.close()
    except (TunneldConnectionError, OSError):
        rsd = None
    if rsd is not None:
        try:
            yield rsd
        finally:
            await rsd.close()
        return
    lockdown = await _lockdown(udid)
    try:
        yield lockdown
    finally:
        await lockdown.close()


async def _lockdown(udid: str | None) -> Any:
    from pymobiledevice3.lockdown import create_using_usbmux

    last: Exception | None = None
    for connection_type in ("USB", "Network"):
        try:
            return await create_using_usbmux(
                serial=udid, connection_type=connection_type, autopair=False
            )
        except Exception as exc:  # noqa: BLE001 - pymobiledevice3 raises many connection types
            last = exc
    label = f"Device {udid}" if udid else "The device"
    hint = (
        " On iOS 17 or later, start the device tunnel on the Setup page."
        if tunnels() is None
        else ""
    )
    raise HostError(f"{label} is not reachable: {last}.{hint}")


def set_location(
    udid: str | None, latitude: float | None, longitude: float | None, clear: bool
) -> None:
    async def body() -> None:
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.location_simulation import (
            LocationSimulation,
        )

        async with service_provider(udid) as provider:
            async with DvtProvider(provider) as dvt, LocationSimulation(dvt) as simulation:
                if clear:
                    await simulation.clear()
                else:
                    await simulation.set(float(latitude or 0.0), float(longitude or 0.0))

    run_sync(body)


def screenshot_png(udid: str | None) -> bytes:
    async def body() -> bytes:
        from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
        from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot

        async with service_provider(udid) as provider:
            async with DvtProvider(provider) as dvt, Screenshot(dvt) as shot:
                return await shot.get_screenshot()

    return run_sync(body)


# ---- tunneld lifecycle ----------------------------------------------------------------------


def status(_ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    host, port = tunneld_address()
    active = tunnels()
    return {
        "running": active is not None,
        "address": f"{host}:{port}",
        "devices": sorted(active or {}),
        "note": (
            "Needed for developer services (location, screenshots, syslog) on iOS, iPadOS, "
            "and tvOS 17 or later."
        ),
    }


def _admin_command(ctx: HostContext) -> str:
    host, port = tunneld_address()
    log = ctx.work_dir / "tunneld.log"
    home = str(Path.home())
    argv = [
        sys.executable,
        "-m",
        "pymobiledevice3",
        "remote",
        "tunneld",
        "--host",
        host,
        "--port",
        str(port),
    ]
    inner = " ".join(shlex.quote(part) for part in argv)
    return f"HOME={shlex.quote(home)} nohup {inner} > {shlex.quote(str(log))} 2>&1 &"


def start(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    """Start tunneld as root through the macOS administrator prompt. Loopback only."""
    if tunnels() is not None:
        return {**status(ctx, {}), "started": False, "detail": "tunneld is already running."}
    shell = _admin_command(ctx).replace("\\", "\\\\").replace('"', '\\"')
    script = f'do shell script "{shell}" with administrator privileges'
    result = ctx.run(["osascript", "-e", script], timeout=120)
    if not result.ok:
        detail = (result.stderr or "").strip()
        if "-128" in detail:
            raise HostError("The administrator prompt was cancelled. The tunnel was not started.")
        raise HostError(f"Could not start tunneld: {detail[:300]}")
    for _ in range(40):
        if tunnels() is not None:
            return {**status(ctx, {}), "started": True}
        threading.Event().wait(0.25)
    raise HostError(f"tunneld did not answer. See {ctx.work_dir / 'tunneld.log'}.")


def stop(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    if tunnels() is None:
        return {**status(ctx, {}), "stopped": False, "detail": "tunneld is not running."}
    _tunneld_get("/shutdown")
    return {**status(ctx, {}), "stopped": True}


# ---- Apple TV and other PIN pairing ---------------------------------------------------------

_PAIRING: dict[str, Any] = {}
_PAIRING_LOCK = threading.Lock()


def browse_pairable(_ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    """Apple TVs (and Vision Pros) currently showing Settings > Remotes and Devices."""

    async def body() -> list[dict[str, Any]]:
        from pymobiledevice3.bonjour import browse_remotepairing_manual_pairing

        rows = []
        for answer in await browse_remotepairing_manual_pairing():
            props = answer.properties
            rows.append(
                {
                    "name": props.get("name"),
                    "identifier": props.get("identifier"),
                    "addresses": [address.full_ip for address in answer.addresses],
                    "port": answer.port,
                }
            )
        return rows

    rows = run_sync(body)
    return {"devices": rows, "count": len(rows)}


def pair_start(_ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    """Begin pairing with one device. The device shows a PIN; send it with device_pair_pin."""
    identifier = str(args.get("identifier") or "").strip()
    if not identifier or len(identifier) > 128:
        raise HostError("identifier is required. Call device_pair_browse.")
    with _PAIRING_LOCK:
        if _PAIRING.get("thread") and _PAIRING["thread"].is_alive():
            raise HostError("A pairing is already in progress.")
        pins: queue.Queue[str] = queue.Queue(maxsize=1)
        state: dict[str, Any] = {"identifier": identifier, "pins": pins, "state": "waiting_for_pin"}

        def worker() -> None:
            try:
                run_sync(lambda: _pair(identifier, pins))
                state["state"] = "paired"
            except Exception as exc:  # noqa: BLE001 - surfaced through device_pair_status
                state["state"] = "failed"
                state["error"] = str(exc)[:300]

        thread = threading.Thread(target=worker, daemon=True)
        state["thread"] = thread
        _PAIRING.clear()
        _PAIRING.update(state)
        thread.start()
    return {"identifier": identifier, "state": "waiting_for_pin"}


async def _pair(identifier: str, pins: queue.Queue[str]) -> None:
    from pymobiledevice3.bonjour import browse_remotepairing_manual_pairing
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.remote import tunnel_service

    target = None
    for answer in await browse_remotepairing_manual_pairing():
        if answer.properties.get("identifier") == identifier and answer.addresses:
            target = answer
            break
    if target is None:
        raise HostError("The device is no longer advertising. Reopen Remotes and Devices.")

    def read_pin(_prompt: str = "") -> str:
        try:
            return pins.get(timeout=PAIRING_PIN_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise HostError("No PIN was entered in time.") from exc

    # tunnel_service reads the PIN with input(); a module global shadows the builtin there.
    tunnel_service.input = read_pin  # type: ignore[attr-defined]
    try:
        service = tunnel_service.RemotePairingManualPairingService(
            identifier, target.addresses[0].full_ip, target.port
        )
        async with service:
            try:
                await service.connect(autopair=True)
            except RemotePairingCompletedError:
                return
    finally:
        del tunnel_service.input  # type: ignore[attr-defined]


def pair_pin(_ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    pin = str(args.get("pin") or "").strip()
    if not pin.isdigit() or not 4 <= len(pin) <= 8:
        raise HostError("pin must be the 4 to 8 digits shown on the device.")
    with _PAIRING_LOCK:
        pins = _PAIRING.get("pins")
        if pins is None or _PAIRING.get("state") != "waiting_for_pin":
            raise HostError("No pairing is waiting for a PIN. Call device_pair_start.")
        try:
            pins.put_nowait(pin)
        except queue.Full as exc:
            raise HostError("A PIN was already sent.") from exc
        _PAIRING["state"] = "pairing"
    return {"state": "pairing"}


def pair_status(_ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    with _PAIRING_LOCK:
        return {
            "identifier": _PAIRING.get("identifier"),
            "state": _PAIRING.get("state", "idle"),
            "error": _PAIRING.get("error"),
        }


# ---- host tools -----------------------------------------------------------------------------


def device_screenshot(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    udid = check_udid(str(args.get("udid") or "")) if args.get("udid") else None
    data = screenshot_png(udid)
    path = ctx.temp_file(".png")
    try:
        path.write_bytes(data)
        payload = png_payload(
            path, max_dimension=int(args.get("maxDimension", 1000)), runner=ctx.runner
        )
    finally:
        path.unlink(missing_ok=True)
    return {"udid": udid, **payload}


def device_location(_ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    udid = check_udid(str(args.get("udid") or "")) if args.get("udid") else None
    clear = bool(args.get("clear"))
    lat = lon = None
    if not clear:
        try:
            lat = float(args["latitude"])
            lon = float(args["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HostError("latitude and longitude are required numbers.") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise HostError("latitude or longitude is out of range.")
    set_location(udid, lat, lon, clear)
    if clear:
        return {"udid": udid, "cleared": True}
    return {"udid": udid, "latitude": lat, "longitude": lon}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    table = {
        "device_tunnel_status": (status, False),
        "device_tunnel_start": (start, True),
        "device_tunnel_stop": (stop, True),
        "device_pair_browse": (browse_pairable, False),
        "device_pair_start": (pair_start, True),
        "device_pair_pin": (pair_pin, True),
        "device_pair_status": (pair_status, False),
        "device_screenshot": (device_screenshot, False),
        "device_location": (device_location, True),
    }
    for name, (handler, mutating) in table.items():
        toolkit.add(name, lambda args, fn=handler: fn(ctx, args), mutating=mutating)
