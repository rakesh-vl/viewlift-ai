"""Named default profiles (project, scheme, simulator, device) so agents skip repeated args."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from viewlift_ai.services.host.runner import HostError

DEFAULT_KEYS = (
    "projectPath",
    "workspacePath",
    "packagePath",
    "scheme",
    "configuration",
    "platform",
    "simulatorId",
    "simulatorName",
    "deviceId",
    "bundleId",
    "derivedDataPath",
    "useLatestOS",
    "developmentTeam",
)
_PROFILE_RE_MAX = 60


class SessionDefaults:
    def __init__(self, store_path: Path | None = None) -> None:
        self._path = store_path
        self._lock = threading.Lock()
        self._profiles: dict[str, dict[str, Any]] = {"default": {}}
        self._active = "default"
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.is_file():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        profiles = data.get("profiles") if isinstance(data, dict) else None
        if isinstance(profiles, dict):
            self._profiles = {
                str(name): {k: v for k, v in values.items() if k in DEFAULT_KEYS}
                for name, values in profiles.items()
                if isinstance(values, dict)
            } or {"default": {}}
        active = data.get("active") if isinstance(data, dict) else None
        if isinstance(active, str) and active in self._profiles:
            self._active = active

    def _save(self) -> None:
        if self._path is None:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"active": self._active, "profiles": self._profiles}
        self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def current(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._profiles.get(self._active, {}))

    def show(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_profile": self._active,
                "defaults": dict(self._profiles.get(self._active, {})),
                "profiles": sorted(self._profiles),
                "keys": list(DEFAULT_KEYS),
            }

    def set(self, values: dict[str, Any], *, profile: str = "") -> dict[str, Any]:
        unknown = sorted(set(values) - set(DEFAULT_KEYS))
        if unknown:
            raise HostError(f"Unknown default keys: {', '.join(unknown)}")
        if values.get("projectPath") and values.get("workspacePath"):
            raise HostError("projectPath and workspacePath are mutually exclusive.")
        with self._lock:
            name = self._profile_name(profile) if profile else self._active
            target = self._profiles.setdefault(name, {})
            if values.get("projectPath"):
                target.pop("workspacePath", None)
            if values.get("workspacePath"):
                target.pop("projectPath", None)
            for key, value in values.items():
                if value in (None, ""):
                    target.pop(key, None)
                else:
                    target[key] = value
            self._save()
        return self.show()

    def clear(self, keys: list[str] | None = None, *, all_profiles: bool = False) -> dict[str, Any]:
        with self._lock:
            if all_profiles:
                self._profiles = {"default": {}}
                self._active = "default"
            elif keys:
                for key in keys:
                    self._profiles.get(self._active, {}).pop(key, None)
            else:
                self._profiles[self._active] = {}
            self._save()
        return self.show()

    def use_profile(self, profile: str, *, create: bool = False) -> dict[str, Any]:
        name = self._profile_name(profile)
        with self._lock:
            if name not in self._profiles:
                if not create:
                    raise HostError(f"Unknown profile {name}. Pass create=true to add it.")
                self._profiles[name] = {}
            self._active = name
            self._save()
        return self.show()

    @staticmethod
    def _profile_name(raw: str) -> str:
        name = (raw or "").strip()
        if (
            not name
            or len(name) > _PROFILE_RE_MAX
            or not name.replace("-", "").replace("_", "").isalnum()
        ):
            raise HostError("Profile names use letters, digits, - and _.")
        return name

    def merged(self, args: dict[str, Any]) -> dict[str, Any]:
        """Explicit args win; empty args fall back to the active profile."""
        out = self.current()
        for key, value in args.items():
            if value not in (None, "", [], {}):
                out[key] = value
        if args.get("projectPath"):
            out.pop("workspacePath", None)
        if args.get("workspacePath"):
            out.pop("projectPath", None)
        return out
