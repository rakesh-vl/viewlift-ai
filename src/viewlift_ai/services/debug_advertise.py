"""Advertise the SDK receiver on the local network so a phone can find a new Wi-Fi address."""

from __future__ import annotations

import subprocess
import threading
from typing import Protocol


class DebugPublishing(Protocol):
    def publish(self, host: str, port: int, *, fingerprint: str, debug_key: str) -> None: ...

    def stop(self) -> None: ...


class DebugAdvertiser:
    """Bonjour registration through dns-sd. The TXT record carries the bound IPv4 address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._published: tuple[str, int, str, str] | None = None

    def publish(self, host: str, port: int, *, fingerprint: str, debug_key: str) -> None:
        key = (host, port, fingerprint, debug_key)
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            if key == self._published and alive:
                return
            self._stop_locked()
            self._process = subprocess.Popen(
                [
                    "dns-sd",
                    "-R",
                    "Viewlift AI",
                    "_viewliftdebug._tcp",
                    "local",
                    str(port),
                    f"ip={host}",
                    f"port={port}",
                    f"fp={fingerprint}",
                    f"key={debug_key}",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._published = key

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        process = self._process
        self._process = None
        self._published = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
