"""Markdown brief of one crash, written so a coding agent can fix it without the dashboard."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from viewlift_ai.services.host.crash_diagnosis import diagnose, stack_line

MAX_STACK = 40
MAX_CONSOLE = 20


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _code_block(site: Mapping[str, Any]) -> list[str]:
    rows = site.get("snippet") or []
    if not rows:
        note = site.get("snippet_note") or "Source was not found inside the project roots."
        return [f"_{note}_"]
    width = len(str(rows[-1]["line"]))
    out = ["```swift"]
    for row in rows:
        marker = ">" if row.get("crash") else " "
        out.append(f"{marker} {str(row['line']).rjust(width)} | {row['code']}")
    out.append("```")
    return out


def brief_markdown(
    crash_id: str,
    payload: Mapping[str, Any],
    *,
    client: Mapping[str, Any] | None = None,
    replay_url: str | None = None,
) -> str:
    diagnosis = _mapping(payload.get("diagnosis")) or diagnose(payload, [], client)
    site = _mapping(diagnosis.get("crash_site"))
    lines = [f"# Crash: {diagnosis.get('headline') or payload.get('name') or 'Crash'}", ""]
    lines += [
        f"- Crash id: `{crash_id}`",
        f"- Source: {payload.get('source') or '?'}"
        + (" (recorded in the previous run)" if payload.get("previous_session") else ""),
        f"- Signal or exception: {payload.get('name') or payload.get('signal') or '?'}",
        f"- Thread: {payload.get('thread') or '?'}",
    ]
    if payload.get("reason"):
        lines.append(f"- Reason: {payload['reason']}")
    symbolication = _mapping(payload.get("symbolication"))
    if symbolication.get("status"):
        missing = symbolication.get("missing_dsym") or []
        lines.append(
            f"- Symbolication: {symbolication['status']}"
            + (f" (missing dSYM for {', '.join(map(str, missing))})" if missing else "")
        )
    lines += [
        "",
        "## Root cause hypothesis",
        "",
        str(diagnosis.get("root_cause") or "Unknown."),
        "",
    ]
    lines += ["## Crash site", ""]
    if site:
        where = f"{site.get('file') or '?'}:{site.get('line') or '?'}"
        lines.append(f"`{where}` in `{site.get('function') or '?'}`")
        if site.get("path"):
            lines.append(f"Path: `{site['path']}`")
        lines += ["", *_code_block(site)]
    else:
        lines.append("_No app frame in the stack._")
    lines += ["", "## Suggested fix area", "", str(diagnosis.get("suggested_fix_area") or ""), ""]
    lines += _stack_section(payload, diagnosis)
    steps = diagnosis.get("steps_to_reproduce") or []
    lines += ["## Steps to reproduce", ""]
    lines += [f"{number}. {step}" for number, step in enumerate(steps, 1)] or [
        "_No inputs recorded._"
    ]
    lines += ["", "## Timeline (seconds before the crash)", ""]
    for row in diagnosis.get("timeline") or []:
        lines.append(f"- `-{row.get('before_crash_s')}s` {row.get('kind')}: {row.get('text')}")
    lines += _context_sections(payload)
    lines += ["", "## Environment", ""]
    for key, value in _mapping(diagnosis.get("environment")).items():
        lines.append(f"- {key}: {value}")
    replay = _mapping(payload.get("replay"))
    lines += ["", "## Replay", ""]
    if replay.get("status") == "ready" and replay_url:
        lines.append(f"Video of the last seconds before the crash, taps drawn: {replay_url}")
    else:
        lines.append(f"Replay status: {replay.get('status') or 'none'}.")
    lines += [
        "",
        "## How to verify the fix",
        "",
        "1. Fix the code at the crash site; keep the change small.",
        "2. Build the Debug configuration for every iOS and tvOS target.",
        "3. Run the app without the debugger attached and repeat the steps above.",
        "4. Call `crash_reports` for this app and confirm no new crash appeared.",
        "",
    ]
    return "\n".join(lines)


def _stack_section(payload: Mapping[str, Any], diagnosis: Mapping[str, Any]) -> list[str]:
    out = ["## Stack (app frames first)", ""]
    app = diagnosis.get("app_stack") or []
    if app:
        out += ["App frames:", "", "```", *app, "```", ""]
    frames = [frame for frame in payload.get("frames") or [] if isinstance(frame, Mapping)]
    out += ["Crashed thread:", "", "```", *[stack_line(frame) for frame in frames[:MAX_STACK]]]
    if len(frames) > MAX_STACK:
        out.append(f"... {len(frames) - MAX_STACK} more frames")
    out += ["```", ""]
    return out


def _context_sections(payload: Mapping[str, Any]) -> list[str]:
    context = _mapping(payload.get("context"))
    out: list[str] = []
    requests = [item for item in context.get("requests") or [] if isinstance(item, Mapping)]
    if requests:
        out += ["", "## Last requests", ""]
        for item in requests:
            status = item.get("error") or item.get("status") or "no status"
            took = item.get("duration_ms")
            timing = f" in {round(float(took))} ms" if isinstance(took, int | float) else ""
            out.append(
                f"- {item.get('method')} {item.get('host')}{item.get('path')} -> {status}{timing}"
            )
    console = [item for item in context.get("console") or [] if isinstance(item, Mapping)]
    if console:
        out += ["", "## Console (last lines)", "", "```"]
        out += [str(item.get("message") or "") for item in console[-MAX_CONSOLE:]]
        out.append("```")
    return out
