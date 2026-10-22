"""Host allowlist for TLS decrypt / capture. Default deny."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterable, Sequence


class HostAllowlist:
    """Exact and glob (`fnmatch`) host matching. Empty patterns → deny all."""

    def __init__(self, patterns: Iterable[str] | None = None) -> None:
        self._patterns: list[str] = []
        if patterns:
            self.set_patterns(patterns)

    @property
    def patterns(self) -> Sequence[str]:
        return tuple(self._patterns)

    def set_patterns(self, patterns: Iterable[str]) -> None:
        cleaned: list[str] = []
        for raw in patterns:
            item = raw.strip().lower().rstrip(".")
            if item:
                cleaned.append(item)
        self._patterns = cleaned

    def allows(self, host: str | None) -> bool:
        """Return True only when host matches an allowlist entry."""
        if not host or not self._patterns:
            return False
        normalized = host.strip().lower().rstrip(".")
        if not normalized:
            return False
        for pattern in self._patterns:
            if self._matches(pattern, normalized):
                return True
        return False

    @staticmethod
    def _matches(pattern: str, host: str) -> bool:
        if pattern == host:
            return True
        return fnmatch.fnmatchcase(host, pattern)
