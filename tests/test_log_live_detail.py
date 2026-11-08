"""Live log stream keeps the right-hand detail on the newest line."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/viewlift_ai/web/static/js/app.js"

NODE = r"""
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

const els = new Map();
function makeEl() {
  return {
    value: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    className: "",
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {},
    setAttribute() {},
    getAttribute() { return ""; },
    appendChild() {},
    scrollIntoView() {},
    options: [],
    dataset: {},
  };
}
function el(id) {
  if (!els.has(id)) els.set(id, makeEl());
  return els.get(id);
}

global.window = global;
global.CSS = { escape: (value) => String(value) };
global.document = {
  getElementById: (id) => el(id),
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => makeEl(),
  addEventListener() {},
  body: makeEl(),
};

const waits = [];
function detail(id, message) {
  return {
    id,
    subsystem: "com.viewlift.spinco",
    message,
    created_at: "2026-11-07T07:36:23+00:00",
    level: "info",
    source: "stdout",
    client_id: "app-1",
    request_id: "",
    udid: "",
    console_note: "Captured from app stdout/stderr",
    linked_flow_id: null,
  };
}
global.fetch = (url) => new Promise((resolve) => {
  const id = decodeURIComponent(String(url).split("/api/logs/")[1]);
  waits.push(() => resolve({
    ok: true,
    json: async () => detail(id, `${id}-FULL`),
  }));
});

vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const page = global.ViewliftNetwork;
page.logs = [];
page.logPending = [];
page.logPaused = false;
page.logSubsystems = new Set();
page.selectedLogId = null;
el("btn-log-pause").textContent = "Pause";

function line(id, message) {
  return {
    id,
    level: "info",
    source: "stdout",
    subsystem: "com.viewlift.spinco",
    message,
    created_at: "2026-11-07T07:36:23",
    client_id: "app-1",
    request_id: "",
  };
}

(async () => {
  page.queueLiveLog(line("older", "first"));
  page.queueLiveLog(line("newer", "second"));
  clearTimeout(page.logFlushTimer);
  page.logFlushTimer = null;
  page.flushLogPending();

  assert.equal(page.logPaused, false);
  assert.equal(page.selectedLogId, "newer");
  assert.equal(el("btn-log-pause").textContent, "Pause");
  assert.match(el("log-detail-body").innerHTML, /second/);
  assert.equal(el("log-detail-title").textContent, "com.viewlift.spinco");

  page.queueLiveLog(line("latest", "third"));
  clearTimeout(page.logFlushTimer);
  page.logFlushTimer = null;
  page.flushLogPending();
  assert.equal(page.selectedLogId, "latest");
  assert.match(el("log-detail-body").innerHTML, /third/);

  const stale = waits[0];
  const fresh = waits[1];
  stale();
  await Promise.resolve();
  await Promise.resolve();
  assert.match(el("log-detail-body").innerHTML, /third/);
  assert.doesNotMatch(el("log-detail-body").innerHTML, /newer-FULL/);
  fresh();
  await Promise.resolve();
  await Promise.resolve();
  assert.match(el("log-detail-body").innerHTML, /latest-FULL/);

  const pick = page.selectLog("older", true, true);
  assert.equal(page.logPaused, true);
  assert.equal(page.selectedLogId, "older");
  assert.equal(el("btn-log-pause").textContent, "Resume live");
  assert.match(el("log-detail-body").innerHTML, /first/);
  waits.at(-1)();
  await pick;

  page.queueLiveLog(line("after", "fourth"));
  assert.equal(page.selectedLogId, "older");
  assert.equal(page.logPending.length, 1);
  page.followLiveLog();
  assert.equal(page.selectedLogId, "older");

  page.setLogPaused(false);
  assert.equal(page.logPaused, false);
  assert.equal(page.selectedLogId, "after");
  assert.match(el("log-detail-body").innerHTML, /fourth/);
  assert.equal(el("btn-log-pause").textContent, "Pause");
  waits.at(-1)();
  await Promise.resolve();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


def test_live_mode_updates_log_detail() -> None:
    node = shutil.which("node")
    assert node, "node is required"
    subprocess.run([node, "--check", str(APP)], check=True)
    subprocess.run([node, "-e", NODE, str(APP)], check=True)
