"""Booted iOS/tvOS simulator log stream via `xcrun simctl spawn <udid> log stream`."""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from viewlift_ai.models.log import LogLevel

CommandRunner = Callable[..., Any]
LineHandler = Callable[[LogLevel, str, str], None]


class SimulatorLogCollectorProtocol(Protocol):
    def list_booted(self) -> list[dict[str, str]]: ...

    def start(
        self,
        udid: str,
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
class SimulatorLogCollector:
    """Subprocess collector. Always argv list — never shell=True."""

    runner: CommandRunner | None = None
    popen: Callable[..., Any] | None = None
    _proc: Any = field(default=None, init=False, repr=False)
    _thread: threading.Thread | None = field(default=None, init=False, repr=False)
    _udid: str | None = field(default=None, init=False)
    _stop: threading.Event = field(default_factory=threading.Event, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def list_booted(self) -> list[dict[str, str]]:
        argv = ["xcrun", "simctl", "list", "devices", "booted", "-j"]
        run = self.runner or subprocess.run
        try:
            completed = run(argv, check=False, capture_output=True, text=True)
        except FileNotFoundError:
            return []
        except OSError:
            return []
        if completed.returncode != 0:
            # Fallback: plain text list
            return self._list_booted_text()
        return _parse_booted_json(completed.stdout or "")

    def _list_booted_text(self) -> list[dict[str, str]]:
        argv = ["xcrun", "simctl", "list", "devices", "booted"]
        run = self.runner or subprocess.run
        try:
            completed = run(argv, check=False, capture_output=True, text=True)
        except OSError:
            return []
        if completed.returncode != 0:
            return []
        return _parse_booted_text(completed.stdout or "")

    @property
    def running(self) -> bool:
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def active_udid(self) -> str | None:
        return self._udid if self.running else None

    def start(
        self,
        udid: str,
        *,
        on_line: LineHandler,
        process: str | None = None,
    ) -> dict[str, object]:
        target = (udid or "").strip()
        if not target:
            return {"ok": False, "detail": "Simulator UDID is required."}
        with self._lock:
            if self.running:
                return {
                    "ok": False,
                    "detail": f"Already streaming simulator {self._udid}. Stop first.",
                }
            argv = [
                "xcrun",
                "simctl",
                "spawn",
                target,
                "log",
                "stream",
                "--style",
                "compact",
                "--level",
                "debug",
            ]
            if process:
                argv += ["--predicate", f'process == "{process}"']
            factory = self.popen or subprocess.Popen
            try:
                proc = factory(
                    argv,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                )
            except FileNotFoundError:
                return {
                    "ok": False,
                    "detail": (
                        "xcrun not found. Install Xcode command-line tools, boot a "
                        "simulator, then retry."
                    ),
                    "argv": argv,
                }
            except OSError as exc:
                return {
                    "ok": False,
                    "detail": f"Failed to start log stream: {exc}",
                    "argv": argv,
                }
            self._proc = proc
            self._udid = target
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._pump,
                args=(proc, on_line),
                name=f"sim-log-{target[:8]}",
                daemon=True,
            )
            self._thread.start()
            return {
                "ok": True,
                "detail": f"Streaming simulator logs for {target}.",
                "udid": target,
                "argv": argv,
            }

    def stop(self) -> dict[str, object]:
        with self._lock:
            proc = self._proc
            self._stop.set()
            if proc is None:
                return {"ok": True, "detail": "Simulator log stream was not running."}
            try:
                proc.terminate()
            except OSError:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except OSError:
                    pass
            udid = self._udid
            self._proc = None
            self._udid = None
            thread = self._thread
            self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        return {"ok": True, "detail": f"Stopped simulator log stream ({udid})."}

    def _pump(self, proc: Any, on_line: LineHandler) -> None:
        try:
            stdout = proc.stdout
            if stdout is None:
                return
            for raw in stdout:
                if self._stop.is_set():
                    break
                line = raw.rstrip("\n")
                if not line:
                    continue
                level, subsystem, message = parse_simctl_log_line(line)
                try:
                    on_line(level, subsystem, message)
                except Exception:
                    continue
        finally:
            try:
                if proc.stdout is not None:
                    proc.stdout.close()
            except Exception:
                pass


_UDID_RE = re.compile(
    r"([0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12})"
)
_COMPACT_RE = re.compile(
    r"^(?P<ts>\S+\s+\S+)\s+(?P<proc>[^\[]+)\[(?P<pid>\d+)\]\s+"
    r"(?P<subsys>[^:]+):\s*(?P<msg>.*)$"
)
_LEVEL_MARKERS = (
    (re.compile(r"\bfault\b", re.I), LogLevel.FAULT),
    (re.compile(r"\berror\b", re.I), LogLevel.ERROR),
    (re.compile(r"\bwarn(?:ing)?\b", re.I), LogLevel.WARNING),
    (re.compile(r"\bdebug\b", re.I), LogLevel.DEBUG),
)


def parse_simctl_log_line(line: str) -> tuple[LogLevel, str, str]:
    match = _COMPACT_RE.match(line)
    if match:
        subsystem = match.group("subsys").strip() or match.group("proc").strip()
        message = match.group("msg")
        level = _infer_level(message)
        return level, subsystem, message
    return LogLevel.INFO, "simulator", line


def _infer_level(message: str) -> LogLevel:
    for pattern, level in _LEVEL_MARKERS:
        if pattern.search(message):
            return level
    return LogLevel.INFO


def _parse_booted_json(raw: str) -> list[dict[str, str]]:
    import json

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        return []
    out: list[dict[str, str]] = []
    for runtime, rows in devices.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("state") != "Booted":
                continue
            udid = str(row.get("udid") or "")
            name = str(row.get("name") or udid)
            if not udid:
                continue
            platform = "tvOS" if "tvos" in runtime.lower() else "iOS"
            out.append(
                {
                    "udid": udid,
                    "name": name,
                    "runtime": str(runtime),
                    "platform": platform,
                    "kind": "simulator",
                }
            )
    return out


def _parse_booted_text(raw: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for line in raw.splitlines():
        if "Booted" not in line:
            continue
        match = _UDID_RE.search(line)
        if not match:
            continue
        udid = match.group(1)
        name = line.split("(")[0].strip() or udid
        platform = "tvOS" if "tvOS" in line or "Apple TV" in line else "iOS"
        out.append(
            {
                "udid": udid,
                "name": name,
                "runtime": "",
                "platform": platform,
                "kind": "simulator",
            }
        )
    return out
