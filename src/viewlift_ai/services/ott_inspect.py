"""Mac-side stack frame parsing for hang and crash events."""

from __future__ import annotations

import re
from typing import Any

_FRAME_RE = re.compile(
    r"^\s*(?P<index>\d+)\s+(?P<image>\S+)\s+(?P<address>0x[0-9a-fA-F]+)\s+"
    r"(?P<symbol>.+?)(?:\s+\+\s+\d+)?(?:\s+\((?P<file>[^:)]+\.(?:swift|m|mm)):(?P<line>\d+)\))?$"
)


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
