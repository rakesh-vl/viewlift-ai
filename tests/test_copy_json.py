"""JSON bodies expose a button that copies the pretty text."""

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

const clicks = [];
const els = new Map();
function makeEl() {
  return {
    value: "",
    textContent: "",
    innerHTML: "",
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
    dataset: {},
  };
}
global.window = global;
global.CSS = { escape: (value) => String(value) };
global.localStorage = { getItem() { return ""; }, setItem() {} };
const copied = [];
const nav = global.navigator || {};
nav.clipboard = { writeText: async (text) => { copied.push(text); } };
global.navigator = nav;
global.document = {
  getElementById: (id) => {
    if (!els.has(id)) els.set(id, makeEl());
    return els.get(id);
  },
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => makeEl(),
  addEventListener: (type, fn) => { if (type === "click") clicks.push(fn); },
  body: makeEl(),
};

vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
vm.runInThisContext(fs.readFileSync(process.argv[1], "utf8"));
const page = global.ViewliftNetwork;

const root = {
  innerHTML: "",
  querySelector() { return null; },
  querySelectorAll() { return []; },
};
page.detailTab = "response";
page.renderDetail({
  method: "POST",
  host: "golfchannel.api.viewlift.com",
  path: "/graphql",
  graphql: true,
  operation: "Bootstrap",
  overview: { url: "https://golfchannel.api.viewlift.com/graphql", method: "POST" },
  request: { headers: {}, body: { kind: "empty" } },
  response: {
    headers: { "content-type": "application/json" },
    body: { kind: "json", pretty: '{\n  "data": {\n    "ok": true\n  }\n}' },
  },
  query: [],
  websocket_messages: [],
}, root);

assert.match(root.innerHTML, /Copy JSON/);
assert.match(root.innerHTML, /data-copy-label="Copy JSON"/);
assert.match(root.innerHTML, /class="json-tree"/);
assert.match(root.innerHTML, /data-json-filter/);
assert.match(root.innerHTML, /data-json-node-copy/);
assert.match(root.innerHTML, /&quot;ok&quot;: true/);
const responseAt = root.innerHTML.indexOf('data-panel="response"');
const bodyAt = root.innerHTML.indexOf('data-panel="body"');
assert.ok(responseAt >= 0 && bodyAt > responseAt);
assert.match(root.innerHTML.slice(responseAt, bodyAt), /Copy JSON/);
assert.doesNotMatch(root.innerHTML.slice(0, responseAt), /Copy JSON/);

const block = page.copyBlockHtml('{\n  "n": 1\n}', "Copy JSON", "Body");
assert.match(block, /data-copy-text/);
assert.match(block, /class="json-tree"/);
assert.match(block, /&quot;n&quot;: 1/);
const tree = global.VLJsonTree.html('{"a":{"b":1},"c":[true,null]}', { toolbar: true, copyBar: true });
assert.match(tree, /data-json-copy-all/);
assert.match(tree, /jt-key">a</);
assert.match(tree, /jt-boolean">true</);

const pre = { textContent: '{\n  "n": 1\n}' };
const button = {
  textContent: "Copy JSON",
  dataset: { copyLabel: "Copy JSON" },
  closest(sel) {
    if (sel === "[data-copy-text]") return button;
    if (sel === ".copy-block") return { querySelector: () => pre };
    return null;
  },
};
assert.ok(clicks.length >= 2);
(async () => {
  for (const click of clicks) click({ target: button });
  await Promise.resolve();
  assert.deepEqual(copied, ['{\n  "n": 1\n}']);
  assert.equal(button.textContent, "Copied");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
"""


def test_response_json_has_copy_button() -> None:
    node = shutil.which("node")
    assert node, "node is required"
    tree = ROOT / "src/viewlift_ai/web/static/js/json_tree.js"
    subprocess.run([node, "--check", str(APP)], check=True)
    subprocess.run([node, "--check", str(tree)], check=True)
    subprocess.run([node, "--check", str(ROOT / "src/viewlift_ai/web/static/js/debug.js")], check=True)
    subprocess.run([node, "--check", str(ROOT / "src/viewlift_ai/web/static/js/graphql.js")], check=True)
    subprocess.run([node, "-e", NODE, str(APP), str(tree)], check=True)
