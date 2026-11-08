"""Shared state passed to every host service module."""

from __future__ import annotations

import base64
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.jobs import JobStore
from viewlift_ai.services.host.runner import CommandResult, HostError, ProjectRoots, Runner
from viewlift_ai.services.host.session_defaults import SessionDefaults
from viewlift_ai.services.package_detect import derived_data_root

ReceiverSettings = Callable[[], dict[str, Any]]


def derived_data() -> Path:
    """`VIEWLIFT_DERIVED_DATA`, then Xcode's custom location, then Xcode's default."""
    return derived_data_root()


@dataclass(slots=True)
class HostContext:
    data_dir: Path
    roots: ProjectRoots
    defaults: SessionDefaults
    jobs: JobStore
    runner: Runner
    # The console sets this so tools that start on-device helpers can reach the SDK receiver.
    receiver: ReceiverSettings | None = None

    @property
    def work_dir(self) -> Path:
        path = self.data_dir / "host"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def run(self, argv: list[str], **kwargs: Any) -> CommandResult:
        return self.runner(argv, **kwargs)

    def run_ok(self, argv: list[str], **kwargs: Any) -> CommandResult:
        result = self.runner(argv, **kwargs)
        if not result.ok:
            detail = (result.stderr or result.stdout or "").strip().splitlines()[-3:]
            label = " ".join(argv[:3])
            suffix = " (timed out)" if result.timed_out else ""
            raise HostError(f"{label} failed{suffix}: {' '.join(detail)[:600]}")
        return result

    def run_json(self, argv: list[str], **kwargs: Any) -> Any:
        result = self.run_ok(argv, **kwargs)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise HostError(f"{' '.join(argv[:3])} did not return JSON.") from exc

    def resolve_artifact(self, raw: str) -> Path:
        """Build products may live in DerivedData or our work dir as well as project roots."""
        text = (raw or "").strip()
        if not text:
            raise HostError("appPath is required.")
        path = Path(text).expanduser().resolve()
        if not path.exists():
            raise HostError(f"Path does not exist: {path}")
        for base in (derived_data(), self.work_dir.resolve()):
            if path.is_relative_to(base.resolve()):
                return path
        return self.roots.resolve(str(path))

    def temp_file(self, suffix: str) -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix="vl-", suffix=suffix, dir=self.work_dir, delete=False
        )
        handle.close()
        return Path(handle.name)


def png_payload(path: Path, *, max_dimension: int, runner: Runner) -> dict[str, Any]:
    """Read a PNG, shrinking it with sips first so agents spend fewer image tokens."""
    if max_dimension > 0:
        runner(["sips", "-Z", str(max_dimension), str(path)], timeout=20)
    data = path.read_bytes()
    width, height = png_size(data)
    return {
        "mime": "image/png",
        "base64": base64.b64encode(data).decode("ascii"),
        "width": width,
        "height": height,
        "bytes": len(data),
    }


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return 0, 0
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
