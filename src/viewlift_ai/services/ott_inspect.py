"""Mac-side OTT inspect helpers: SPM pins + stack frame parsing."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_DEFAULT_APPCMS = Path(
    "/Users/nexg/Documents/Viewlift/Main_Projects/Clone_2/Untitled/AppCMS"
)

_FRAME_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+(?P<image>\S+)\s+(?P<address>0x[0-9a-fA-F]+)\s+"
    r"(?P<symbol>.+?)(?:\s+\+\s+\d+)?(?:\s+\((?P<file>[^:)]+\.(?:swift|m|mm)):(?P<line>\d+)\))?$"
)


def resolve_appcms_root(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    env = Path(
        __import__("os").environ.get("VIEWLIFT_APPCMS_ROOT", str(_DEFAULT_APPCMS))
    ).expanduser()
    return env.resolve()


def load_spm_packages(appcms_root: Path | None = None) -> dict[str, Any]:
    root = appcms_root or resolve_appcms_root()
    candidates = [
        root
        / "AppCMS.xcworkspace"
        / "xcshareddata"
        / "swiftpm"
        / "Package.resolved",
        root
        / "AppCMS.xcodeproj"
        / "project.xcworkspace"
        / "xcshareddata"
        / "swiftpm"
        / "Package.resolved",
    ]
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        return {
            "ok": False,
            "root": str(root),
            "packages": [],
            "error": "Package.resolved not found",
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    pins = data.get("pins") or []
    packages = []
    for pin in pins:
        state = pin.get("state") or {}
        packages.append(
            {
                "identity": pin.get("identity") or "",
                "location": pin.get("location") or "",
                "version": state.get("version") or "",
                "revision": (state.get("revision") or "")[:12],
                "kind": pin.get("kind") or "",
            }
        )
    packages.sort(key=lambda row: row["identity"])
    return {
        "ok": True,
        "root": str(root),
        "path": str(path),
        "count": len(packages),
        "packages": packages,
    }


def parse_stack_frame(line: str) -> dict[str, Any]:
    match = _FRAME_RE.match(line.strip())
    if not match:
        return {"raw": line, "parsed": False}
    symbol = (match.group("symbol") or "").strip()
    class_name = symbol.split(".", 1)[0] if symbol else ""
    file_name = match.group("file")
    line_no = match.group("line")
    return {
        "parsed": True,
        "index": int(match.group("index")),
        "image": match.group("image"),
        "address": match.group("address"),
        "symbol": symbol,
        "class": class_name,
        "file": file_name,
        "line": int(line_no) if line_no else None,
        "raw": line,
    }


def enrich_frames(frames: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for frame in frames or []:
        if not isinstance(frame, dict):
            continue
        raw = str(frame.get("raw") or "")
        parsed = parse_stack_frame(raw) if raw else {"parsed": False}
        merged = {**parsed, **{k: v for k, v in frame.items() if v not in (None, "")}}
        out.append(merged)
    return out
