"""Filter buttons follow committed state. Logs page has no Start/Stop stream buttons."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/viewlift_ai/web/static/js/app.js"
LOGS = ROOT / "src/viewlift_ai/web/templates/logs.html"
DASHBOARD = ROOT / "src/viewlift_ai/web/templates/dashboard.html"
CSS = ROOT / "src/viewlift_ai/web/static/css/app.css"

NODE = r"""
const assert = require("assert");
const empty = commitButtonPlan({ host: "" }, { host: "" });
assert.equal(empty.showApply, true);
assert.equal(empty.showClear, false);

const dirty = commitButtonPlan({ host: "api.example.com" }, { host: "" });
assert.equal(dirty.showApply, true);
assert.equal(dirty.showClear, false);

const applied = commitButtonPlan({ host: "api.example.com" }, { host: "api.example.com" });
assert.equal(applied.showApply, false);
assert.equal(applied.showClear, true);

const edited = commitButtonPlan({ host: "other.example" }, { host: "api.example.com" });
assert.equal(edited.showApply, true);
assert.equal(edited.showClear, true);
"""


def test_pages_hide_the_inactive_action() -> None:
    logs = LOGS.read_text()
    dashboard = DASHBOARD.read_text()
    css = CSS.read_text()
    # Logs page streams only from the SDK, so no simctl or device start/stop buttons.
    assert 'id="btn-log-start"' not in logs
    assert 'id="btn-log-stop"' not in logs
    assert 'id="btn-log-restart"' not in logs
    assert 'id="btn-log-clear"' in logs and "hidden" in logs.split('id="btn-log-clear"', 1)[1][:80]
    assert 'id="btn-clear"' in dashboard and "hidden" in dashboard.split('id="btn-clear"', 1)[1][:80]
    assert ".btn[hidden]" in css


def test_control_plans() -> None:
    node = shutil.which("node")
    assert node, "node is required to check control plans"
    src = APP.read_text()
    start = src.index("/* control-state */")
    end = src.index("/* /control-state */")
    script = src[start:end] + "\n" + NODE
    subprocess.run([node, "-e", script], check=True)
