"""argv-only subprocess runner, project-root allowlist, and argument validation."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOTS_ENV = "VIEWLIFT_AI_PROJECT_ROOTS"
DEFAULT_TIMEOUT = 60.0
MAX_CAPTURE_CHARS = 400_000

_NAME_RE = re.compile(r"^[\w .+\-()&'@,]{1,200}$")
_UDID_RE = re.compile(r"^[A-Fa-f0-9-]{8,64}$")
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9.\-_]{1,255}$")
# Build settings that inject compiler, linker, or loader input.
_BLOCKED_SETTING_RE = re.compile(
    r"(?i)^(OTHER_[A-Z_]*FLAGS|[A-Z_]*_SCRIPT[A-Z_]*|LD_[A-Z_]+|DYLD_[A-Z_]+|SWIFT_EXEC|CC|CXX|"
    r"LD|LIBTOOL|CODE_SIGN_[A-Z_]*|PROVISIONING_PROFILE[A-Z_]*|DEVELOPMENT_TEAM)="
)
_SETTING_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,80}=[^\n\r]{0,500}$")
_FLAG_ALONE = frozenset(
    {
        "-quiet",
        "-showBuildTimingSummary",
        "-skipMacroValidation",
        "-skipPackagePluginValidation",
        "-allowProvisioningUpdates",
        "-parallel-testing-enabled",
        "-only-testing",
        "-skip-testing",
    }
)
_FLAG_WITH_VALUE = frozenset(
    {
        "-sdk",
        "-arch",
        "-jobs",
        "-testPlan",
        "-parallel-testing-worker-count",
        "-retry-tests-on-failure",
        "-test-iterations",
        "-enableCodeCoverage",
        "-destination-timeout",
    }
)


class HostError(Exception):
    """Refused or failed host action. The message is safe to show an agent."""


@dataclass(slots=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def as_dict(self, *, tail: int = 4000) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "argv": self.argv,
            "returncode": self.returncode,
            "timed_out": self.timed_out,
            "stdout": self.stdout[-tail:],
            "stderr": self.stderr[-tail:],
        }


Runner = Callable[..., CommandResult]


def run(
    argv: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    cwd: str | Path | None = None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    """Run one argv list with a timeout. Missing executables become a HostError."""
    args = [str(item) for item in argv]
    merged = {**os.environ, **env} if env else None
    try:
        completed = subprocess.run(
            args,
            cwd=str(cwd) if cwd else None,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=merged,
        )
    except FileNotFoundError as exc:
        raise HostError(f"{args[0]} not found. Install Xcode command-line tools.") from exc
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return CommandResult(args, -1, out[-MAX_CAPTURE_CHARS:], err[-MAX_CAPTURE_CHARS:], True)
    return CommandResult(
        args,
        completed.returncode,
        (completed.stdout or "")[-MAX_CAPTURE_CHARS:],
        (completed.stderr or "")[-MAX_CAPTURE_CHARS:],
    )


def which(name: str) -> str | None:
    return shutil.which(name)


class ProjectRoots:
    """Folders an agent may build, scan, or scaffold in. Env roots plus roots saved on Setup."""

    def __init__(self, store_path: Path | None = None) -> None:
        self._store_path = store_path
        self._lock = threading.Lock()
        self._saved: list[str] = self._load()

    def _load(self) -> list[str]:
        if self._store_path is None or not self._store_path.is_file():
            return []
        try:
            data = json.loads(self._store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        items = data.get("roots") if isinstance(data, dict) else None
        return [str(item) for item in items if isinstance(item, str)] if items else []

    @staticmethod
    def _env_roots() -> list[str]:
        raw = os.environ.get(ROOTS_ENV, "")
        return [part.strip() for part in raw.split(":") if part.strip()]

    def roots(self) -> list[Path]:
        with self._lock:
            saved = list(self._saved)
        out: list[Path] = []
        for raw in [*self._env_roots(), *saved]:
            path = Path(raw).expanduser()
            if path.is_dir():
                resolved = path.resolve()
                if resolved not in out:
                    out.append(resolved)
        return out

    def status(self) -> dict[str, Any]:
        with self._lock:
            saved = list(self._saved)
        return {
            "env": self._env_roots(),
            "saved": saved,
            "active": [str(item) for item in self.roots()],
            "env_name": ROOTS_ENV,
        }

    def set_saved(self, roots: Iterable[str]) -> dict[str, Any]:
        cleaned: list[str] = []
        for raw in roots:
            text = str(raw).strip()
            if not text:
                continue
            path = Path(text).expanduser()
            if not path.is_absolute():
                raise HostError(f"Project root must be an absolute path: {text}")
            if not path.is_dir():
                raise HostError(f"Project root is not a folder: {text}")
            resolved = path.resolve()
            if resolved == Path("/") or resolved == Path.home().resolve():
                raise HostError("Pick a project folder, not / or your home folder.")
            if str(resolved) not in cleaned:
                cleaned.append(str(resolved))
        with self._lock:
            self._saved = cleaned
            if self._store_path is not None:
                self._store_path.parent.mkdir(parents=True, exist_ok=True)
                self._store_path.write_text(json.dumps({"roots": cleaned}), encoding="utf-8")
        return self.status()

    def resolve(self, raw: str, *, must_exist: bool = True) -> Path:
        """Resolve a user path and refuse anything outside every root, including via symlinks."""
        text = (raw or "").strip()
        if not text:
            raise HostError("A path is required.")
        roots = self.roots()
        if not roots:
            raise HostError(
                f"No project roots are set. Add one on the Setup page or set {ROOTS_ENV}."
            )
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = roots[0] / path
        resolved = path.resolve()
        if must_exist and not resolved.exists():
            raise HostError(f"Path does not exist: {resolved}")
        for root in roots:
            if resolved == root or resolved.is_relative_to(root):
                return resolved
        raise HostError("Path is outside the configured project roots.")


def check_name(value: str, label: str) -> str:
    text = (value or "").strip()
    if not _NAME_RE.match(text):
        raise HostError(f"{label} has unsupported characters or length.")
    return text


def check_udid(value: str) -> str:
    text = (value or "").strip()
    if not _UDID_RE.match(text):
        raise HostError("Device or simulator id must be a UDID.")
    return text


def check_bundle_id(value: str) -> str:
    text = (value or "").strip()
    if not _BUNDLE_RE.match(text):
        raise HostError("bundleId has unsupported characters.")
    return text


def check_extra_args(values: Sequence[str] | None) -> list[str]:
    """Allow a short list of xcodebuild flags and plain KEY=VALUE build settings."""
    out: list[str] = []
    items = [str(item) for item in values or []]
    index = 0
    while index < len(items):
        item = items[index]
        if "\n" in item or "\r" in item or "\x00" in item:
            raise HostError("extraArgs cannot contain control characters.")
        if _BLOCKED_SETTING_RE.match(item):
            raise HostError(f"Build setting is not allowed from an agent: {item.split('=')[0]}")
        if _SETTING_RE.match(item):
            out.append(item)
        elif item.split(":", 1)[0] in {"-only-testing", "-skip-testing"}:
            out.append(item)
        elif item in _FLAG_WITH_VALUE and index + 1 < len(items):
            out.extend([item, items[index + 1]])
            index += 1
        elif item in _FLAG_ALONE:
            out.append(item)
        else:
            raise HostError(f"extraArgs entry is not allowed: {item}")
        index += 1
    return out


def check_launch_args(values: Sequence[str] | None) -> list[str]:
    out = []
    for item in values or []:
        text = str(item)
        if "\x00" in text or len(text) > 2000:
            raise HostError("launchArgs entry is too long or has a NUL byte.")
        out.append(text)
    return out[:64]
