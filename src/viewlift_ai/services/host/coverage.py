"""Code coverage from an .xcresult bundle via `xcrun xccov`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host.runner import HostError

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_LINE_RE = re.compile(r"^\s*(\d+):\s*(\*|\d+)")


def _xcresult(ctx: HostContext, args: dict[str, Any]) -> Path:
    raw = str(args.get("xcresultPath") or "")
    if raw:
        path = ctx.resolve_artifact(raw)
    else:
        results = sorted(
            (ctx.work_dir / "results").glob("*.xcresult"), key=lambda item: item.stat().st_mtime
        )
        if not results:
            raise HostError("No test results yet. Run test_sim, test_device, or test_macos.")
        path = results[-1]
    if path.suffix != ".xcresult":
        raise HostError("xcresultPath must be an .xcresult bundle.")
    return path


def _pct(value: Any) -> float:
    return round(float(value or 0) * 100, 1)


def report(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = _xcresult(ctx, args)
    data = ctx.run_json(["xcrun", "xccov", "view", "--report", "--json", str(path)], timeout=120)
    target_filter = str(args.get("target") or "").lower()
    show_files = bool(args.get("showFiles"))
    targets = []
    for target in data.get("targets", []) if isinstance(data, dict) else []:
        name = str(target.get("name", ""))
        if target_filter and target_filter not in name.lower():
            continue
        row: dict[str, Any] = {
            "name": name,
            "line_coverage": _pct(target.get("lineCoverage")),
            "covered_lines": target.get("coveredLines"),
            "executable_lines": target.get("executableLines"),
        }
        files = sorted(target.get("files", []), key=lambda item: item.get("lineCoverage", 0))
        if show_files:
            row["files"] = [
                {"path": item.get("path"), "line_coverage": _pct(item.get("lineCoverage"))}
                for item in files[:200]
            ]
        else:
            row["least_covered"] = [
                {"name": item.get("name"), "line_coverage": _pct(item.get("lineCoverage"))}
                for item in files[:8]
            ]
        targets.append(row)
    return {
        "xcresult": str(path),
        "line_coverage": _pct(data.get("lineCoverage") if isinstance(data, dict) else 0),
        "targets": targets,
    }


def _ranges(lines: list[int]) -> list[str]:
    out: list[str] = []
    start = prev = None
    for line in lines:
        if start is None:
            start = prev = line
        elif line == prev + 1:
            prev = line
        else:
            out.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = line
    if start is not None:
        out.append(f"{start}" if start == prev else f"{start}-{prev}")
    return out


def file_coverage(ctx: HostContext, args: dict[str, Any]) -> dict[str, Any]:
    path = _xcresult(ctx, args)
    wanted = str(args.get("file") or "").strip()
    if not wanted:
        raise HostError("file is required (a source path or file name).")
    listing = ctx.run_ok(
        ["xcrun", "xccov", "view", "--archive", "--file-list", str(path)], timeout=60
    )
    matches = [
        line.strip() for line in listing.stdout.splitlines() if line.strip().endswith(wanted)
    ]
    if not matches:
        raise HostError(f"No coverage for {wanted} in this result bundle.")
    source = matches[0]
    detail = ctx.run_ok(
        ["xcrun", "xccov", "view", "--archive", "--file", source, str(path)], timeout=60
    )
    uncovered: list[int] = []
    executable = 0
    for line in detail.stdout.splitlines():
        match = _LINE_RE.match(line)
        if not match or match.group(2) == "*":
            continue
        executable += 1
        if match.group(2) == "0":
            uncovered.append(int(match.group(1)))
    covered = executable - len(uncovered)
    return {
        "file": source,
        "executable_lines": executable,
        "covered_lines": covered,
        "line_coverage": round(covered / executable * 100, 1) if executable else 0.0,
        "uncovered_ranges": _ranges(uncovered)[:200],
    }


def register(toolkit: HostToolkit) -> None:
    ctx = toolkit.ctx
    toolkit.add("get_coverage_report", lambda args: report(ctx, args))
    toolkit.add("get_file_coverage", lambda args: file_coverage(ctx, args))
