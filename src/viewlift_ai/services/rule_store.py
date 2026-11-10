"""Atomic JSON persistence for the proxy allowlist (mode 0600)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from viewlift_ai.models.rules import RulesConfig

RULES_FILENAME = "rules.json"


class RuleConfigStore:
    """Load/save the allowlist under the app data directory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> RulesConfig:
        with self._lock:
            if not self._path.is_file():
                return RulesConfig()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return RulesConfig()
            if not isinstance(raw, dict):
                return RulesConfig()
            return config_from_dict(raw)

    def save(self, config: RulesConfig) -> None:
        payload = config_to_dict(config)
        text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        with self._lock:
            self._atomic_write(text)

    def _atomic_write(self, text: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=".rules-",
            suffix=".tmp",
            dir=str(self._path.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self._path)
            os.chmod(self._path, 0o600)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise


def config_to_dict(config: RulesConfig) -> dict[str, Any]:
    return {"allowlist": list(config.allowlist)}


def config_from_dict(data: dict[str, Any]) -> RulesConfig:
    """Read the allowlist. Older files may still hold retired rule keys."""
    allowlist_raw = data.get("allowlist") or []
    if not isinstance(allowlist_raw, list):
        allowlist_raw = []
    allowlist = [str(item).strip() for item in allowlist_raw if str(item).strip()]
    return RulesConfig(allowlist=allowlist)
