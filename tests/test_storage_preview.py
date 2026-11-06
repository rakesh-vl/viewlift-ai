"""Sandbox file preview: plist, image, and the other kinds the storage panel shows."""

import base64
import json
import plistlib
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREVIEW_JS = ROOT / "src/viewlift_ai/web/static/js/storage_preview.js"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
ARCHIVE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>$archiver</key><string>NSKeyedArchiver</string>
  <key>$version</key><integer>100000</integer>
  <key>$top</key><dict><key>root</key><dict><key>CF$UID</key><integer>1</integer></dict></dict>
  <key>$objects</key><array>
    <string>$null</string>
    <dict>
      <key>name</key><string>Alpha</string>
      <key>$class</key><dict><key>CF$UID</key><integer>2</integer></dict>
    </dict>
    <dict>
      <key>$classname</key><string>VLItem</string>
      <key>$classes</key><array><string>VLItem</string><string>NSObject</string></array>
    </dict>
  </array>
</dict></plist>
"""


def _node_preview(cases: list[dict]) -> list[dict]:
    script = r"""
const fs = require("fs");
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
eval(fs.readFileSync(process.argv[1], "utf8"));
const out = cases.map((item) => {
  const prepared = globalThis.VLFilePreview.prepare(item);
  return { kind: prepared.kind, plain: prepared.plain, html: prepared.html, label: prepared.label };
});
process.stdout.write(JSON.stringify(out));
"""
    completed = subprocess.run(
        ["node", "-e", script, str(PREVIEW_JS)],
        input=json.dumps(cases),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


def _case(path: str, payload: bytes | str, encoding: str) -> dict:
    if encoding == "base64":
        content = base64.b64encode(payload).decode() if isinstance(payload, bytes) else payload
    else:
        content = payload.decode() if isinstance(payload, bytes) else payload
    return {"path": path, "encoding": encoding, "content": content}


def _normalize(value):
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    if isinstance(value, bytes):
        return {"__data": base64.b64encode(value).decode()}
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return {"__date": moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    return value


def _same(expected, actual) -> bool:
    if isinstance(expected, dict) and isinstance(actual, dict) and "__date" in expected:
        left = datetime.fromisoformat(expected["__date"].replace("Z", "+00:00"))
        right = datetime.fromisoformat(str(actual["__date"]).replace("Z", "+00:00"))
        return abs((left - right).total_seconds()) < 0.01
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_same(expected[key], actual[key]) for key in expected)
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(_same(left, right) for left, right in zip(expected, actual, strict=True))
    if isinstance(expected, str) and isinstance(actual, str):
        try:
            return abs(float(expected) - float(actual)) < 1e-9 or expected == actual
        except ValueError:
            return expected == actual
    return expected == actual


def test_storage_preview_renders_plist_image_and_other_files(tmp_path: Path):
    node = subprocess.run(["node", "--version"], capture_output=True, text=True, check=False)
    if node.returncode != 0:
        pytest.skip("node is required to execute the storage preview")

    sample = {
        "name": "viewlift",
        "on": True,
        "off": False,
        "count": -2,
        "big": 2**40,
        "ratio": 1.5,
        "note": "héllo <tag>",
        "when": datetime(2020, 1, 2, 3, 4, 5),
        "blob": b"\x00\x01hello",
        "items": [1, "two"],
        "nested": {"empty": {}, "more": []},
        "<script>": "a&b",
    }
    binary = plistlib.dumps(sample, fmt=plistlib.FMT_BINARY)
    xml = plistlib.dumps(sample, fmt=plistlib.FMT_XML)
    database = tmp_path / "app.sqlite"
    with sqlite3.connect(database) as connection:
        connection.execute("create table shows (id integer)")
    sqlite_bytes = database.read_bytes()

    cases = [
        _case("Library/Preferences/app.plist", binary, "base64"),
        _case("Library/Preferences/app.plist", xml, "utf8"),
        _case("Library/Preferences/archive.plist", ARCHIVE, "utf8"),
        _case("Library/Caches/pixel.png", PNG, "base64"),
        _case("note.txt", b"hello sandbox", "utf8"),
        _case("state.json", b'{"alpha": 1, "items": ["x"]}', "utf8"),
        _case("Library/HTTPStorages/store.sqlite", sqlite_bytes, "base64"),
        _case("Library/Caches/blob.bin", bytes(range(32)), "base64"),
        _case("empty.dat", b"", "utf8"),
        _case("icon.svg", b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>', "utf8"),
        _case("guide.pdf", b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF", "base64"),
    ]
    previews = _node_preview(cases)
    expected = _normalize(sample)

    assert previews[0]["kind"] == "plist"
    assert previews[0]["label"] == "Binary property list"
    assert _same(expected, previews[0]["plain"])
    assert "viewlift" in previews[0]["html"]
    assert "&lt;script&gt;" in previews[0]["html"]
    assert "a&amp;b" in previews[0]["html"]
    assert "<script>" not in previews[0]["html"]

    assert previews[1]["kind"] == "plist"
    assert previews[1]["label"] == "XML property list"
    assert _same(expected, previews[1]["plain"])

    assert previews[2]["kind"] == "plist"
    assert previews[2]["plain"] == {"__class": "VLItem", "name": "Alpha"}
    assert "NSKeyedArchiver" in previews[2]["html"]
    assert "VLItem" in previews[2]["html"]

    assert previews[3]["kind"] == "image"
    assert 'class="img-preview"' in previews[3]["html"]
    assert "image/png" in previews[3]["html"]
    assert "data-file-dims" in previews[3]["html"]

    assert previews[4]["kind"] == "text"
    assert "hello sandbox" in previews[4]["html"]

    assert previews[5]["kind"] == "json"
    assert previews[5]["plain"] == {"alpha": "1", "items": ["x"]}

    assert previews[6]["kind"] == "sqlite"
    assert "SQLite database" in previews[6]["html"]
    assert "Page size" in previews[6]["html"]
    assert "Read-only SQL" in previews[6]["html"]

    assert previews[7]["kind"] == "binary"
    assert "00000000" in previews[7]["html"]

    assert previews[8]["kind"] == "empty"
    assert "empty" in previews[8]["html"].lower()

    assert previews[9]["kind"] == "image"
    assert "image/svg+xml" in previews[9]["html"]

    assert previews[10]["kind"] == "pdf"
    assert "data-file-media" in previews[10]["html"]
    assert "PDF" in previews[10]["html"]
