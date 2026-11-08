"""Registry of host tools. The console route and the MCP layer both call `HostToolkit.call`."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.context import HostContext
from viewlift_ai.services.host.jobs import JobStore
from viewlift_ai.services.host.runner import HostError, ProjectRoots, Runner, run
from viewlift_ai.services.host.session_defaults import SessionDefaults

Handler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    handler: Handler
    mutating: bool = False


class HostToolkit:
    def __init__(self, data_dir: Path, *, runner: Runner = run) -> None:
        host_dir = data_dir / "host"
        self.ctx = HostContext(
            data_dir=data_dir,
            roots=ProjectRoots(host_dir / "project_roots.json"),
            defaults=SessionDefaults(host_dir / "session_defaults.json"),
            jobs=JobStore(host_dir / "jobs"),
            runner=runner,
        )
        self._tools: dict[str, ToolSpec] = {}
        from viewlift_ai.services.host import (
            apple_docs,
            coverage,
            device_tunnel,
            device_video,
            devicectl,
            doctor,
            lldb_session,
            macos,
            previews,
            scaffold,
            simctl,
            swiftpm,
            target,
            ui_driver,
            ui_runner,
            xcodebuild,
        )

        for module in (
            doctor,
            xcodebuild,
            simctl,
            devicectl,
            device_tunnel,
            device_video,
            macos,
            swiftpm,
            coverage,
            scaffold,
            lldb_session,
            apple_docs,
            previews,
            target,
            ui_driver,
            ui_runner,
        ):
            module.register(self)

    def add(self, name: str, handler: Handler, *, mutating: bool = False) -> None:
        if name in self._tools:
            raise ValueError(f"duplicate host tool {name}")
        self._tools[name] = ToolSpec(name, handler, mutating)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def is_mutating(self, name: str) -> bool:
        spec = self._tools.get(name)
        return bool(spec and spec.mutating)

    def call(
        self, name: str, args: dict[str, Any] | None, *, confirm: bool = False
    ) -> dict[str, Any]:
        spec = self._tools.get((name or "").strip())
        if spec is None:
            return {"ok": False, "detail": f"Unknown host tool {name}."}
        if spec.mutating and confirm is not True:
            return {"ok": False, "detail": "confirm must be true"}
        clean = {key: value for key, value in (args or {}).items() if value is not None}
        try:
            result = spec.handler(clean)
        except HostError as exc:
            return {"ok": False, "detail": str(exc)}
        if not isinstance(result, dict):
            return {"ok": True, "result": result}
        result.setdefault("ok", True)
        return result
