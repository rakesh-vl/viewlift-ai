"""Allowlist store. The proxy decrypts only hosts in this list."""

from __future__ import annotations

import threading
from collections.abc import Callable

from viewlift_ai.models.rules import RulesConfig
from viewlift_ai.services.rule_store import RuleConfigStore, config_from_dict, config_to_dict


class RuleEngine:
    """Persists the HTTPS decryption allowlist and notifies listeners on change."""

    def __init__(self, store: RuleConfigStore) -> None:
        self._store = store
        self._lock = threading.RLock()
        self._config = store.load()
        self._listeners: list[Callable[[RulesConfig], None]] = []

    @property
    def config(self) -> RulesConfig:
        with self._lock:
            return self._clone(self._config)

    def subscribe(self, listener: Callable[[RulesConfig], None]) -> None:
        self._listeners.append(listener)

    def reload(self) -> RulesConfig:
        with self._lock:
            self._config = self._store.load()
            cfg = self._clone(self._config)
        self._notify(cfg)
        return cfg

    def replace(self, config: RulesConfig) -> RulesConfig:
        normalized = self._normalize(config)
        with self._lock:
            self._store.save(normalized)
            self._config = normalized
            cfg = self._clone(self._config)
        self._notify(cfg)
        return cfg

    def set_allowlist(self, patterns: list[str]) -> RulesConfig:
        with self._lock:
            self._config.allowlist = [p.strip() for p in patterns if p and str(p).strip()]
            self._store.save(self._config)
            cfg = self._clone(self._config)
        self._notify(cfg)
        return cfg

    def list_rules(self) -> dict[str, object]:
        with self._lock:
            return {"allowlist": list(self._config.allowlist)}

    def _notify(self, cfg: RulesConfig) -> None:
        for listener in list(self._listeners):
            try:
                listener(cfg)
            except Exception:
                pass

    @staticmethod
    def _normalize(config: RulesConfig) -> RulesConfig:
        return RulesConfig(
            allowlist=[p.strip() for p in config.allowlist if p and str(p).strip()],
        )

    @staticmethod
    def _clone(config: RulesConfig) -> RulesConfig:
        return config_from_dict(config_to_dict(config))
