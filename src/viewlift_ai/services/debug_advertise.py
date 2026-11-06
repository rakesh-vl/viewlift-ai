"""Advertise the SDK receiver on the local network so a phone can find a new Wi-Fi address."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Sequence
from typing import Protocol

SERVICE_TYPE = "_viewliftdebug._tcp"
SERVICE_NAME = "Viewlift AI"


class DebugPublishing(Protocol):
    def publish(
        self,
        host: str,
        port: int,
        *,
        fingerprint: str,
        debug_key: str,
        alt: Sequence[str] = (),
    ) -> None: ...

    def stop(self) -> None: ...


class DebugAdvertiser:
    """Bonjour registration through dns-sd. The TXT record carries the bound IPv4 address."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._published: tuple[str, int, str, str, tuple[str, ...]] | None = None

    def publish(
        self,
        host: str,
        port: int,
        *,
        fingerprint: str,
        debug_key: str,
        alt: Sequence[str] = (),
    ) -> None:
        """Advertise the receiver. `alt` lists other addresses (VPN) the phone may fall back to."""
        alternates = tuple(alt)
        key = (host, port, fingerprint, debug_key, alternates)
        txt = [f"ip={host}", f"port={port}", f"fp={fingerprint}", f"key={debug_key}"]
        if alternates:
            txt.append(f"alt={','.join(alternates)}")
        with self._lock:
            alive = self._process is not None and self._process.poll() is None
            if key == self._published and alive:
                return
            self._stop_locked()
            self._process = subprocess.Popen(
                [
                    "dns-sd",
                    "-R",
                    SERVICE_NAME,
                    SERVICE_TYPE,
                    "local",
                    str(port),
                    *txt,
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
