"""Built-in scratchpad scripts. Boop shape: a JSON header plus function main(state)."""

from __future__ import annotations

BUILTIN_SCRIPTS: tuple[tuple[str, str], ...] = (
    (
        "pretty-json",
        """\
/**
{
  "api": 1,
  "name": "Pretty JSON",
  "description": "Indent JSON with two spaces.",
  "author": "Viewlift AI",
  "tags": "json,format"
}
**/

function main(state) {
  try {
    state.text = JSON.stringify(JSON.parse(state.text), null, 2);
  } catch (error) {
    state.postError("Invalid JSON.");
  }
}
""",
    ),
    (
        "minify-json",
        """\
/**
{
  "api": 1,
  "name": "Minify JSON",
  "description": "Collapse JSON onto one line.",
  "author": "Viewlift AI",
  "tags": "json,format"
}
**/

function main(state) {
  try {
    state.text = JSON.stringify(JSON.parse(state.text));
  } catch (error) {
    state.postError("Invalid JSON.");
  }
}
""",
    ),
    (
        "query-to-json",
        """\
/**
{
  "api": 1,
  "name": "Query string to JSON",
  "description": "Turn a query string into a JSON object. Repeated keys become arrays.",
  "author": "Viewlift AI",
  "tags": "url,json"
}
**/

function main(state) {
  const raw = state.text.trim().replace(/^\\?/, "");
  const params = new URLSearchParams(raw);
  const obj = {};
  for (const [key, value] of params) {
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const current = obj[key];
      obj[key] = Array.isArray(current) ? current.concat(value) : [current, value];
    } else {
      obj[key] = value;
    }
  }
  state.text = JSON.stringify(obj, null, 2);
}
""",
    ),
    (
        "json-to-query",
        """\
/**
{
  "api": 1,
  "name": "JSON to query string",
  "description": "Turn a JSON object into a query string.",
  "author": "Viewlift AI",
  "tags": "url,json"
}
**/

function main(state) {
  let data;
  try {
    data = JSON.parse(state.text);
  } catch (error) {
    state.postError("Invalid JSON.");
    return;
  }
  if (data === null || typeof data !== "object" || Array.isArray(data)) {
    state.postError("JSON must be an object.");
    return;
  }
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(data)) {
    if (Array.isArray(value)) {
      for (const item of value) params.append(key, String(item));
    } else if (value !== null && typeof value === "object") {
      params.append(key, JSON.stringify(value));
    } else if (value !== undefined && value !== null) {
      params.append(key, String(value));
    }
  }
  state.text = params.toString();
}
""",
    ),
    (
        "url-decode",
        """\
/**
{
  "api": 1,
  "name": "URL decode",
  "description": "Decode percent-encoding.",
  "author": "Viewlift AI",
  "tags": "url"
}
**/

function main(state) {
  try {
    state.text = decodeURIComponent(state.text);
  } catch (error) {
    state.postError("Could not URL-decode this text.");
  }
}
""",
    ),
    (
        "url-encode",
        """\
/**
{
  "api": 1,
  "name": "URL encode",
  "description": "Percent-encode the text.",
  "author": "Viewlift AI",
  "tags": "url"
}
**/

function main(state) {
  state.text = encodeURIComponent(state.text);
}
""",
    ),
    (
        "base64-decode",
        """\
/**
{
  "api": 1,
  "name": "Base64 decode",
  "description": "Decode base64 to UTF-8 text.",
  "author": "Viewlift AI",
  "tags": "base64"
}
**/

function main(state) {
  try {
    const binary = atob(state.text.trim());
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    state.text = new TextDecoder().decode(bytes);
  } catch (error) {
    state.postError("Not valid base64.");
  }
}
""",
    ),
    (
        "base64-encode",
        """\
/**
{
  "api": 1,
  "name": "Base64 encode",
  "description": "Encode UTF-8 text as base64.",
  "author": "Viewlift AI",
  "tags": "base64"
}
**/

function main(state) {
  const bytes = new TextEncoder().encode(state.text);
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  state.text = btoa(binary);
}
""",
    ),
    (
        "jwt-decode",
        """\
/**
{
  "api": 1,
  "name": "JWT decode",
  "description": "Show the JWT header and payload. The signature is not verified.",
  "author": "Viewlift AI",
  "tags": "jwt,json"
}
**/

function main(state) {
  const parts = state.text.trim().split(".");
  if (parts.length < 2) {
    state.postError("Not a JWT. Expected header.payload.signature.");
    return;
  }
  function decodePart(part) {
    const pad = part.replace(/-/g, "+").replace(/_/g, "/");
    const padded = pad + "=".repeat((4 - (pad.length % 4)) % 4);
    const binary = atob(padded);
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes));
  }
  try {
    state.text = JSON.stringify(
      { header: decodePart(parts[0]), payload: decodePart(parts[1]) },
      null,
      2
    );
  } catch (error) {
    state.postError("Could not decode JWT.");
  }
}
""",
    ),
    (
        "unix-to-iso",
        """\
/**
{
  "api": 1,
  "name": "Unix time to ISO",
  "description": "Convert seconds or milliseconds since the epoch to ISO 8601.",
  "author": "Viewlift AI",
  "tags": "time"
}
**/

function main(state) {
  const value = Number(state.text.trim());
  if (!Number.isFinite(value)) {
    state.postError("Not a number.");
    return;
  }
  const ms = Math.abs(value) < 1e12 ? value * 1000 : value;
  const date = new Date(ms);
  if (Number.isNaN(date.getTime())) {
    state.postError("Not a valid timestamp.");
    return;
  }
  state.text = date.toISOString();
}
""",
    ),
    (
        "iso-to-unix",
        """\
/**
{
  "api": 1,
  "name": "ISO to Unix time",
  "description": "Convert a date to Unix seconds.",
  "author": "Viewlift AI",
  "tags": "time"
}
**/

function main(state) {
  const date = new Date(state.text.trim());
  if (Number.isNaN(date.getTime())) {
    state.postError("Not a valid date.");
    return;
  }
  state.text = String(Math.floor(date.getTime() / 1000));
}
""",
    ),
    (
        "headers-to-json",
        """\
/**
{
  "api": 1,
  "name": "Headers to JSON",
  "description": "Turn Header: value lines into a JSON object.",
  "author": "Viewlift AI",
  "tags": "http,json"
}
**/

function main(state) {
  const obj = {};
  for (const line of state.text.split(/\\n/)) {
    const index = line.indexOf(":");
    if (index <= 0) continue;
    const key = line.slice(0, index).trim();
    const value = line.slice(index + 1).trim();
    if (!key) continue;
    if (Object.prototype.hasOwnProperty.call(obj, key)) {
      const current = obj[key];
      obj[key] = Array.isArray(current) ? current.concat(value) : [current, value];
    } else {
      obj[key] = value;
    }
  }
  if (!Object.keys(obj).length) {
    state.postError("No Header: value lines found.");
    return;
  }
  state.text = JSON.stringify(obj, null, 2);
}
""",
    ),
    (
        "extract-urls",
        """\
/**
{
  "api": 1,
  "name": "Extract URLs",
  "description": "Keep only http and https URLs, one per line.",
  "author": "Viewlift AI",
  "tags": "url,text"
}
**/

function main(state) {
  const found = state.text.match(/https?:\\/\\/[^\\s<>"']+/g) || [];
  if (!found.length) {
    state.postError("No URLs found.");
    return;
  }
  state.text = found.join("\\n");
}
""",
    ),
    (
        "sort-lines",
        """\
/**
{
  "api": 1,
  "name": "Sort lines",
  "description": "Sort lines alphabetically.",
  "author": "Viewlift AI",
  "tags": "text"
}
**/

function main(state) {
  state.text = state.text.split("\\n").sort((a, b) => a.localeCompare(b)).join("\\n");
}
""",
    ),
    (
        "unique-lines",
        """\
/**
{
  "api": 1,
  "name": "Unique lines",
  "description": "Drop duplicate lines and keep the first occurrence.",
  "author": "Viewlift AI",
  "tags": "text"
}
**/

function main(state) {
  const seen = new Set();
  const lines = [];
  for (const line of state.text.split("\\n")) {
    if (seen.has(line)) continue;
    seen.add(line);
    lines.push(line);
  }
  state.text = lines.join("\\n");
}
""",
    ),
    (
        "trim-lines",
        """\
/**
{
  "api": 1,
  "name": "Trim lines",
  "description": "Trim whitespace on each line.",
  "author": "Viewlift AI",
  "tags": "text"
}
**/

function main(state) {
  state.text = state.text.split("\\n").map((line) => line.trim()).join("\\n");
}
""",
    ),
    (
        "count",
        """\
/**
{
  "api": 1,
  "name": "Count",
  "description": "Report characters and lines. The text stays as it is.",
  "author": "Viewlift AI",
  "tags": "text"
}
**/

function main(state) {
  const text = state.text;
  const lines = text === "" ? 0 : text.split(/\\n/).length;
  state.postInfo(text.length + " characters, " + lines + " lines");
}
""",
    ),
)
