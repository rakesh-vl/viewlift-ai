"""Live refresh intervals come from server configuration, never from the page."""

from __future__ import annotations

import pytest

from viewlift_ai import defaults


def test_limits_expose_live_refresh_settings() -> None:
    values = defaults.limits()
    assert values["live_refresh_seconds"] == defaults.LIVE_REFRESH_SECONDS
    assert values["live_backoff_max_seconds"] == defaults.LIVE_BACKOFF_MAX_SECONDS
    assert values["live_diagnostics_seconds"] == defaults.LIVE_DIAGNOSTICS_SECONDS
    assert values["live_backoff_max_seconds"] >= values["live_refresh_seconds"]


def test_environment_overrides_live_intervals() -> None:
    env = {
        "VIEWLIFT_LIVE_REFRESH_SECONDS": "5",
        "VIEWLIFT_LIVE_BACKOFF_MAX_SECONDS": "60",
        "VIEWLIFT_LIVE_DIAGNOSTICS_SECONDS": "30",
    }
    assert defaults.live_refresh_seconds(env) == 5
    assert defaults.live_backoff_max_seconds(env) == 60
    assert defaults.live_diagnostics_seconds(env) == 30


def test_blank_environment_uses_the_default() -> None:
    blank = {"VIEWLIFT_LIVE_REFRESH_SECONDS": "  "}
    assert defaults.live_refresh_seconds(blank) == defaults.LIVE_REFRESH_SECONDS


@pytest.mark.parametrize("raw", ["fast", "0.1", "-3"])
def test_invalid_refresh_interval_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="VIEWLIFT_LIVE_REFRESH_SECONDS"):
        defaults.live_refresh_seconds({"VIEWLIFT_LIVE_REFRESH_SECONDS": raw})
