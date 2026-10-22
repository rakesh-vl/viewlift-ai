"""Simulator log collector: argv stream, parse, start/stop."""

from __future__ import annotations

from types import SimpleNamespace

from viewlift_ai.models.log import LogLevel
from viewlift_ai.services.simulator_logs import (
    SimulatorLogCollector,
    parse_simctl_log_line,
)


def test_parse_simctl_compact_line() -> None:
    line = "2026-10-21 12:00:00.123 MyApp[1234] com.example.sub: hello requestId=abc123xyz"
    level, subsystem, message = parse_simctl_log_line(line)
    assert subsystem == "com.example.sub"
    assert "hello" in message
    assert level == LogLevel.INFO


def test_list_booted_uses_argv(monkeypatch) -> None:
    calls: list[list[str]] = []

    def runner(argv, **kwargs):
        calls.append(list(argv))
        payload = """
        {"devices":{"com.apple.CoreSimulator.SimRuntime.iOS-17-0":[
          {"udid":"AAAA-BBBB","name":"iPhone 15","state":"Booted"}
        ]}}
        """
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    collector = SimulatorLogCollector(runner=runner)
    rows = collector.list_booted()
    assert calls[0][:4] == ["xcrun", "simctl", "list", "devices"]
    assert rows[0]["udid"] == "AAAA-BBBB"
    assert rows[0]["platform"] == "iOS"


def test_start_stop_popen_argv() -> None:
    started: list[list[str]] = []

    class FakeProc:
        def __init__(self) -> None:
            self.stdout = iter(["2026-10-21 12:00:00.123 App[1] sub: hi\n"])
            self.returncode = None

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.returncode = 0

        def wait(self, timeout=None) -> int:
            self.returncode = 0
            return 0

        def kill(self) -> None:
            self.returncode = 0

    def popen(argv, **kwargs):
        started.append(list(argv))
        return FakeProc()

    lines: list[tuple] = []
    collector = SimulatorLogCollector(popen=popen)
    result = collector.start("UDID-1", on_line=lambda *a: lines.append(a))
    assert result["ok"] is True
    assert started[0] == [
        "xcrun",
        "simctl",
        "spawn",
        "UDID-1",
        "log",
        "stream",
        "--style",
        "compact",
        "--level",
        "debug",
    ]
    # Allow pump thread to consume
    import time

    time.sleep(0.1)
    stop = collector.stop()
    assert stop["ok"] is True


def test_log_tail_rejects_unsafe_process_name() -> None:
    from viewlift_ai.services.log_tail import LogTailService

    tail = LogTailService(capture=None)  # type: ignore[arg-type]
    result = tail.start("simulator", udid="UDID-1", process='x" OR 1==1')
    assert result["ok"] is False
    assert "Process name" in str(result["detail"])
