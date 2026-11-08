"""SwiftUI preview discovery in source, and snapshot baselines compared pixel by pixel."""

from __future__ import annotations

import base64
import binascii
import os
import re
import struct
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from viewlift_ai.services.host.runner import HostError

if TYPE_CHECKING:
    from viewlift_ai.services.host.context import HostContext
    from viewlift_ai.services.host.toolkit import HostToolkit

_PREVIEW_MACRO_RE = re.compile(r'^\s*#Preview\s*(?:\(\s*"([^"]*)")?')
_PREVIEW_PROVIDER_RE = re.compile(
    r"^\s*(?:struct|final class|class)\s+(\w+)\s*:\s*[^{]*PreviewProvider"
)
_VIEW_TYPE_RE = re.compile(
    r"^\s*(?:public\s+|internal\s+|private\s+|fileprivate\s+)?struct\s+(\w+)\s*:\s*[^{]*\bView\b"
)
_SKIP_DIRS = frozenset(
    {".git", ".build", "DerivedData", "Pods", "Carthage", "node_modules", "build"}
)
_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")
MAX_FILES = 5000
MAX_PNG_BYTES = 12_000_000


def scan_previews(base: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    files = 0
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for filename in filenames:
            if not filename.endswith(".swift"):
                continue
            files += 1
            if files > MAX_FILES:
                return rows
            path = Path(dirpath) / filename
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            views = [m.group(1) for line in lines if (m := _VIEW_TYPE_RE.match(line))]
            unnamed = 0
            for number, line in enumerate(lines, start=1):
                macro = _PREVIEW_MACRO_RE.match(line)
                provider = _PREVIEW_PROVIDER_RE.match(line)
                if macro:
                    name = macro.group(1)
                    if not name:
                        unnamed += 1
                        base_name = views[0] if views else path.stem
                        name = base_name if unnamed == 1 else f"{base_name} {unnamed}"
                    body = " ".join(item.strip() for item in lines[number : number + 3])
                    rows.append(
                        {
                            "name": name,
                            "kind": "#Preview",
                            "file": str(path),
                            "line": number,
                            "snippet": body[:160],
                        }
                    )
                elif provider:
                    rows.append(
                        {
                            "name": provider.group(1).removesuffix("_Previews"),
                            "kind": "PreviewProvider",
                            "file": str(path),
                            "line": number,
                        }
                    )
    return rows


def decode_png(data: bytes) -> tuple[int, int, int, bytes]:
    """Return width, height, channels, and raw 8-bit pixels for non-interlaced RGB(A)/gray PNGs."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HostError("Not a PNG.")
    offset = 8
    width = height = 0
    bit_depth = color_type = interlace = 0
    idat = bytearray()
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset : offset + 8])
        chunk = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if kind == b"IHDR":
            width, height, bit_depth, color_type, _c, _f, interlace = struct.unpack(
                ">IIBBBBB", chunk
            )
        elif kind == b"IDAT":
            idat.extend(chunk)
        elif kind == b"IEND":
            break
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
    if bit_depth != 8 or channels is None or interlace:
        raise HostError("Only 8-bit non-interlaced gray, RGB, or RGBA PNGs are compared.")
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    out = bytearray(height * stride)
    prev = bytearray(stride)
    pos = 0
    for row in range(height):
        filter_type = raw[pos]
        line = bytearray(raw[pos + 1 : pos + 1 + stride])
        pos += 1 + stride
        for i in range(stride):
            left = line[i - channels] if i >= channels else 0
            up = prev[i]
            if filter_type == 1:
                line[i] = (line[i] + left) & 0xFF
            elif filter_type == 2:
                line[i] = (line[i] + up) & 0xFF
            elif filter_type == 3:
                line[i] = (line[i] + ((left + up) >> 1)) & 0xFF
            elif filter_type == 4:
                upper_left = prev[i - channels] if i >= channels else 0
                p = left + up - upper_left
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upper_left)
                pred = left if pa <= pb and pa <= pc else (up if pb <= pc else upper_left)
                line[i] = (line[i] + pred) & 0xFF
        out[row * stride : (row + 1) * stride] = line
        prev = line
    return width, height, channels, bytes(out)


def compare_png(baseline: bytes, candidate: bytes, *, tolerance: int = 8) -> dict[str, Any]:
    bw, bh, bc, bpx = decode_png(baseline)
    cw, ch, cc, cpx = decode_png(candidate)
    if (bw, bh) != (cw, ch):
        return {"match": False, "reason": "size", "baseline": [bw, bh], "candidate": [cw, ch]}
    if bc != cc:
        return {"match": False, "reason": "channels"}
    changed = 0
    min_x, min_y, max_x, max_y = bw, bh, -1, -1
    stride = bw * bc
    for y in range(bh):
        row_b = bpx[y * stride : (y + 1) * stride]
        row_c = cpx[y * stride : (y + 1) * stride]
        if row_b == row_c:
            continue
        for x in range(bw):
            start = x * bc
            if any(abs(row_b[start + k] - row_c[start + k]) > tolerance for k in range(bc)):
                changed += 1
                min_x, min_y = min(min_x, x), min(min_y, y)
                max_x, max_y = max(max_x, x), max(max_y, y)
    total = bw * bh or 1
    out: dict[str, Any] = {
        "match": changed == 0,
        "changed_pixels": changed,
        "changed_percent": round(changed / total * 100, 3),
        "size": [bw, bh],
    }
    if changed:
        out["changed_region"] = {
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x + 1,
            "height": max_y - min_y + 1,
        }
    return out


class PreviewBaselines:
    def __init__(self, ctx: HostContext) -> None:
        self.ctx = ctx

    def _dir(self, kind: str, suite: str) -> Path:
        key = _KEY_RE.sub("_", suite or "default")[:80]
        path = self.ctx.work_dir / "previews" / key / kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _file(name: str, variant: str) -> str:
        label = _KEY_RE.sub("_", f"{name}__{variant or 'default'}")[:160]
        return f"{label}.png"

    def list_previews(self, args: dict[str, Any]) -> dict[str, Any]:
        raw = str(args.get("path") or "")
        bases = [self.ctx.roots.resolve(raw)] if raw else self.ctx.roots.roots()
        if not bases:
            self.ctx.roots.resolve("")
        rows: list[dict[str, Any]] = []
        for base in bases:
            rows.extend(scan_previews(base))
        text = str(args.get("filter") or "").lower()
        if text:
            rows = [
                row for row in rows if text in row["name"].lower() or text in row["file"].lower()
            ]
        return {
            "previews": rows[:500],
            "count": len(rows),
            "note": (
                "Source previews are listed from Swift files. render_preview draws previews the "
                "running app registered with ViewliftPreviews.register."
            ),
        }

    def compare(self, args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if not name:
            raise HostError("name is required.")
        try:
            candidate = base64.b64decode(str(args.get("base64") or ""), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HostError("base64 must be a PNG.") from exc
        if not candidate or len(candidate) > MAX_PNG_BYTES:
            raise HostError("PNG is empty or too large.")
        suite = str(args.get("suite") or "")
        variant = str(args.get("variant") or "")
        filename = self._file(name, variant)
        (self._dir("candidates", suite) / filename).write_bytes(candidate)
        baseline_path = self._dir("baselines", suite) / filename
        if not baseline_path.is_file():
            baseline_path.write_bytes(candidate)
            return {
                "name": name,
                "variant": variant,
                "status": "recorded",
                "baseline": str(baseline_path),
            }
        result = compare_png(
            baseline_path.read_bytes(), candidate, tolerance=int(args.get("tolerance", 8))
        )
        threshold = float(args.get("maxChangedPercent", 0.0))
        passed = result.get("match") or (
            "changed_percent" in result and result["changed_percent"] <= threshold
        )
        return {
            "name": name,
            "variant": variant,
            "status": "passed" if passed else "changed",
            **result,
            "baseline": str(baseline_path),
        }

    def approve(self, args: dict[str, Any]) -> dict[str, Any]:
        suite = str(args.get("suite") or "")
        names = args.get("names")
        candidates = self._dir("candidates", suite)
        baselines = self._dir("baselines", suite)
        approved = []
        files = (
            [
                self._file(str(item.get("name")), str(item.get("variant") or ""))
                for item in names
                if isinstance(item, dict)
            ]
            if isinstance(names, list) and names
            else [item.name for item in candidates.glob("*.png")]
        )
        for filename in files:
            source = candidates / filename
            if source.is_file():
                (baselines / filename).write_bytes(source.read_bytes())
                approved.append(filename)
        return {"approved": approved, "suite": suite or "default"}


def register(toolkit: HostToolkit) -> None:
    store = PreviewBaselines(toolkit.ctx)
    toolkit.add("list_previews", store.list_previews)
    toolkit.add("preview_compare", store.compare)
    toolkit.add("approve_preview_baseline", store.approve, mutating=True)
