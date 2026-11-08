"""Crash reports from macOS (`.ips`) and the pipeline that symbolicates each crash.

The SDK records a crash it can see. macOS writes its own report a second or two after any
process death, with every thread and the exact fault. Both land on one card, by client and pid.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.symbolicate import Symbolicator, class_of

logger = logging.getLogger(__name__)

REPORTS_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"
POLL_SECONDS = 1.0
BACKLOG_SECONDS = 1800.0
CRASH_BUG_TYPES = frozenset({"309", "109"})
HEADER_BYTES = 4096
MAX_THREADS = 24
MAX_FRAMES = 160
ASI_CHARS = 600


# ----- .ips parsing -----------------------------------------------------------------------


def read_header(path: Path) -> dict[str, Any] | None:
    """The first line of an `.ips` file is a small JSON header, cheap to read for every file."""
    try:
        with path.open("rb") as handle:
            first = handle.read(HEADER_BYTES).split(b"\n", 1)[0]
        value = json.loads(first)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def read_report(path: Path) -> dict[str, Any] | None:
    """Header and body of one `.ips` report, or None when it is unfinished or not JSON."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        head, _, body = text.partition("\n")
        header, report = json.loads(head), json.loads(body)
    except (OSError, ValueError):
        return None
    if not isinstance(header, dict) or not isinstance(report, dict):
        return None
    return {"header": header, "report": report}


def capture_time(report: dict[str, Any]) -> float:
    raw = str(report.get("captureTime") or "")
    for pattern in ("%Y-%m-%d %H:%M:%S.%f %z", "%Y-%m-%d %H:%M:%S %z"):
        try:
            return datetime.strptime(raw, pattern).timestamp()
        except ValueError:
            continue
    return time.time()


def _images(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in report.get("usedImages") or []:
        if not isinstance(item, dict) or not item.get("uuid") or not item.get("base"):
            rows.append({})
            continue
        path = str(item.get("path") or "")
        rows.append(
            {
                "name": str(item.get("name") or os.path.basename(path)),
                "path": path,
                "uuid": str(item["uuid"]).upper(),
                "base": hex(int(item["base"])),
                "arch": str(item.get("arch") or ""),
            }
        )
    return rows


def _frame(index: int, raw: dict[str, Any], images: list[dict[str, Any]]) -> dict[str, Any]:
    slot = raw.get("imageIndex")
    image = images[slot] if isinstance(slot, int) and 0 <= slot < len(images) else {}
    row: dict[str, Any] = {"index": index}
    base = int(image["base"], 16) if image.get("base") else None
    offset = int(raw.get("imageOffset") or 0)
    if base is not None:
        row["address"] = hex(base + offset)
        row["image"] = image.get("name", "")
        row["image_uuid"] = image.get("uuid", "")
        row["image_base"] = image["base"]
        row["offset"] = offset
    symbol = str(raw.get("symbol") or "")
    if symbol:
        row["symbol"] = symbol
        row["class"] = class_of(symbol)
    if raw.get("sourceFile"):
        row["file"] = os.path.basename(str(raw["sourceFile"]))
        row["path"] = str(raw["sourceFile"])
        if raw.get("sourceLine") is not None:
            row["line"] = int(raw["sourceLine"])
    location = f" + {raw['symbolLocation']}" if raw.get("symbolLocation") is not None else ""
    row["raw"] = (
        f"{index}  {row.get('image', '?')} {row.get('address', '')} {symbol}{location}".strip()
    )
    return row


def _frames(raw_frames: Any, images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for index, raw in enumerate((raw_frames or [])[:MAX_FRAMES]):
        if isinstance(raw, dict):
            out.append(_frame(index, raw, images))
    return out


def _reason(report: dict[str, Any]) -> str:
    notes = report.get("asi")
    if isinstance(notes, dict):
        lines = [str(line) for group in notes.values() if isinstance(group, list) for line in group]
        if lines:
            return " | ".join(lines)[:ASI_CHARS]
    termination = report.get("termination") or {}
    detail = [str(termination.get("indicator") or ""), str(termination.get("details") or "")]
    exception = report.get("exception") or {}
    detail.append(str(exception.get("subtype") or ""))
    return " ".join(part for part in detail if part)[:ASI_CHARS]


def _thread_label(thread: dict[str, Any]) -> str:
    label = str(thread.get("name") or thread.get("queue") or "")
    return (
        f"{label} (id {thread['id']})"
        if thread.get("id") is not None and label
        else (label or f"thread {thread.get('id', '?')}")
    )


def event_from_report(parsed: dict[str, Any], path: Path) -> dict[str, Any]:
    """The `crash` / `fatal` event payload for one `.ips` report."""
    report, header = parsed["report"], parsed["header"]
    images = _images(report)
    threads = [item for item in report.get("threads") or [] if isinstance(item, dict)]
    faulting = next((item for item in threads if item.get("triggered")), None)
    if faulting is None and isinstance(report.get("faultingThread"), int):
        index = report["faultingThread"]
        faulting = threads[index] if 0 <= index < len(threads) else None
    exception = report.get("exception") or {}
    signal = str(exception.get("signal") or "")
    kind = str(exception.get("type") or "")
    name = f"{kind} ({signal})" if kind and signal else (kind or signal or "Crash")
    info = report.get("bundleInfo") if isinstance(report.get("bundleInfo"), dict) else {}
    cpu = str(report.get("cpuType") or "").lower()
    event: dict[str, Any] = {
        "kind": "fatal",
        "source": "crash-report",
        "timestamp": capture_time(report),
        "signal": signal,
        "name": name,
        "reason": _reason(report),
        "pid": int(report.get("pid") or 0),
        "arch": "arm64" if cpu.startswith("arm") else ("x86_64" if cpu else ""),
        "thread": _thread_label(faulting) if faulting else "",
        "executable": str(report.get("procName") or header.get("name") or ""),
        "bundle_id": str(info.get("CFBundleIdentifier") or ""),
        "proc_path": str(report.get("procPath") or ""),
        "previous_session": False,
        "report_file": path.name,
        "frames": _frames(faulting.get("frames"), images) if faulting else [],
        "images": [item for item in images if item],
        "threads": [
            {"label": _thread_label(item), "frames": _frames(item.get("frames"), images)}
            for item in threads[:MAX_THREADS]
            if item is not faulting
        ],
    }
    backtrace = report.get("lastExceptionBacktrace")
    if backtrace:
        event["exception_frames"] = _frames(backtrace, images)
    return event


# ----- client matching --------------------------------------------------------------------


def match_client(
    clients: list[dict[str, Any]], header: dict[str, Any], body: dict[str, Any]
) -> str | None:
    """The app a report belongs to: bundle id, then executable, then the simulator that ran it."""
    info = body.get("bundleInfo") if isinstance(body.get("bundleInfo"), dict) else {}
    bundle = str(info.get("CFBundleIdentifier") or "")
    proc = str(body.get("procName") or header.get("name") or "")
    proc_path = str(body.get("procPath") or "")
    candidates = []
    for client in clients:
        if client.get("superseded_by"):
            continue
        same_bundle = bool(bundle) and client.get("bundle_id") == bundle
        same_exe = bool(proc) and client.get("executable") == proc
        if not (same_bundle or same_exe):
            continue
        udid = str(client.get("device_key") or "")
        on_device = bool(client.get("is_simulator")) and bool(udid) and udid in proc_path
        candidates.append(
            (on_device, same_bundle, str(client.get("last_seen_at") or ""), client["id"])
        )
    if not candidates:
        return None
    return max(candidates)[3]


# ----- pipeline ---------------------------------------------------------------------------


class CrashPipeline:
    """Stores a crash at once, then symbolicates it on a worker thread and updates the same card."""

    def __init__(self, sessions: Any, symbolicator: Symbolicator | None = None) -> None:
        self._sessions = sessions
        self._symbolicator = symbolicator or Symbolicator()

    def attach(self) -> None:
        """Run symbolication for crashes the SDK uploads."""
        self._sessions.on_crash = self._from_sdk

    def _from_sdk(self, client_id: str, stored: dict[str, Any]) -> None:
        self._symbolicate_async(client_id, str(stored["id"]), dict(stored["payload"]))

    def submit(self, client_id: str, event: dict[str, Any]) -> dict[str, Any]:
        result = self._sessions.record_crash(client_id, event)
        if not result.get("kept"):
            self._symbolicate_async(client_id, str(result["id"]), event)
        return result

    def _symbolicate_async(self, client_id: str, event_id: str, event: dict[str, Any]) -> None:
        threading.Thread(
            target=self._symbolicate,
            args=(client_id, event_id, event),
            name="viewlift-symbolicate",
            daemon=True,
        ).start()

    def _symbolicate(self, client_id: str, event_id: str, event: dict[str, Any]) -> None:
        try:
            resolved = self._symbolicator.symbolicate_event(event)
            # Stored even when nothing resolved, so the card can say why it has no file names.
            self._sessions.record_crash(client_id, resolved, event_id=event_id)
        except Exception:
            logger.exception("Crash symbolication failed")


class CrashReportWatcher:
    """Polls DiagnosticReports for crash files of apps that registered with the console."""

    def __init__(
        self,
        sessions: Any,
        pipeline: CrashPipeline,
        *,
        reports_dir: Path = REPORTS_DIR,
        backlog_seconds: float = BACKLOG_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = sessions
        self._pipeline = pipeline
        self._dir = reports_dir
        self._clock = clock
        self._cutoff = clock() - backlog_seconds
        self._seen: dict[str, float] = {}
        self._primed = False

    def scan_once(self) -> list[str]:
        """Reports handled this pass, by file name."""
        if not self._dir.is_dir():
            return []
        handled: list[str] = []
        clients: list[dict[str, Any]] | None = None
        for path in sorted(self._dir.glob("*.ips")):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._seen.get(path.name) == mtime:
                continue
            self._seen[path.name] = mtime
            if mtime < self._cutoff and not self._primed:
                continue
            header = read_header(path)
            if not header or str(header.get("bug_type")) not in CRASH_BUG_TYPES:
                continue
            clients = clients if clients is not None else self._sessions.list_clients()
            if self._deliver(path, clients):
                handled.append(path.name)
        self._primed = True
        return handled

    def _deliver(self, path: Path, clients: list[dict[str, Any]]) -> bool:
        parsed = read_report(path)
        if parsed is None:
            self._seen.pop(path.name, None)  # still being written; try again next pass
            return False
        client_id = match_client(clients, parsed["header"], parsed["report"])
        if client_id is None:
            return False
        try:
            self._pipeline.submit(client_id, event_from_report(parsed, path))
        except LookupError:
            return False
        return True

    async def run(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.scan_once)
                self._sessions.refresh_presence()
            except Exception:
                logger.exception("Crash report scan failed")
            await asyncio.sleep(POLL_SECONDS)
