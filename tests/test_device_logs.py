"""Device syslog adapter: parse + clear diagnostics."""

from __future__ import annotations

from viewlift_ai.models.log import LogLevel
from viewlift_ai.services.device_logs import (
    DeviceLogCollector,
    parse_device_syslog_line,
)


def test_parse_device_syslog_line() -> None:
    line = "Oct 21 12:00:00 iPhone MyApp[99] <Error>: boom"
    level, subsystem, message = parse_device_syslog_line(line)
    assert level == LogLevel.ERROR
    assert subsystem == "MyApp"
    assert message == "boom"


def test_list_devices_empty_diagnostics() -> None:
    collector = DeviceLogCollector(list_fn=lambda: [])
    rows = collector.list_devices()
    assert rows[0]["ok"] is False
    assert "No paired" in str(rows[0]["detail"])


def test_start_stop_injected_stream() -> None:
    def list_fn():
        return [{"udid": "DEV1", "name": "Phone", "connection": "usb"}]

    def stream_fn(udid, on_line, stop, process):
        on_line(LogLevel.INFO, "app", "hello")
        stop.wait(0.2)

    collector = DeviceLogCollector(list_fn=list_fn, stream_fn=stream_fn)
    lines: list[tuple] = []
    result = collector.start("DEV1", on_line=lambda *a: lines.append(a))
    assert result["ok"] is True
    stop = collector.stop()
    assert stop["ok"] is True
    assert lines
    assert lines[0][2] == "hello"


def test_start_reports_list_failure_instead_of_pair_list() -> None:
    def list_fn():
        raise RuntimeError("asyncio.run() cannot be called from a running event loop")

    collector = DeviceLogCollector(list_fn=list_fn, stream_fn=lambda *args: None)
    result = collector.start("00008120-001E558E21D1A01E", on_line=lambda *args: None)
    assert result["ok"] is False
    assert "asyncio.run()" in result["detail"]
    assert "paired list" not in result["detail"]


def test_start_requires_udid_when_multiple() -> None:
    def list_fn():
        return [
            {"udid": "A", "name": "a", "connection": "usb"},
            {"udid": "B", "name": "b", "connection": "wifi"},
        ]

    collector = DeviceLogCollector(list_fn=list_fn, stream_fn=lambda *a: None)
    result = collector.start(None, on_line=lambda *a: None)
    assert result["ok"] is False
    assert "Multiple" in result["detail"]


def test_start_passes_process_filter_to_stream() -> None:
    seen: list[str | None] = []

    def stream_fn(udid, on_line, stop, process):
        seen.append(process)
        stop.wait(0.2)

    collector = DeviceLogCollector(
        list_fn=lambda: [{"udid": "DEV1", "name": "Phone", "connection": "usb"}],
        stream_fn=stream_fn,
    )
    result = collector.start("DEV1", on_line=lambda *a: None, process="NESN")
    collector.stop()
    assert result["process"] == "NESN"
    assert seen == ["NESN"]


def test_list_devices_uses_cache_while_streaming() -> None:
    calls = {"n": 0}

    def list_fn():
        calls["n"] += 1
        return [{"udid": "DEV1", "name": "Phone", "connection": "usb"}]

    def stream_fn(udid, on_line, stop, process):
        stop.wait(1)

    collector = DeviceLogCollector(list_fn=list_fn, stream_fn=stream_fn)
    collector.start("DEV1", on_line=lambda *a: None)
    calls["n"] = 0
    rows = collector.list_devices()
    collector.stop()
    assert calls["n"] == 0
    assert rows[0]["udid"] == "DEV1"


def test_selector_matches_bundle_id_and_process() -> None:
    from viewlift_ai.services.device_logs import LogSelector

    selector = LogSelector()
    selector.update(process="NESN", subsystem="com.nesn.nesngo")
    assert selector.allows(process="NESN", subsystem="com.nesn.nesngo")
    assert not selector.allows(process="locationd", subsystem="com.apple.locationd")
    selector.update(process="", subsystem="com.nesn.nesngo")
    assert selector.allows(process="NESN", subsystem="com.nesn.nesngo")


def test_live_filter_drops_other_subsystems_without_restarting() -> None:
    from viewlift_ai.services.log_tail import LogTailService

    class Capture:
        def __init__(self) -> None:
            self.saved: list[str] = []

        def persist(self, **kwargs: object) -> object:
            self.saved.append(str(kwargs["subsystem"]))
            return kwargs

    capture = Capture()
    tail = LogTailService(capture=capture)  # type: ignore[arg-type]
    tail.set_filter(subsystem="com.nesn.nesngo")
    tail._on_line("device", LogLevel.DEBUG, "com.apple.locationd", "noise", process="locationd")
    tail._on_line("device", LogLevel.DEBUG, "com.nesn.nesngo", "hit", process="NESN")
    assert capture.saved == ["com.nesn.nesngo"]
