"""Advertise the SDK receiver on the local network so a phone can find a new Wi-Fi address."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

SERVICE_TYPE = "_viewliftdebug._tcp"
SERVICE_NAME = "Viewlift AI"
_REGISTER_PREFIX = f"dns-sd -R {SERVICE_NAME} {SERVICE_TYPE} "

ProcessLister = Callable[[], str]


def _list_processes() -> str:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pid=,uid=,command="],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout or ""


def stale_registration_pids(
    *, keep: set[int] | None = None, lister: ProcessLister | None = None, uid: int | None = None
) -> list[int]:
    """Our own `dns-sd -R` advertisements left behind by an earlier console.

    A console that was killed, or crashed, leaves its `dns-sd` child running. Each leftover keeps
    advertising an old address, port, fingerprint, or key, and a phone can pick that record.
    """
    own = os.getuid() if uid is None else uid
    spared = keep or set()
    found: list[int] = []
    for line in (lister or _list_processes)().splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) != 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid, owner, command = int(parts[0]), int(parts[1]), parts[2]
        if owner == own and command.startswith(_REGISTER_PREFIX) and pid not in spared:
            found.append(pid)
    return found


def reap_stale_registrations(*, keep: set[int] | None = None) -> int:
    """Stop leftover advertisements. Returns how many were stopped."""
    stopped = 0
    for pid in stale_registration_pids(keep=keep):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
            stopped += 1
    return stopped


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
            reap_stale_registrations()
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
