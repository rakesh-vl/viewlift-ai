"""Present LogRecord as JSON-safe dicts for API/UI."""

from __future__ import annotations

from viewlift_ai.models.log import LogRecord


def log_summary(log: LogRecord) -> dict[str, object]:
    return {
        "id": log.id,
        "level": log.level.value,
        "subsystem": log.subsystem,
        "message": _truncate(log.message, 240),
        "created_at": log.created_at.isoformat(),
        "request_id": log.request_id,
        "source": log.source,
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
        "testflight_private_note": (
            "TestFlight os_log privacy fields often arrive as <private>. "
            "That is expected for App Store distribution builds; use a debug "
            "install or ViewliftNetworkCapture LogService for full messages."
        ),
    }


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"
