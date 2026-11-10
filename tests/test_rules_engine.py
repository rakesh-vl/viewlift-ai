"""Allowlist persistence. Older rules.json files keep their host list and drop mutation keys."""

from __future__ import annotations

import json
import os
from pathlib import Path

from viewlift_ai.models.rules import RulesConfig
from viewlift_ai.services.rule_engine import RuleEngine
from viewlift_ai.services.rule_store import RuleConfigStore


def _engine(tmp_path: Path) -> RuleEngine:
    return RuleEngine(RuleConfigStore(tmp_path / "rules.json"))


def test_defaults_empty(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine.config.allowlist == []
    assert engine.list_rules() == {"allowlist": []}


def test_set_allowlist_strips_blanks(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cfg = engine.set_allowlist(["  api.example.com ", "", "  "])
    assert cfg.allowlist == ["api.example.com"]


def test_load_ignores_retired_rule_keys(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "allowlist": ["cdn.example.com"],
                "map_local": [{"id": "old", "enabled": True}],
                "rewrite": [{"id": "rw", "enabled": True}],
                "throttle": [{"id": "debug-network-profile", "enabled": True}],
                "breakpoints": [{"id": "bp", "enabled": True}],
            }
        ),
        encoding="utf-8",
    )
    engine = RuleEngine(RuleConfigStore(path))
    assert engine.config.allowlist == ["cdn.example.com"]
    engine.set_allowlist(["cdn.example.com"])
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved == {"allowlist": ["cdn.example.com"]}


def test_atomic_persist_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    engine = RuleEngine(RuleConfigStore(path))
    engine.set_allowlist(["a.com"])
    assert path.is_file()
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert RuleConfigStore(path).load() == RulesConfig(allowlist=["a.com"])
