"""Orchestrate simulator/device log collectors into encrypted store + event bus."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from viewlift_ai.models.log import LogLevel
from viewlift_ai.services.device_logs import DeviceLogCollector, DeviceLogCollectorProtocol
from viewlift_ai.services.log_capture import LogCaptureService
from viewlift_ai.services.simulator_logs import (
    SimulatorLogCollector,
    SimulatorLogCollectorProtocol,
)

SourceKind = Literal["simulator", "device", "sdk"]

# Interpolated into a simctl predicate, so keep it to a safe literal charset.
_PROCESS_NAME = re.compile(r"[A-Za-z0-9 ._-]{1,64}")


@dataclass(slots=True)
class LogTailService:
    """Start/stop one active external log source (simulator or device)."""

    capture: LogCaptureService
    simulator: SimulatorLogCollectorProtocol = field(default_factory=SimulatorLogCollector)
    device: DeviceLogCollectorProtocol = field(default_factory=DeviceLogCollector)
    _active: SourceKind | None = field(default=None, init=False)

    def list_sources(self) -> dict[str, object]:
        sims = self.simulator.list_booted()
        devices = self.device.list_devices()
        return {
            "active": self.status(),
            "simulators": sims,
            "devices": devices,
            "sdk": {
                "kind": "sdk",
                "detail": (
                    "SDK logs arrive via ViewliftNetworkCapture LogService when the "
                    "secure ingest receiver is enabled."
                ),
            },
            "testflight_note": (
                "TestFlight os_log privacy fields often arrive as <private>. "
                "Expected for distribution builds."
            ),
        }

    def status(self) -> dict[str, object]:
        if self.simulator.running:
            return {
                "running": True,
                "source": "simulator",
                "udid": self.simulator.active_udid,
            }
        if self.device.running:
            return {
                "running": True,
                "source": "device",
                "udid": self.device.active_udid,
                "last_error": getattr(self.device, "last_error", None),
            }
        return {"running": False, "source": None, "udid": None}

    def start(
        self,
        source: str,
        *,
        udid: str | None = None,
        process: str | None = None,
    ) -> dict[str, object]:
        kind = (source or "").strip().lower()
        if kind not in {"simulator", "device"}:
            return {
                "ok": False,
                "detail": "source must be 'simulator' or 'device' (SDK is ingest-only).",
            }
        process_name = (process or "").strip() or None
        if process_name and not _PROCESS_NAME.fullmatch(process_name):
            return {
                "ok": False,
                "detail": "Process name may only contain letters, digits, space, '.', '_' or '-'.",
            }
        current = self.status()
        if current.get("running"):
            self.set_filter(process=process_name or "")
            return {
                "ok": True,
                "detail": (
                    f"Streaming {current.get('source')} ({current.get('udid')}). "
                    "Filter updated. Left the USB session up."
                ),
            }
        if kind == "simulator":
            target = (udid or "").strip()
            if not target:
                booted = self.simulator.list_booted()
                if len(booted) == 1:
                    target = booted[0]["udid"]
                elif not booted:
                    return {
                        "ok": False,
                        "detail": "No booted simulator. Boot an iOS/tvOS simulator first.",
                    }
                else:
                    return {
                        "ok": False,
                        "detail": (
                            "Multiple booted simulators. Pass udid. "
                            f"Available: {[s['udid'] for s in booted]}"
                        ),
                    }
            self.set_filter(process=process_name or "")
            result = self.simulator.start(
                target,
                on_line=lambda level, subsystem, message: self._on_line(
                    "simulator", level, subsystem, message
                ),
                process=process_name,
            )
            if result.get("ok"):
                self._active = "simulator"
            return result

        self.set_filter(process=process_name or "")
        result = self.device.start(
            udid,
            on_line=lambda level, subsystem, message, process="": self._on_line(
                "device", level, subsystem, message, process=process
            ),
            process=process_name,
        )
        if result.get("ok"):
            self._active = "device"
        return result

    def stop(self) -> dict[str, object]:
        results = []
        if self.simulator.running or self._active == "simulator":
            results.append(self.simulator.stop())
        if self.device.running or self._active == "device":
            results.append(self.device.stop())
        self._active = None
        if not results:
            return {"ok": True, "detail": "No log stream was running."}
        # Prefer last detail
        return results[-1]

    def set_filter(
        self,
        *,
        process: str | None = None,
        subsystem: str | None = None,
    ) -> dict[str, object]:
        """Narrow the live stream without reopening USB."""
        for label, value in (("Process", process), ("Subsystem", subsystem)):
            if value and not _PROCESS_NAME.fullmatch(value):
                return {
                    "ok": False,
                    "detail": (
                        f"{label} may only contain letters, digits, space, '.', '_' or '-'."
                    ),
                }
        selector = getattr(self.device, "selector", None)
        if selector is not None:
            selector.update(process=process, subsystem=subsystem)
        return {
            "ok": True,
            "process": getattr(selector, "process", process or ""),
            "subsystem": getattr(selector, "subsystem", subsystem or ""),
        }

    def _on_line(
        self,
        source: str,
        level: LogLevel,
        subsystem: str,
        message: str,
        process: str = "",
    ) -> None:
        selector = getattr(self.device, "selector", None)
        if selector is not None and not selector.allows(process=process, subsystem=subsystem):
            return
        request_id = _extract_request_id(message)
        self.capture.persist(
            level=level,
            subsystem=subsystem,
            message=message,
            source=source,
            request_id=request_id,
        )


def _extract_request_id(message: str) -> str | None:
    """Pull optional requestId=... / request_id=... tokens from free text."""
    import re

    match = re.search(
        r"(?:requestId|request_id|x-request-id)[=:\s]+([A-Za-z0-9._-]{6,128})",
        message,
        re.I,
    )
    if match:
        return match.group(1)
    return None
