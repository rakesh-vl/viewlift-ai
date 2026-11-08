"""Plain-language diagnosis of one crash from the stack, the reason, and what the app did first.

The result is meant for two readers: a person looking at the dashboard, and an agent asked to fix
the crash. Both need the same things: what failed, where in the code, what led there, and where to
start. Nothing here guesses beyond the data; every sentence names its evidence.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.symbolicate import is_system_image

SNIPPET_RADIUS = 3
MAX_TIMELINE = 40
MAX_STEPS = 8
REPRO_WINDOW_SECONDS = 20.0
SEARCH_FILE_LIMIT = 40_000
SKIP_DIRS = frozenset(
    {
        ".git",
        ".build",
        "build",
        "DerivedData",
        "Pods",
        "Carthage",
        "node_modules",
        ".swiftpm",
        "SourcePackages",
        ".venv",
        "__pycache__",
    }
)
RUNTIME_FAILURE = "Swift runtime failure: "

# (substring in reason, symbol, or console, lower case) -> (headline, fix hint)
_KNOWN_FAILURES: tuple[tuple[str, str, str], ...] = (
    (
        "arithmetic overflow",
        "Arithmetic overflow: an integer calculation produced a value its type cannot hold.",
        "Check the operands at the crash line. Clamp or validate them, use a wider type, or use "
        "the overflow-reporting operators (`addingReportingOverflow`, `&+`) when wrapping is "
        "intended.",
    ),
    (
        "unexpectedly found nil",
        "Force unwrap of nil: an Optional that was expected to have a value was empty.",
        "Replace the `!` at the crash line with `guard let` or `if let`, and handle the missing "
        "value. Find out why it was nil (late data, failed request, wrong cell type).",
    ),
    (
        "force unwrapped a nil value",
        "Force unwrap of nil: an Optional that was expected to have a value was empty.",
        "Replace the `!` at the crash line with `guard let` or `if let`, and handle the "
        "missing value.",
    ),
    (
        "index out of range",
        "Index out of range: code read an array element that does not exist.",
        "Check the index against `indices` before reading. Look for a count that changed between "
        "the check and the access (data reload, async update).",
    ),
    (
        "division by zero",
        "Division by zero.",
        "Guard the divisor at the crash line.",
    ),
    (
        "unrecognized selector",
        "Unrecognized selector: a message was sent to an object that does not implement it.",
        "Check the object's real type at the crash line, a stale outlet or target-action, or an "
        "object that was released and replaced.",
    ),
    (
        "invalid update",
        "Invalid table or collection view update: the row or item counts did not match the data.",
        "Change the data source and the view in the same batch, on the main thread.",
    ),
    (
        "deallocated instance",
        "An object was used after it was deallocated.",
        "Look for `unowned` references and delegates that outlive their owner.",
    ),
    (
        "fatal error",
        "`fatalError` was called.",
        "Read the message next to the crash line and handle that case instead of stopping the app.",
    ),
    (
        "precondition",
        "A precondition failed.",
        "The condition at the crash line was false. Find out which input broke it.",
    ),
    (
        "assertion",
        "An assertion failed.",
        "The asserted condition at the crash line was false. Find out which input broke it.",
    ),
)

_SIGNALS: dict[str, tuple[str, str]] = {
    "SIGSEGV": (
        "Invalid memory access: the app read or wrote memory it does not own.",
        "Look for objects used after release, unsafe pointers, or UIKit touched off the main "
        "thread.",
    ),
    "SIGBUS": (
        "Bad memory access: the address was misaligned or no longer mapped.",
        "Look for unsafe pointers and memory used after it was freed.",
    ),
    "SIGTRAP": (
        "Swift runtime trap: Swift stopped the app on purpose because a safety check failed.",
        "The check that failed is at the crash line, usually a force unwrap, overflow, or "
        "bounds check.",
    ),
    "SIGILL": (
        "Swift runtime trap: Swift stopped the app on purpose because a safety check failed.",
        "The check that failed is at the crash line, usually a force unwrap, overflow, or "
        "bounds check.",
    ),
    "SIGABRT": (
        "The app aborted, usually from an uncaught exception, `fatalError`, or a failed assertion.",
        "Read the reason and the first app frame. Exceptions name what was wrong.",
    ),
    "SIGFPE": ("Arithmetic fault, such as an integer division by zero.", "Guard the divisor."),
}

_EXCEPTIONS: dict[str, str] = {
    "NSInvalidArgumentException": (
        "Usually a nil inserted into a collection, an unrecognized selector, or a bad argument "
        "to a Foundation API."
    ),
    "NSRangeException": "An index or range outside a collection or string.",
    "NSInternalInconsistencyException": (
        "UIKit state did not match what it was told, often a table or collection view update."
    ),
    "NSGenericException": "Often a collection mutated while being enumerated.",
    "NSUnknownKeyException": (
        "A key-value coding key that the object does not have, often a stale outlet."
    ),
}


def _text(value: object) -> str:
    return str(value or "").strip()


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _frames(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    frames = payload.get("frames") or payload.get("exception_frames") or []
    return [frame for frame in frames if isinstance(frame, Mapping)]


def _number(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


# ----- crash site --------------------------------------------------------------------------


def stack_line(frame: Mapping[str, Any]) -> str:
    """`3  UIHierarchyCapture.swift:63  UIHierarchyCapture.screenshot(of:budget:)  [OneApp]`."""
    where = ""
    if frame.get("file"):
        line = frame.get("line")
        where = f"{frame['file']}:{line}" if line is not None else str(frame["file"])
    symbol = str(frame.get("symbol") or frame.get("raw") or frame.get("address") or "?")
    image = f"  [{frame['image']}]" if frame.get("image") else ""
    inlined = " (inlined)" if frame.get("inlined") else ""
    return f"{frame.get('index', '?')}  {where or '-'}  {symbol}{inlined}{image}".strip()


def _is_runtime_failure(frame: Mapping[str, Any]) -> bool:
    return _text(frame.get("symbol")).startswith(RUNTIME_FAILURE)


def _is_app_frame(frame: Mapping[str, Any], executable: str, images: dict[str, str]) -> bool:
    image = _text(frame.get("image"))
    if executable and image == executable:
        return True
    path = images.get(_text(frame.get("image_uuid")).upper(), "")
    return bool(frame.get("file")) and bool(path) and not is_system_image(path)


def _image_paths(payload: Mapping[str, Any]) -> dict[str, str]:
    out = {}
    for image in payload.get("images") or []:
        if isinstance(image, Mapping) and image.get("uuid"):
            out[_text(image.get("uuid")).upper()] = _text(image.get("path"))
    return out


def app_frames(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    executable = _text(payload.get("executable"))
    images = _image_paths(payload)
    return [
        frame
        for frame in _frames(payload)
        if not _is_runtime_failure(frame) and _is_app_frame(frame, executable, images)
    ]


def crash_frame(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The first app frame with a file, else the first app frame, else the top frame."""
    candidates = app_frames(payload)
    with_file = [frame for frame in candidates if frame.get("file")]
    if with_file:
        return with_file[0]
    if candidates:
        return candidates[0]
    frames = _frames(payload)
    return frames[0] if frames else None


def _within(path: Path, roots: Iterable[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(root) for root in roots)


def find_source(frame: Mapping[str, Any], roots: list[Path]) -> Path | None:
    """The frame's file on this Mac, only when it sits inside a project root."""
    raw = _text(frame.get("path"))
    if raw:
        path = Path(raw)
        if path.is_file() and _within(path, roots):
            return path
    name = _text(frame.get("file")) or (os.path.basename(raw) if raw else "")
    return _search(name, roots) if name else None


_SEARCH_CACHE: dict[tuple[str, tuple[Path, ...]], Path | None] = {}


def _search(name: str, roots: list[Path]) -> Path | None:
    key = (name, tuple(roots))
    cached = _SEARCH_CACHE.get(key)
    if cached is not None and cached.is_file():
        return cached
    found = _walk(name, roots)
    if found is not None:
        _SEARCH_CACHE[key] = found
        if len(_SEARCH_CACHE) > 256:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    return found


def _walk(name: str, roots: list[Path]) -> Path | None:
    seen = 0
    for root in roots:
        for folder, dirs, files in os.walk(root):
            dirs[:] = [item for item in dirs if item not in SKIP_DIRS and not item.startswith(".")]
            seen += len(files)
            if name in files:
                return Path(folder) / name
            if seen > SEARCH_FILE_LIMIT:
                return None
    return None


def snippet(path: Path, line: int, radius: int = SNIPPET_RADIUS) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if not 1 <= line <= len(lines):
        return []
    start, end = max(1, line - radius), min(len(lines), line + radius)
    return [
        {"line": number, "code": lines[number - 1], "crash": number == line}
        for number in range(start, end + 1)
    ]


def _site(payload: Mapping[str, Any], roots: list[Path]) -> dict[str, Any] | None:
    frame = crash_frame(payload)
    if frame is None:
        return None
    site: dict[str, Any] = {
        "file": frame.get("file"),
        "line": frame.get("line"),
        "function": frame.get("symbol") or frame.get("raw"),
        "class": frame.get("class") or None,
        "image": frame.get("image"),
        "frame_index": frame.get("index"),
    }
    line = frame.get("line")
    source = find_source(frame, roots) if frame.get("file") or frame.get("path") else None
    if source is not None:
        site["path"] = str(source)
        if isinstance(line, int):
            site["snippet"] = snippet(source, line)
    elif frame.get("path"):
        site["path"] = frame.get("path")
        site["snippet_note"] = "Source file is outside the project roots, so it was not read."
    return site


# ----- what failed -------------------------------------------------------------------------


def _evidence(payload: Mapping[str, Any]) -> list[str]:
    """Text that may name the failure: runtime failure frames, the reason, and console errors."""
    texts = [_text(frame.get("symbol")) for frame in _frames(payload) if _is_runtime_failure(frame)]
    # The SDK's signal record only has a generic summary of the signal, which names no failure.
    if payload.get("source") != "sdk-signal":
        texts += [_text(payload.get("reason")), _text(payload.get("name"))]
    console = _mapping(payload.get("context")).get("console") or []
    for line in console[-10:]:
        message = _text(_mapping(line).get("message"))
        if "fatal error" in message.lower() or "terminating" in message.lower():
            texts.append(message)
    return [item for item in texts if item]


def _failure(payload: Mapping[str, Any]) -> tuple[str, str, str]:
    """(headline, fix hint, evidence) in plain language."""
    evidence = _evidence(payload)
    for text in evidence:
        lowered = text.lower()
        for needle, headline, fix in _KNOWN_FAILURES:
            if needle in lowered:
                return headline, fix, text
    name = _text(payload.get("name"))
    if payload.get("kind") == "exception" or name.startswith("NS"):
        meaning = _EXCEPTIONS.get(name, "An Objective-C exception was not caught.")
        reason = _text(payload.get("reason"))
        headline = f"Uncaught {name or 'exception'}: {reason}" if reason else f"Uncaught {name}."
        return headline, meaning, reason
    signal = _text(payload.get("signal")) or name.split(" ")[-1].strip("()")
    for key, (headline, fix) in _SIGNALS.items():
        if key in signal or key in name:
            return headline, fix, signal
    return "The app crashed.", "Start from the first app frame in the stack.", ""


# ----- what led there ----------------------------------------------------------------------


def _crash_time(payload: Mapping[str, Any]) -> float | None:
    return _number(payload.get("timestamp"))


def timeline(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Screens, taps, presses, requests, and console errors, oldest first, timed to the crash."""
    context = _mapping(payload.get("context"))
    captured = _number(context.get("captured_at"))
    crash = _crash_time(payload) or captured
    if crash is None:
        return []
    rows: list[tuple[float, dict[str, Any]]] = []

    def add(when: float | None, kind: str, text: str, **extra: Any) -> None:
        if when is None or not text:
            return
        rows.append(
            (when, {"kind": kind, "text": text, "before_crash_s": round(crash - when, 2), **extra})
        )

    for screen in context.get("screens") or []:
        item = _mapping(screen)
        title = _text(item.get("title"))
        name = _text(item.get("screen_class"))
        add(
            _number(item.get("time")), "screen", f"Opened {name}" + (f" ({title})" if title else "")
        )
    for action in context.get("actions") or []:
        item = _mapping(action)
        ago = _number(item.get("ago_ms"))
        when = captured - ago / 1000 if captured is not None and ago is not None else None
        add(when, item.get("kind") or "tap", _action_text(item), screen=item.get("screen_class"))
    for press in context.get("presses") or []:
        item = _mapping(press)
        add(
            _number(item.get("time")), "press", f"Pressed {_text(item.get('button'))} on the remote"
        )
    for request in context.get("requests") or []:
        item = _mapping(request)
        status = item.get("status")
        failed = bool(item.get("error")) or (isinstance(status, int | float) and status >= 400)
        label = f"{_text(item.get('method'))} {_text(item.get('host'))}{_text(item.get('path'))}"
        result = _text(item.get("error")) or (
            f"{int(status)}" if isinstance(status, int | float) else ""
        )
        add(
            _number(item.get("time")),
            "request-failed" if failed else "request",
            f"{label} -> {result or 'no status'}",
        )
    for line in context.get("console") or []:
        item = _mapping(line)
        message = _text(item.get("message"))
        ago = _number(item.get("ago_ms"))
        if not _is_error_line(message) or captured is None or ago is None:
            continue
        add(captured - ago / 1000, "console", message[:200])
    add(crash, "crash", _text(payload.get("name")) or "Crash")
    rows.sort(key=lambda row: row[0])
    return [row for _, row in rows][-MAX_TIMELINE:]


def _is_error_line(message: str) -> bool:
    lowered = message.lower()
    return any(word in lowered for word in ("error", "fatal", "fail", "exception", "warning"))


def _action_text(item: Mapping[str, Any]) -> str:
    kind = _text(item.get("kind")) or "tap"
    label = _text(item.get("label"))
    target = _text(item.get("target_class"))
    what = f'"{label}"' if label else (target or "the screen")
    if kind == "scroll":
        direction = _text(item.get("direction"))
        return f"Scrolled {direction} on {target or 'the screen'}".replace("  ", " ")
    return f"Tapped {what}" + (f" ({target})" if label and target else "")


def steps_to_reproduce(rows: list[dict[str, Any]]) -> list[str]:
    """Screens and inputs in the last seconds before the crash, in order, without repeats."""
    steps: list[str] = []
    for row in rows:
        if row["kind"] == "crash":
            steps.append(f"The app crashes ({row['text']}).")
            break
        if row["before_crash_s"] > REPRO_WINDOW_SECONDS:
            continue
        if row["kind"] in {"screen", "tap", "scroll", "press", "request-failed"}:
            text = (
                row["text"] if row["kind"] != "request-failed" else f"Request fails: {row['text']}"
            )
            if not steps or steps[-1] != text:
                steps.append(text)
    return steps[-MAX_STEPS:]


def _last_input(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    inputs = [row for row in rows if row["kind"] in {"tap", "scroll", "press"}]
    return inputs[-1] if inputs else None


# ----- assembly ----------------------------------------------------------------------------


def _where(site: Mapping[str, Any] | None) -> str:
    if not site:
        return "in an unknown location"
    function = _text(site.get("function"))
    if site.get("file"):
        line = site.get("line")
        place = f"{site['file']}:{line}" if line is not None else str(site["file"])
        return f"at {place}" + (f" in `{function}`" if function else "")
    return f"in `{function}`" if function else "in an unknown location"


def _root_cause(
    headline: str, evidence: str, site: Mapping[str, Any] | None, rows: list[dict[str, Any]]
) -> str:
    parts = [f"{headline.rstrip('.')} {_where(site)}."]
    if evidence and evidence.lower() not in headline.lower():
        parts.append(f"Evidence: {evidence[:240]}.")
    last = _last_input(rows)
    if last:
        parts.append(
            f"The last input was: {last['text']}, {last['before_crash_s']} s before the crash."
        )
    failed = [
        row for row in rows if row["kind"] == "request-failed" and row["before_crash_s"] <= 10
    ]
    if failed:
        parts.append(
            f"A request failed {failed[-1]['before_crash_s']} s earlier ({failed[-1]['text']}). "
            "Check how that response, or its absence, reaches the crash line."
        )
    return " ".join(parts)


def _environment(payload: Mapping[str, Any], client: Mapping[str, Any] | None) -> dict[str, Any]:
    context_env = dict(_mapping(_mapping(payload.get("context")).get("environment")))
    performance = _mapping(_mapping(payload.get("context")).get("performance"))
    info = client or {}
    out: dict[str, Any] = {
        "app": info.get("app_name") or context_env.get("app_name"),
        "bundle_id": info.get("bundle_id")
        or context_env.get("bundle_id")
        or payload.get("bundle_id"),
        "version": info.get("app_version") or context_env.get("app_version"),
        "build": info.get("build") or context_env.get("build"),
        "platform": info.get("platform"),
        "os_version": info.get("os_version") or context_env.get("os_version"),
        "device": info.get("device_model") or context_env.get("device_model"),
        "simulator": info.get("is_simulator", context_env.get("is_simulator")),
        "arch": payload.get("arch"),
        "thread": payload.get("thread"),
        "app_state": context_env.get("app_state"),
        "thermal_state": context_env.get("thermal_state") or performance.get("thermal_state"),
        "low_power_mode": context_env.get("low_power_mode"),
        "memory_mb": _mb(performance.get("memory_bytes")),
        "cpu_percent": performance.get("cpu_percent"),
    }
    return {key: value for key, value in out.items() if value not in (None, "")}


def _mb(value: object) -> float | None:
    number = _number(value)
    return round(number / 1_048_576, 1) if number else None


def diagnose(
    payload: Mapping[str, Any],
    roots: list[Path],
    client: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    headline, fix, evidence = _failure(payload)
    site = _site(payload, roots)
    rows = timeline(payload)
    fix_area = fix
    if site and site.get("file"):
        fix_area = f"{fix} Start at {_where(site)}."
    return {
        "headline": headline,
        "evidence": evidence or None,
        "crash_site": site,
        "root_cause": _root_cause(headline, evidence, site, rows),
        "suggested_fix_area": fix_area,
        "steps_to_reproduce": steps_to_reproduce(rows),
        "timeline": rows,
        "app_stack": [stack_line(frame) for frame in app_frames(payload)[:12]],
        "environment": _environment(payload, client),
    }


def make_diagnoser(
    roots: Callable[[], list[Path]],
    clients: Callable[[str], Mapping[str, Any] | None] | None = None,
) -> Callable[[str, dict[str, object]], dict[str, object]]:
    """The `diagnose_crash` hook for `DebugSessionService`."""

    def run(client_id: str, payload: dict[str, object]) -> dict[str, object]:
        client = clients(client_id) if clients is not None else None
        return diagnose(payload, roots(), client)

    return run
