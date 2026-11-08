"""Screen recording for USB-connected iPhones and iPads.

The recorder is a small Swift helper (`ios/ViewliftHostTools`) built on first use into the
console's work folder. It turns on CoreMediaIO screen-capture devices and records with
AVFoundation, the same capture QuickTime uses. macOS asks once for camera access for the
process that runs the console. Apple TV has no USB screen capture, so it is refused.
"""

from __future__ import annotations

import json
import os
import signal
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host.runner import HostError, check_udid

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

PACKAGE_ENV = "VIEWLIFT_HOST_TOOLS_PACKAGE"
PRODUCT = "viewlift-screen-recorder"
_RECORDINGS: dict[str, tuple[Any, Path]] = {}


def package_dir() -> Path:
    override = (os.environ.get(PACKAGE_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / "ios" / "ViewliftHostTools"


def recorder_binary(ctx: HostContext) -> Path:
    """Build the helper once per source change and return its path."""
    package = package_dir()
    source = package / "Sources" / "ScreenRecorder" / "main.swift"
    if not source.is_file():
        raise HostError(f"Recorder sources are missing at {package}. Set {PACKAGE_ENV}.")
    scratch = ctx.work_dir / "host-tools-build"
    binary = scratch / "release" / PRODUCT
    if binary.is_file() and binary.stat().st_mtime >= source.stat().st_mtime:
        return binary
    ctx.run_ok(
        [
            "swift",
            "build",
            "-c",
            "release",
            "--package-path",
            str(package),
            "--scratch-path",
            str(scratch),
            "--product",
            PRODUCT,
        ],
        timeout=600,
    )
    if not binary.is_file():
        raise HostError("The recorder build finished without producing a binary.")
    return binary


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows = []
    for line in (text or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def list_capture_devices(ctx: HostContext, _args: dict[str, Any]) -> dict[str, Any]:
    result = ctx.run([str(recorder_binary(ctx)), "list"], timeout=30)
    rows = _json_lines(result.stdout)
    for row in rows:
        if "error" in row:
            raise HostError(str(row["error"]))
    devices = rows[-1].get("devices", []) if rows else []
    return {"devices": devices, "count": len(devices)}


def record(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    udid = check_udid(str(args.get("udid") or ""))
    if str(args.get("platform") or "").lower() == "tvos":
        raise HostError("Apple TV has no USB screen capture. Record from the tvOS simulator.")
    action = str(args.get("action", "start")).lower()
    if action == "stop":
        running = _RECORDINGS.pop(udid, None)
        if running is None:
            raise HostError("No recording is running on this device.")
        proc, path = running
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=30)
        except Exception:  # noqa: BLE001 - make sure the helper never outlives the request
            proc.kill()
        return {"udid": udid, "stopped": True, "path": str(path)}
    if udid in _RECORDINGS:
        raise HostError("A recording is already running. Call with action=stop first.")
    binary = recorder_binary(ctx)
    path = ctx.work_dir / "recordings" / f"{udid}-{uuid.uuid4().hex[:8]}.mov"
    path.parent.mkdir(parents=True, exist_ok=True)
    holder: dict[str, Any] = {}

    def body(job_ctx: Any) -> dict[str, Any]:
        holder["proc"] = job_ctx.spawn([str(binary), "record", udid, str(path)])
        code = holder["proc"].wait()
        if code != 0:
            raise HostError("The recorder stopped with an error. See the job log.")
        return {"path": str(path)}

    job = ctx.jobs.start("record_device_video", f"Recording {udid}", body)
    for _ in range(100):
        if "proc" in holder or job.done.is_set():
            break
        job.done.wait(0.05)
    if "proc" not in holder:
        raise HostError(job.error or "Recording did not start.")
    _RECORDINGS[udid] = (holder["proc"], path)
    return {"udid": udid, "recording": True, "path": str(path), "job_id": job.id}


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add("list_capture_devices", lambda args: list_capture_devices(ctx, args))
    toolkit.add("record_device_video", lambda args: record(ctx, args))
