"""Atomic JSON persistence for non-sensitive rule config (mode 0600)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from viewlift_ai.models.rules import (
    BreakpointRule,
    MapLocalRule,
    MapRemoteRule,
    RewriteAction,
    RewriteActionKind,
    RewriteRule,
    RulesConfig,
    ThrottleRule,
    match_from_dict,
    match_to_dict,
)

RULES_FILENAME = "rules.json"


class RuleConfigStore:
    """Load/save RulesConfig under the app data directory."""

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
    return {
        "allowlist": list(config.allowlist),
        "map_local_root": config.map_local_root,
        "map_local": [_map_local_to_dict(r) for r in config.map_local],
        "map_remote": [_map_remote_to_dict(r) for r in config.map_remote],
        "rewrite": [_rewrite_to_dict(r) for r in config.rewrite],
        "throttle": [_throttle_to_dict(r) for r in config.throttle],
        "breakpoints": [_breakpoint_to_dict(r) for r in config.breakpoints],
    }


def config_from_dict(data: dict[str, Any]) -> RulesConfig:
    allowlist_raw = data.get("allowlist") or []
    allowlist = [str(x).strip() for x in allowlist_raw if str(x).strip()]
    map_local_raw = data.get("map_local") or []
    map_remote_raw = data.get("map_remote") or []
    rewrite_raw = data.get("rewrite") or []
    throttle_raw = data.get("throttle") or []
    breakpoints_raw = data.get("breakpoints") or []
    return RulesConfig(
        allowlist=allowlist,
        map_local_root=str(data.get("map_local_root") or ""),
        map_local=[_map_local_from_dict(x) for x in map_local_raw if isinstance(x, dict)],
        map_remote=[_map_remote_from_dict(x) for x in map_remote_raw if isinstance(x, dict)],
        rewrite=[_rewrite_from_dict(x) for x in rewrite_raw if isinstance(x, dict)],
        throttle=[_throttle_from_dict(x) for x in throttle_raw if isinstance(x, dict)],
        breakpoints=[
            _breakpoint_from_dict(x) for x in breakpoints_raw if isinstance(x, dict)
        ],
    )


def _map_local_to_dict(rule: MapLocalRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "relative_path": rule.relative_path,
    }


def _map_local_from_dict(data: dict[str, Any]) -> MapLocalRule:
    return MapLocalRule(
        id=str(data.get("id") or ""),
        enabled=bool(data.get("enabled", False)),
        order=int(data.get("order") or 0),
        match=match_from_dict(data.get("match") if isinstance(data.get("match"), dict) else None),
        relative_path=str(data.get("relative_path") or ""),
    )


def _map_remote_to_dict(rule: MapRemoteRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "target_url": rule.target_url,
        "preserve_path": bool(rule.preserve_path),
        "preserve_query": bool(rule.preserve_query),
    }


def _map_remote_from_dict(data: dict[str, Any]) -> MapRemoteRule:
    return MapRemoteRule(
        id=str(data.get("id") or ""),
        enabled=bool(data.get("enabled", False)),
        order=int(data.get("order") or 0),
        match=match_from_dict(data.get("match") if isinstance(data.get("match"), dict) else None),
        target_url=str(data.get("target_url") or ""),
        preserve_path=bool(data.get("preserve_path", True)),
        preserve_query=bool(data.get("preserve_query", True)),
    )


def _rewrite_to_dict(rule: RewriteRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "actions": [_action_to_dict(a) for a in rule.actions],
    }


def _rewrite_from_dict(data: dict[str, Any]) -> RewriteRule:
    actions_raw = data.get("actions") or []
    actions = [_action_from_dict(a) for a in actions_raw if isinstance(a, dict)]
    return RewriteRule(
        id=str(data.get("id") or ""),
        enabled=bool(data.get("enabled", False)),
        order=int(data.get("order") or 0),
        match=match_from_dict(data.get("match") if isinstance(data.get("match"), dict) else None),
        actions=actions,
    )


def _action_to_dict(action: RewriteAction) -> dict[str, Any]:
    return {
        "kind": str(action.kind),
        "header_name": action.header_name,
        "header_value": action.header_value,
        "status": action.status,
        "find": action.find,
        "replace": action.replace,
    }


def _action_from_dict(data: dict[str, Any]) -> RewriteAction:
    kind_raw = str(data.get("kind") or RewriteActionKind.SET_HEADER)
    try:
        kind = RewriteActionKind(kind_raw)
    except ValueError:
        kind = RewriteActionKind.SET_HEADER
    status = data.get("status")
    return RewriteAction(
        kind=kind,
        header_name=None if data.get("header_name") is None else str(data.get("header_name")),
        header_value=None if data.get("header_value") is None else str(data.get("header_value")),
        status=int(status) if status is not None else None,
        find=None if data.get("find") is None else str(data.get("find")),
        replace=None if data.get("replace") is None else str(data.get("replace")),
    )


def _throttle_to_dict(rule: ThrottleRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "host": rule.host,
        "latency_ms": int(rule.latency_ms),
        "upload_kbps": rule.upload_kbps,
        "download_kbps": rule.download_kbps,
        "offline": bool(rule.offline),
    }


def _throttle_from_dict(data: dict[str, Any]) -> ThrottleRule:
    up = data.get("upload_kbps")
    down = data.get("download_kbps")
    return ThrottleRule(
        id=str(data.get("id") or ""),
        enabled=bool(data.get("enabled", False)),
        order=int(data.get("order") or 0),
        host=str(data.get("host") or "*"),
        latency_ms=int(data.get("latency_ms") or 0),
        upload_kbps=int(up) if up is not None else None,
        download_kbps=int(down) if down is not None else None,
        offline=bool(data.get("offline", False)),
    )


def _breakpoint_to_dict(rule: BreakpointRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "enabled": bool(rule.enabled),
        "order": int(rule.order),
        "match": match_to_dict(rule.match),
        "request_enabled": bool(rule.request_enabled),
        "response_enabled": bool(rule.response_enabled),
    }


def _breakpoint_from_dict(data: dict[str, Any]) -> BreakpointRule:
    return BreakpointRule(
        id=str(data.get("id") or ""),
        enabled=bool(data.get("enabled", False)),
        order=int(data.get("order") or 0),
        match=match_from_dict(data.get("match") if isinstance(data.get("match"), dict) else None),
        request_enabled=bool(data.get("request_enabled", False)),
        response_enabled=bool(data.get("response_enabled", False)),
    )
