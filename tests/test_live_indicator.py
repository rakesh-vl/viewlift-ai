"""Sidebar connection indicator is shared chrome, not a Network/Logs socket."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL = ROOT / "src/viewlift_ai/web/static/js/shell.js"
APP = ROOT / "src/viewlift_ai/web/static/js/app.js"
BASE = ROOT / "src/viewlift_ai/web/templates/base.html"


def test_shell_owns_the_sidebar_connection_indicator() -> None:
    shell = SHELL.read_text()
    app = APP.read_text()
    base = BASE.read_text()
    assert "function setConnection" in shell
    assert "Scope.watch(setConnection)" in shell
    assert 'getElementById("live-label")' in shell
    assert 'getElementById("live-label")' not in app
    assert 'getElementById("live-dot")' not in app
    assert 'id="live-label"' in base
    assert "shell.js?v=" in base
