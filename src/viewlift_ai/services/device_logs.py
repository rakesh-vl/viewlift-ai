"""Physical paired USB/Wi-Fi device syslog via pymobiledevice3 (stable adapter)."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from viewlift_ai.models.log import LogLevel

logger = logging.getLogger(__name__)

LineHandler = Callable[..., None]
DeviceLister = Callable[[], list[dict[str, str]]]
SyslogStarter = Callable[[str | None, LineHandler, threading.Event, str | None], None]


class DeviceLogCollectorProtocol(Protocol):
    def list_devices(self) -> list[dict[str, object]]: ...

    def start(
        self,
        udid: str | None,
        *,
        on_line: LineHandler,
        process: str | None = None,
    ) -> dict[str, object]: ...

    def stop(self) -> dict[str, object]: ...

    @property
    def running(self) -> bool: ...

    @property
    def active_udid(self) -> str | None: ...


@dataclass(slots=True)
class LogSelector:
    """Live process / bundle-id filter. Safe to update while a stream is running."""

    _process: str = ""
    _subsystem: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def update(self, *, process: str | None = None, subsystem: str | None = None) -> None:
        with self._lock:
            if process is not None:
                self._process = process.strip()
            if subsystem is not None:
                self._subsystem = subsystem.strip()

    @property
    def process(self) -> str:
        with self._lock:
            return self._process

    @property
    def subsystem(self) -> str:
        with self._lock:
            return self._subsystem

    def allows(self, *, process: str, subsystem: str) -> bool:
        wanted_process, wanted_subsystem = self.process.lower(), self.subsystem.lower()
        proc = process.lower()
        sub = subsystem.lower()
        if wanted_process and wanted_process != proc and wanted_process not in sub:
            return False
        if wanted_subsystem and wanted_subsystem not in sub and wanted_subsystem not in proc:
            return False
        return True


@dataclass(slots=True)
class DeviceLogCollector:
    """
    Prefer pymobiledevice3 OsTraceService syslog; fall back to legacy SyslogService.

    Clear diagnostics when no paired device / lockdown fails. Injectable for tests.
    """

    list_fn: DeviceLister | None = None
    stream_fn: SyslogStarter | None = None
    selector: LogSelector = field(default_factory=LogSelector)
    _cached_devices: list[dict[str, object]] | None = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _udid: str | None = field(default=None, init=False)
    _running: bool = field(default=False, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _last_error: str | None = field(default=None, init=False)

    def list_devices(self) -> list[dict[str, object]]:
        # A second lockdown while syslog is streaming knocks the phone off USB.
        if self.running and self._cached_devices:
            return self._cached_devices
        try:
            rows = (self.list_fn or list_paired_devices)()
        except Exception as exc:
            return [
                {
                    "ok": False,
                    "kind": "device",
                    "detail": _diagnose_list_error(exc),
                    "udid": None,
                    "name": None,
                }
            ]
        if not rows:
            return [
                {
                    "ok": False,
                    "kind": "device",
                    "detail": (
                        "No paired USB/Wi-Fi devices found. Connect the device, unlock it, "
                        "trust this Mac, and ensure `pymobiledevice3 usbmux list` sees it."
                    ),
                    "udid": None,
                    "name": None,
                }
            ]
        out: list[dict[str, object]] = []
        for row in rows:
            out.append(
                {
                    "ok": True,
                    "kind": "device",
                    "udid": row.get("udid"),
                    "name": row.get("name") or row.get("udid"),
                    "connection": row.get("connection") or "unknown",
                    "product": row.get("product") or "",
                    "detail": row.get("detail") or "Paired device available.",
                }
            )
        self._cached_devices = out
        return out

    @property
    def running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def active_udid(self) -> str | None:
        return self._udid if self.running else None

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def start(
        self,
        udid: str | None,
        *,
        on_line: LineHandler,
        process: str | None = None,
    ) -> dict[str, object]:
        with self._lock:
            if self.running:
                return {
                    "ok": False,
                    "detail": f"Already streaming device {self._udid}. Stop first.",
                }
            devices = self.list_devices()
            usable = [d for d in devices if d.get("ok") and d.get("udid")]
            failed = [d for d in devices if not d.get("ok")]
            target = (udid or "").strip() or None
            if target and not usable and failed:
                detail = str(failed[0].get("detail") or "No device available.")
                return {"ok": False, "detail": detail}
            if target is None:
                if len(usable) == 1:
                    target = str(usable[0]["udid"])
                elif not usable:
                    detail = devices[0].get("detail") if devices else "No device available."
                    return {"ok": False, "detail": str(detail)}
                else:
                    return {
                        "ok": False,
                        "detail": (
                            "Multiple paired devices. Select a UDID. "
                            f"Available: {[d.get('udid') for d in usable]}"
                        ),
                    }
            elif not any(str(d.get("udid")) == target for d in usable):
                return {
                    "ok": False,
                    "detail": (
                        f"Device {target} not in paired list. "
                        "Reconnect USB/Wi-Fi pairing and retry."
                    ),
                }

            if process is not None:
                self.selector.update(process=process)
            self._stop.clear()
            self._last_error = None
            self._udid = target
            self._running = True
            starter = self.stream_fn or stream_device_syslog
            self._thread = threading.Thread(
                target=self._run_stream,
                args=(target, on_line, starter, process),
                name=f"device-log-{(target or 'auto')[:8]}",
                daemon=True,
            )
            self._thread.start()
            scope = f" (process {process})" if process else ""
            return {
                "ok": True,
                "detail": f"Streaming device syslog for {target}{scope}.",
                "udid": target,
                "process": process,
            }

    def stop(self) -> dict[str, object]:
        with self._lock:
            self._stop.set()
            thread = self._thread
            udid = self._udid
            self._running = False
            self._thread = None
            self._udid = None
            self._cached_devices = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        return {"ok": True, "detail": f"Stopped device log stream ({udid})."}

    def _run_stream(
        self,
        udid: str,
        on_line: LineHandler,
        starter: SyslogStarter,
        process: str | None,
    ) -> None:
        try:
            starter(udid, on_line, self._stop, process)
        except Exception as exc:
            self._last_error = _diagnose_stream_error(exc)
            logger.warning("device syslog ended: %s", self._last_error)
        finally:
            self._running = False


def list_paired_devices() -> list[dict[str, str]]:
    """List usbmux-connected devices (USB or paired Wi-Fi)."""
    return _run_coro(_list_paired_devices())


async def _list_paired_devices() -> list[dict[str, str]]:
    from pymobiledevice3 import usbmux
    from pymobiledevice3.lockdown import create_using_usbmux

    devices = await usbmux.list_devices()
    chosen: dict[str, dict[str, str]] = {}
    for device in devices:
        serial = getattr(device, "serial", None) or getattr(device, "udid", None)
        if not serial:
            continue
        connection = "usb" if getattr(device, "is_usb", False) else "wifi"
        row = {
            "udid": str(serial),
            "name": str(serial),
            "connection": connection,
            "product": str(getattr(device, "product_type", "") or ""),
            "detail": f"Connected via {connection}.",
        }
        previous = chosen.get(row["udid"])
        if previous is None or connection == "usb":
            chosen[row["udid"]] = row
    enriched: list[dict[str, str]] = []
    for row in chosen.values():
        connection_type = "USB" if row["connection"] == "usb" else "Network"
        lockdown = None
        try:
            lockdown = await create_using_usbmux(
                serial=row["udid"],
                connection_type=connection_type,
                autopair=False,
            )
            enriched.append(
                {
                    **row,
                    "name": str(lockdown.display_name or row["udid"]),
                    "product": str(lockdown.product_type or row.get("product") or ""),
                }
            )
        except Exception:
            enriched.append(row)
        finally:
            if lockdown is not None:
                await lockdown.close()
    return enriched


def stream_device_syslog(
    udid: str | None,
    on_line: LineHandler,
    stop: threading.Event,
    process: str | None = None,
) -> None:
    """Blocking syslog stream using a private asyncio loop (thread-safe stop)."""
    _ = process

    async def _run() -> None:
        from pymobiledevice3.lockdown import create_using_usbmux
        from pymobiledevice3.services.os_trace import OsTraceService
        from pymobiledevice3.services.syslog import SyslogService

        lockdown = None
        last_error: Exception | None = None
        for connection_type in ("USB", "Network"):
            try:
                lockdown = await create_using_usbmux(
                    serial=udid,
                    connection_type=connection_type,
                    autopair=True,
                )
                break
            except Exception as exc:
                last_error = exc
                lockdown = None
        if lockdown is None:
            raise last_error or RuntimeError(f"Device {udid} is not connected")
        try:
            try:
                async with OsTraceService(lockdown=lockdown) as service:
                    async for entry in service.syslog():
                        if stop.is_set():
                            break
                        proc_name = _basename(getattr(entry, "filename", None))
                        level = _map_os_trace_level(getattr(entry, "level", None))
                        label = getattr(entry, "label", None)
                        subsystem = (
                            (getattr(label, "subsystem", None) if label is not None else None)
                            or proc_name
                            or "device"
                        )
                        message = str(getattr(entry, "message", "") or "")
                        on_line(level, str(subsystem), message, proc_name)
                return
            except Exception as os_trace_exc:
                logger.info("OsTraceService unavailable, falling back: %s", os_trace_exc)
            async with SyslogService(service_provider=lockdown) as service:
                async for line in service.watch():
                    if stop.is_set():
                        break
                    text = line if isinstance(line, str) else repr(line)
                    level, subsystem, message = parse_device_syslog_line(text)
                    on_line(level, subsystem, message, subsystem)
        finally:
            await lockdown.close()

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        task = loop.create_task(_run())

        def _watch_stop() -> None:
            while not stop.wait(0.25):
                if task.done():
                    return
            loop.call_soon_threadsafe(task.cancel)

        watcher = threading.Thread(target=_watch_stop, daemon=True)
        watcher.start()
        try:
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
    finally:
        loop.close()


def parse_device_syslog_line(line: str) -> tuple[LogLevel, str, str]:
    text = line.strip()
    if not text:
        return LogLevel.INFO, "device", ""
    # Typical: "Feb  3 12:00:00 iPhone process[123] <Notice>: message"
    level = LogLevel.INFO
    lower = text.lower()
    if "<fault>" in lower or " fault:" in lower:
        level = LogLevel.FAULT
    elif "<error>" in lower or " error:" in lower:
        level = LogLevel.ERROR
    elif "<warning>" in lower or " warning:" in lower:
        level = LogLevel.WARNING
    elif "<debug>" in lower:
        level = LogLevel.DEBUG
    subsystem = "device"
    if "]" in text:
        head = text.split("]", 1)[0]
        if "[" in head:
            subsystem = head.rsplit("[", 1)[0].split()[-1] or "device"
    message = text
    if ": " in text:
        message = text.split(": ", 1)[-1]
    return level, subsystem, message


def _basename(path: Any) -> str:
    text = str(path or "").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""


def _map_os_trace_level(level: Any) -> LogLevel:
    name = getattr(level, "name", None) or str(level or "INFO")
    key = str(name).upper()
    mapping = {
        "DEBUG": LogLevel.DEBUG,
        "INFO": LogLevel.INFO,
        "NOTICE": LogLevel.INFO,
        "DEFAULT": LogLevel.INFO,
        "WARNING": LogLevel.WARNING,
        "ERROR": LogLevel.ERROR,
        "FAULT": LogLevel.FAULT,
        "CRITICAL": LogLevel.FAULT,
    }
    return mapping.get(key, LogLevel.INFO)


def _run_coro(coro):
    """Run a coroutine from sync code, including a thread that already has a loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except Exception as exc:
            result["error"] = exc

    thread = threading.Thread(target=_worker, name="device-log-coro", daemon=True)
    thread.start()
    thread.join()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def _diagnose_list_error(exc: Exception) -> str:
    return (
        f"Failed to list paired devices ({type(exc).__name__}: {exc}). "
        "Install/trust the device, unlock the screen, and verify "
        "`python -m pymobiledevice3 usbmux list` works."
    )


def _diagnose_stream_error(exc: Exception) -> str:
    return (
        f"Device syslog stream failed ({type(exc).__name__}: {exc}). "
        "Confirm the device stays unlocked/trusted, USB/Wi-Fi pairing is intact, "
        "and Developer Mode is enabled when required."
    )
