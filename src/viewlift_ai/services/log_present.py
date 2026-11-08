"""Present LogRecord as JSON-safe dicts for API/UI."""

from __future__ import annotations

from viewlift_ai.models.log import LogRecord


def log_summary(log: LogRecord) -> dict[str, object]:
    return {
        "id": log.id,
        "level": log.level.value,
        "subsystem": log.subsystem,
        "message": _preview(log.message),
        "created_at": log.created_at.isoformat(),
        "request_id": log.request_id,
        "source": log.source,
        "client_id": log.client_id,
        "udid": log.udid,
    }


def log_detail(log: LogRecord) -> dict[str, object]:
    return {
        "id": log.id,
        "level": log.level.value,
        "subsystem": log.subsystem,
        "message": log.message,
        "created_at": log.created_at.isoformat(),
        "request_id": log.request_id,
        "source": log.source,
        "client_id": log.client_id,
        "udid": log.udid,
        "console_note": (
            "Captured from app stdout/stderr through the ViewliftNetworkCapture SDK, "
            "the same text Xcode's debug console shows. os_log is not included."
        ),
    }


def _preview(text: str) -> str:
    # Multiline print is one log. Keep its lines in the list.
    limit = 4_000 if "\n" in text or "\r" in text else 240
    return _truncate(text, limit)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
