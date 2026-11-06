"""Host-app integration snippet for the Setup page, built from live receiver state."""

from __future__ import annotations

from viewlift_ai.services.debug_advertise import SERVICE_TYPE

START_CALL = "CaptureService.shared.start()"
ENV_HOST = "VIEWLIFT_CAPTURE_HOST"
ENV_PORT = "VIEWLIFT_CAPTURE_PORT"
ENV_FINGERPRINT = "VIEWLIFT_CAPTURE_FINGERPRINT"
ENV_DEBUG_KEY = "VIEWLIFT_CAPTURE_DEBUG_KEY"


def sdk_integration(
    *,
    enabled: bool,
    host: str | None,
    port: int | None,
    fingerprint: str | None,
) -> dict[str, object]:
    """Swift start call, Info.plist service, and optional scheme overrides for the host app.

    The debug key is never included; discovery supplies it to the app.
    """
    overrides: dict[str, str] = {}
    if enabled and host and port and fingerprint:
        overrides = {ENV_HOST: host, ENV_PORT: str(port), ENV_FINGERPRINT: fingerprint}
    lines = ["#if DEBUG", START_CALL, "#endif"]
    if overrides:
        lines += ["", "Optional scheme environment (skips Bonjour):"]
        lines += [f"{key}={value}" for key, value in overrides.items()]
    return {
        "start_call": START_CALL,
        "bonjour_service": SERVICE_TYPE,
        "env_overrides": overrides,
        "env_keys": [ENV_HOST, ENV_PORT, ENV_FINGERPRINT, ENV_DEBUG_KEY],
        "snippet": "\n".join(lines),
    }
