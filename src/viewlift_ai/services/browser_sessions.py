"""Track open dashboard tabs so capture data can be wiped when the last one closes."""

from __future__ import annotations

import threading


class BrowserSessions:
    """Latest page generation per browser tab.

    A reload or in-app navigation sends leave for the old page, then ping for a
    higher page number on the same tab id. The stale leave must not count as
    the tab closing.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._page: dict[str, int] = {}

    def ping(self, session_id: str, page: int) -> None:
        if not session_id:
            return
        with self._lock:
            if page >= self._page.get(session_id, 0):
                self._page[session_id] = page

    def leave(self, session_id: str, page: int) -> bool:
        """Drop this page. True when no tab is still open."""
        if not session_id:
            return False
        with self._lock:
            current = self._page.get(session_id)
            if current is not None and page >= current:
                self._page.pop(session_id, None)
            return not self._page

    def alive(self) -> bool:
        with self._lock:
            return bool(self._page)
