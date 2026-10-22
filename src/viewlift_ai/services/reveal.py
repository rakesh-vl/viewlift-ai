"""In-memory raw flow reveal for the current UI session only."""

from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class RawFlowPayload:
    request_headers: dict[str, str]
    request_body: bytes
    response_headers: dict[str, str]
    response_body: bytes


class RevealSession:
    """
    Holds unredacted headers/bodies in process memory.

    Unlock requires the current-session token. Never written to store/export/cURL.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token = secrets.token_urlsafe(24)
        self._unlocked = False
        self._raw: dict[str, RawFlowPayload] = {}

    @property
    def unlock_token(self) -> str:
        return self._token

    @property
    def unlocked(self) -> bool:
        with self._lock:
            return self._unlocked

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "unlocked": self._unlocked,
                "unlock_token": self._token,
                "cached_flows": len(self._raw),
                "note": (
                    "Session-memory reveal only. Paste unlock token to view raw headers/bodies "
                    "in the UI. Never used for HAR, cURL, or persistence. Wiped on quit."
                ),
            }

    def remember(
        self,
        flow_id: str,
        *,
        request_headers: dict[str, str],
        request_body: bytes,
        response_headers: dict[str, str],
        response_body: bytes,
    ) -> None:
        with self._lock:
            self._raw[flow_id] = RawFlowPayload(
                request_headers=dict(request_headers),
                request_body=bytes(request_body),
                response_headers=dict(response_headers),
                response_body=bytes(response_body),
            )

    def unlock(self, token: str) -> bool:
        with self._lock:
            if not token or not secrets.compare_digest(token, self._token):
                return False
            self._unlocked = True
            return True

    def lock(self) -> None:
        with self._lock:
            self._unlocked = False

    def get_raw(self, flow_id: str) -> RawFlowPayload | None:
        with self._lock:
            if not self._unlocked:
                return None
            payload = self._raw.get(flow_id)
            if payload is None:
                return None
            return RawFlowPayload(
                request_headers=dict(payload.request_headers),
                request_body=bytes(payload.request_body),
                response_headers=dict(payload.response_headers),
                response_body=bytes(payload.response_body),
            )

    def wipe(self) -> None:
        with self._lock:
            self._raw.clear()
            self._unlocked = False
            self._token = secrets.token_urlsafe(24)
