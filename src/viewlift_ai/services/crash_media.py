"""Crash replay videos and posters on disk, assembled from the chunks the SDK uploads.

Files are named after the crash event id, so they go away when that crash is deleted from the
store (see `prune`). Frames can show whatever was on screen, so they never leave the data folder.
"""

from __future__ import annotations

import re
import shutil
import threading
from pathlib import Path

MAX_CHUNKS = 64
MAX_VIDEO_BYTES = 12 * 1024 * 1024
_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_key(key: str) -> str:
    cleaned = _SAFE.sub("_", key)[:200]
    if not cleaned or cleaned.startswith("."):
        raise ValueError("invalid crash id")
    return cleaned


_SUFFIXES = (".mp4.tmp", ".jpg.tmp", ".mp4", ".jpg", ".parts")


def _stem(name: str) -> str:
    for suffix in _SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


class CrashMediaStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def video_path(self, key: str) -> Path:
        return self.root / f"{_safe_key(key)}.mp4"

    def poster_path(self, key: str) -> Path:
        return self.root / f"{_safe_key(key)}.jpg"

    def has_video(self, key: str) -> bool:
        return self.video_path(key).is_file()

    def add_chunk(self, key: str, index: int, total: int, data: bytes) -> bool:
        """Stores one piece. Returns True once every piece is in and the video has been written."""
        if not 0 < total <= MAX_CHUNKS or not 0 <= index < total:
            raise ValueError("chunk index out of range")
        parts = self.root / f"{_safe_key(key)}.parts"
        with self._lock:
            parts.mkdir(parents=True, exist_ok=True)
            (parts / f"{index:03d}").write_bytes(data)
            names = sorted(item.name for item in parts.iterdir() if item.name.isdigit())
            if len(names) < total:
                return False
            size = sum((parts / name).stat().st_size for name in names[:total])
            if size > MAX_VIDEO_BYTES:
                shutil.rmtree(parts, ignore_errors=True)
                raise ValueError("replay video is too large")
            target = self.video_path(key)
            temp = target.with_suffix(".mp4.tmp")
            with temp.open("wb") as out:
                for name in names[:total]:
                    out.write((parts / name).read_bytes())
            temp.replace(target)
            shutil.rmtree(parts, ignore_errors=True)
            return True

    def save_poster(self, key: str, data: bytes) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.poster_path(key)
        temp = path.with_suffix(".jpg.tmp")
        temp.write_bytes(data)
        temp.replace(path)

    def prune(self, keep: set[str]) -> int:
        """Deletes media whose crash is no longer stored."""
        if not self.root.is_dir():
            return 0
        allowed = {_safe_key(item) for item in keep if item}
        removed = 0
        with self._lock:
            for item in self.root.iterdir():
                if _stem(item.name) in allowed:
                    continue
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)
                removed += 1
        return removed
