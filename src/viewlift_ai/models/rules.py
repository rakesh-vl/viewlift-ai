"""Allowlist configuration (no framework imports)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RulesConfig:
    """Host patterns the proxy may decrypt. Empty means deny all."""

    allowlist: list[str] = field(default_factory=list)
