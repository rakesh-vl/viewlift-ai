"""Resolve symbol, file, and line for crash frames with dSYMs and `atos`."""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.runner import CommandResult, HostError, run
from viewlift_ai.services.package_detect import derived_data_root

Runner = Callable[..., CommandResult]

INLINE_DELIMITER = "|||"
SCAN_DEPTH = 7
SCAN_INTERVAL_SECONDS = 20.0
ATOS_CHUNK = 200
SYSTEM_PREFIXES = (
    "/System/",
    "/usr/lib/",
    "/usr/libexec/",
    "/Library/Developer/CoreSimulator/Volumes/",
    "/Library/Developer/CoreSimulator/Profiles/",
    "/Library/Apple/",
)
_UUID_LINE = re.compile(r"^UUID:\s+([0-9A-Fa-f-]{36})\s+\(([^)]+)\)\s+(.+)$")
_ATOS_LINE = re.compile(
    r"^(?P<symbol>.+?) \(in (?P<image>[^)]+)\)(?: \((?P<file>.+?):(?P<line>\d+)\))?$"
)
_SKIP_DESCENT = (".app", ".framework", ".build", ".git", "node_modules")

LookupKey = tuple[str, str]
AnswerKey = tuple[str, str, str]


def is_system_image(path: str) -> bool:
    """System libraries have no dSYM here, and `atos` cannot read the simulator runtime cache."""
    return not path or path == "?" or path.startswith(SYSTEM_PREFIXES) or "/RuntimeRoot/" in path


def parse_uuid_lines(text: str) -> list[tuple[str, str, Path]]:
    """`dwarfdump --uuid` output as (uuid, arch, path)."""
    rows = []
    for line in text.splitlines():
        match = _UUID_LINE.match(line.strip())
        if match:
            rows.append((match.group(1).upper(), match.group(2), Path(match.group(3))))
    return rows


def parse_atos_line(line: str, delimiter: str = INLINE_DELIMITER) -> list[dict[str, Any]]:
    """One atos answer. Inlined frames come innermost first, joined by `delimiter`."""
    out: list[dict[str, Any]] = []
    for part in line.strip().split(delimiter):
        match = _ATOS_LINE.match(part.strip())
        if not match:
            continue
        entry: dict[str, Any] = {"symbol": match.group("symbol")}
        if match.group("file"):
            entry["path"] = match.group("file")
            entry["file"] = os.path.basename(match.group("file"))
            entry["line"] = int(match.group("line"))
        out.append(entry)
    return out


def class_of(symbol: str) -> str:
    """`static Type.method(arg:) -> Int` gives `Type`."""
    head = symbol.split(" in ", 1)[-1] if " in " in symbol else symbol
    head = head.removeprefix("static ").split("(", 1)[0]
    parts = [item for item in head.split(".") if item]
    return parts[-2] if len(parts) >= 2 else ""


@dataclass(slots=True)
class DwarfIndex:
    entries: dict[LookupKey, Path] = field(default_factory=dict)
    scanned_at: float = 0.0
    seen: set[Path] = field(default_factory=set)


class Symbolicator:
    """Finds a dSYM by image UUID and asks `atos` for file and line, inlined frames included."""

    def __init__(
        self,
        *,
        runner: Runner = run,
        search_dirs: Callable[[], Iterable[Path]] | None = None,
    ) -> None:
        self._runner = runner
        self._search_dirs = search_dirs or default_search_dirs
        self._index = DwarfIndex()
        self._lock = threading.Lock()

    # ----- dSYM lookup -------------------------------------------------------------------

    def dwarf_for(self, uuid: str, arch: str, image_path: str = "") -> Path | None:
        key = (uuid.upper(), arch)
        with self._lock:
            known = self._index.entries.get(key)
        if known is not None and known.exists():
            return known
        found = self._spotlight(key) or self._scan(key) or self._on_disk(key, image_path)
        if found is not None:
            with self._lock:
                self._index.entries[key] = found
        return found

    def _spotlight(self, key: LookupKey) -> Path | None:
        try:
            result = self._runner(
                ["mdfind", f"com_apple_xcode_dsym_uuids == {key[0]}"], timeout=10.0
            )
        except HostError:
            return None
        bundles = [Path(line) for line in result.stdout.splitlines() if line.endswith(".dSYM")]
        return self._match(self._dwarf_files(bundles), key)

    def _scan(self, key: LookupKey) -> Path | None:
        now = time.monotonic()
        with self._lock:
            if now - self._index.scanned_at < SCAN_INTERVAL_SECONDS:
                return None
            seen = set(self._index.seen)
        bundles = [item for item in self._walk_dsyms() if item not in seen]
        for uuid, arch, path in self._uuids(self._dwarf_files(bundles)):
            with self._lock:
                self._index.entries[(uuid, arch)] = path
        with self._lock:
            self._index.scanned_at = now
            self._index.seen.update(bundles)
            return self._index.entries.get(key)

    def _on_disk(self, key: LookupKey, image_path: str) -> Path | None:
        """A simulator or macOS app runs from a binary on this Mac. Its debug map can give lines."""
        if not image_path or is_system_image(image_path) or not Path(image_path).is_file():
            return None
        return self._match([Path(image_path)], key)

    def _match(self, files: list[Path], key: LookupKey) -> Path | None:
        for uuid, arch, path in self._uuids(files):
            if (uuid, arch) == key:
                return path
        return None

    def _uuids(self, files: list[Path]) -> list[tuple[str, str, Path]]:
        if not files:
            return []
        try:
            result = self._runner(["dwarfdump", "--uuid", *map(str, files)], timeout=30.0)
        except HostError:
            return []
        return parse_uuid_lines(result.stdout)

    @staticmethod
    def _dwarf_files(bundles: list[Path]) -> list[Path]:
        files: list[Path] = []
        for bundle in bundles:
            folder = bundle / "Contents" / "Resources" / "DWARF"
            if folder.is_dir():
                files.extend(sorted(item for item in folder.iterdir() if item.is_file()))
        return files

    def _walk_dsyms(self) -> list[Path]:
        found: list[Path] = []
        for base in self._search_dirs():
            if not base.is_dir():
                continue
            floor = len(base.parts)
            for current, dirs, _ in os.walk(base):
                depth = len(Path(current).parts) - floor
                found.extend(Path(current) / name for name in dirs if name.endswith(".dSYM"))
                dirs[:] = [
                    name
                    for name in dirs
                    if depth < SCAN_DEPTH
                    and not name.endswith(".dSYM")
                    and not name.endswith(_SKIP_DESCENT)
                ]
        return found

    # ----- symbolication -----------------------------------------------------------------

    def symbolicate_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Fills symbol, file, and line on every frame it can, across all threads."""
        images = {
            str(item.get("uuid", "")).upper(): item
            for item in event.get("images") or []
            if isinstance(item, dict)
        }
        arch = str(event.get("arch") or "arm64")
        groups = self._frame_groups(event)
        missing: list[str] = []
        dsyms: dict[str, str] = {}
        answers: dict[AnswerKey, list[dict[str, Any]]] = {}
        for (uuid, base), addresses in self._wanted(groups, images).items():
            image = images.get(uuid, {})
            name = str(image.get("name") or uuid)
            dwarf = self.dwarf_for(uuid, arch, str(image.get("path") or ""))
            if dwarf is None:
                if name not in missing:
                    missing.append(name)
                continue
            dsyms[name] = str(dwarf)
            for address, entries in self._atos(dwarf, arch, base, addresses).items():
                answers[(uuid, base, address)] = entries
        resolved = 0
        for frames in groups:
            rows, count = self._apply(frames, answers)
            frames[:] = rows
            resolved += count
        event["symbolication"] = {
            "status": _status_word(resolved, missing),
            "resolved_frames": resolved,
            "missing_dsym": missing,
            "dsym": dsyms,
        }
        return event

    @staticmethod
    def _frame_groups(event: dict[str, Any]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        if isinstance(event.get("frames"), list):
            groups.append(event["frames"])
        for thread in event.get("threads") or []:
            if isinstance(thread, dict) and isinstance(thread.get("frames"), list):
                groups.append(thread["frames"])
        return groups

    @staticmethod
    def lookup_address(frame: dict[str, Any]) -> str | None:
        """Return addresses point after the call, so look one byte back. Frame 0 is the exact pc."""
        try:
            value = int(str(frame.get("address") or ""), 16)
        except ValueError:
            return None
        return hex(value - 1) if int(frame.get("index") or 0) > 0 else hex(value)

    def _wanted(
        self, groups: list[list[dict[str, Any]]], images: dict[str, dict[str, Any]]
    ) -> dict[LookupKey, list[str]]:
        wanted: dict[LookupKey, set[str]] = {}
        for frames in groups:
            for frame in frames:
                uuid = str(frame.get("image_uuid") or "").upper()
                base = str(frame.get("image_base") or "")
                lookup = self.lookup_address(frame)
                if not uuid or not base or lookup is None or frame.get("file"):
                    continue
                if is_system_image(str(images.get(uuid, {}).get("path") or "")):
                    continue
                wanted.setdefault((uuid, base), set()).add(lookup)
        return {key: sorted(items) for key, items in wanted.items()}

    def _atos(
        self, dwarf: Path, arch: str, base: str, addresses: list[str]
    ) -> dict[str, list[dict[str, Any]]]:
        answers: dict[str, list[dict[str, Any]]] = {}
        for start in range(0, len(addresses), ATOS_CHUNK):
            chunk = addresses[start : start + ATOS_CHUNK]
            argv = [
                "atos", "-arch", arch, "-o", str(dwarf), "-l", base,
                "-i", "--fullPath", "-d", INLINE_DELIMITER, *chunk,
            ]  # fmt: skip
            try:
                result = self._runner(argv, timeout=60.0)
            except HostError:
                return answers
            lines = result.stdout.splitlines()
            if not result.ok or len(lines) != len(chunk):
                continue
            for address, line in zip(chunk, lines, strict=True):
                entries = parse_atos_line(line)
                if entries:
                    answers[address] = entries
        return answers

    def _apply(
        self, frames: list[dict[str, Any]], answers: dict[AnswerKey, list[dict[str, Any]]]
    ) -> tuple[list[dict[str, Any]], int]:
        rows: list[dict[str, Any]] = []
        resolved = 0
        for frame in frames:
            uuid = str(frame.get("image_uuid") or "").upper()
            lookup = self.lookup_address(frame)
            entries = answers.get((uuid, str(frame.get("image_base") or ""), lookup or ""))
            if not entries:
                rows.append(frame)
                continue
            resolved += 1
            for position, entry in enumerate(entries):
                row = {**frame, **entry, "class": class_of(str(entry["symbol"]))}
                row["inlined"] = position < len(entries) - 1
                rows.append(row)
        for position, row in enumerate(rows):
            row["index"] = position
        return rows, resolved


def _status_word(resolved: int, missing: list[str]) -> str:
    if missing and not resolved:
        return "missing_dsym"
    if missing:
        return "partial"
    return "full" if resolved else "not_needed"


def default_search_dirs() -> list[Path]:
    return [derived_data_root(), Path.home() / "Library" / "Developer" / "Xcode" / "Archives"]
