"""Bonjour advertisement: leftover `dns-sd -R` processes from an earlier console are found."""

from __future__ import annotations

import os
import signal

import pytest

from viewlift_ai.services import debug_advertise
from viewlift_ai.services.debug_advertise import reap_stale_registrations, stale_registration_pids

SERVICE = "dns-sd -R Viewlift AI _viewliftdebug._tcp local 8742 ip=192.168.0.40 port=8742"


def _ps(*rows: str):
    return lambda: "\n".join(rows)


def test_finds_only_our_own_advertisements() -> None:
    lister = _ps(
        f" 1659   501 {SERVICE} fp=aa key=bb",
        f" 8646   501 {SERVICE} fp=aa key=bb alt=100.105.93.60",
        f" 9000   0 {SERVICE} fp=aa key=bb",
        " 9100   501 dns-sd -R Other _other._tcp local 80",
        " 9200   501 dns-sd -B _viewliftdebug._tcp local",
        " 9300   501 /usr/bin/python3 viewlift_ai --note dns-sd -R Viewlift AI _viewliftdebug._tcp",
        "garbage line",
    )
    assert stale_registration_pids(lister=lister, uid=501) == [1659, 8646]


def test_keeps_the_running_advertisement() -> None:
    lister = _ps(f" 10   501 {SERVICE}", f" 11   501 {SERVICE}")
    assert stale_registration_pids(keep={11}, lister=lister, uid=501) == [10]


def test_reap_stops_leftovers(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(debug_advertise, "_list_processes", _ps(f" 10   {os.getuid()} {SERVICE}"))

    def fake_kill(pid: int, sig: int) -> None:
        sent.append((pid, sig))
        if pid == 99:
            raise ProcessLookupError

    monkeypatch.setattr(debug_advertise.os, "kill", fake_kill)
    assert reap_stale_registrations() == 1
    assert sent == [(10, signal.SIGTERM)]
