"""MCP secret scrubbing, policy switches, and analysis tools. Does not start stdio."""

from __future__ import annotations

import json
from typing import Any

import pytest

from viewlift_ai.mcp_server import guard, insights
from viewlift_ai.mcp_server.tools import (
    app_mutate,
    compare_flows,
    export_request,
    failed_requests,
    get_flow,
    network_summary,
    replay_flow,
    summarize_logs,
    wait_for_flow,
)

MASK = guard.MASK
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"


def _flow(flow_id: str, **extra: Any) -> dict[str, Any]:
    base = {
        "id": flow_id,
        "client_id": "app-a",
        "method": "GET",
        "host": "api.example.com",
        "path": "/v1/items",
        "query": "page=1&token=abc123secret",
        "status": 200,
        "duration_ms": 100.0,
        "size_bytes": 1000,
        "timing": {"connect_ms": 5.0, "ttfb_ms": 50.0},
        "overview": {"url": "https://api.example.com/v1/items?page=1&token=abc123secret"},
        "request": {
            "headers": {"Authorization": f"Bearer {JWT}", "Accept": "application/json"},
            "cookies": [{"name": "sid", "value": "cookie-value"}],
            "body": {"kind": "json", "raw": '{"user":"me","password":"hunter2"}'},
        },
        "response": {
            "headers": {"Content-Type": "application/json"},
            "body": {"kind": "json", "raw": '{"access_token":"tok-1","items":[1,2]}'},
        },
    }
    base.update(extra)
    return base


class FakeConsole:
    base_url = "http://127.0.0.1:8741"

    def __init__(self, flows: list[dict[str, Any]] | None = None) -> None:
        self.flows = flows or []
        self.calls: list[tuple[str, str, object]] = []
        self.unlocked = True

    def get(self, path: str, query: object = None) -> dict[str, Any]:
        self.calls.append(("GET", path, query))
        if path == "/api/debug/clients":
            return {"clients": [{"id": "app-a", "bundle_id": "com.a", "online": True}]}
        if path == "/api/reveal/status":
            return {"unlocked": self.unlocked, "unlock_token": "do-not-leak"}
        if path == "/api/flows":
            return {"flows": list(self.flows)}
        if path.startswith("/api/flows/"):
            flow_id = path.rsplit("/", 1)[-1]
            return next((f for f in self.flows if f["id"] == flow_id), _flow(flow_id))
        if path == "/api/logs":
            return {
                "logs": [
                    {
                        "id": "1",
                        "level": "error",
                        "message": "Load failed id 42",
                        "subsystem": "net",
                    },
                    {
                        "id": "2",
                        "level": "error",
                        "message": "Load failed id 77",
                        "subsystem": "net",
                    },
                    {"id": "3", "level": "info", "message": "token=abcdef ok", "subsystem": "auth"},
                ]
            }
        return {}

    def post(self, path: str, body: object = None) -> dict[str, Any]:
        self.calls.append(("POST", path, body))
        return {"ok": True}


def _posted(console: FakeConsole) -> list[str]:
    return [path for method, path, _ in console.calls if method == "POST"]


def test_scrub_masks_headers_json_keys_and_pairs() -> None:
    cleaned = guard.scrub(
        {
            "headers": {"Authorization": "Bearer abc.def.ghi", "X-Api-Key": "k1", "Accept": "*/*"},
            "defaults": [
                {"key": "authToken", "value": "secret-value"},
                {"key": "theme", "value": "dark"},
            ],
            "cookies": [{"name": "session", "value": "c1", "domain": ".example.com"}],
            "unlock_token": "drop-me",
            "has_token": True,
        }
    )
    text = json.dumps(cleaned)
    for secret in ("abc.def.ghi", "k1", "secret-value", "c1", "drop-me"):
        assert secret not in text
    assert cleaned["headers"]["Accept"] == "*/*"
    assert cleaned["defaults"][1]["value"] == "dark"
    assert cleaned["cookies"][0]["name"] == "session"
    assert cleaned["cookies"][0]["domain"] == ".example.com"
    assert cleaned["has_token"] is True
    assert "unlock_token" not in cleaned


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        (f"Authorization: Bearer {JWT}", JWT),
        (f"jwt {JWT}", JWT),
        ("https://cdn.example.com/a.m3u8?hdnts=exp=1~hmac=ff&x=1", "exp=1~hmac=ff"),
        ("GET /v1?api_key=AbCd1234&page=2", "AbCd1234"),
        ('{"refresh_token":"r-123456"}', "r-123456"),
        ("password=hunter22 user=me", "hunter22"),
        ("aws AKIAABCDEFGHIJKLMNOP done", "AKIAABCDEFGHIJKLMNOP"),
        (
            "-----BEGIN PRIVATE KEY-----\nMIIEv\n-----END PRIVATE KEY-----",
            "MIIEv",
        ),
    ],
)
def test_scrub_text_masks_patterns(text: str, secret: str) -> None:
    assert secret not in guard.scrub_text(text)


def test_scrub_keeps_ordinary_text_and_images() -> None:
    assert guard.scrub_text("page=2&sort=asc") == "page=2&sort=asc"
    assert guard.scrub({"base64": "eyJabc.eyJdef.ghijkl"})["base64"] == "eyJabc.eyJdef.ghijkl"
    long = "x" * (guard.MAX_STRING + 10)
    assert "truncated 10 chars" in guard.scrub_text(long)


def test_get_flow_masks_credentials() -> None:
    console = FakeConsole([_flow("f1")])
    text = json.dumps(get_flow(console, flow_id="f1", client_id="app-a"))  # type: ignore[arg-type]
    for secret in (JWT, "abc123secret", "hunter2", "tok-1", "cookie-value"):
        assert secret not in text
    assert "api.example.com" in text


def test_reveal_needs_env_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    console = FakeConsole([_flow("f1")])
    monkeypatch.delenv(guard.ALLOW_REVEAL_ENV, raising=False)
    result = get_flow(console, flow_id="f1", client_id="app-a", reveal=True)  # type: ignore[arg-type]
    assert result["revealed"] is False
    assert not any(isinstance(q, dict) and q.get("reveal") for _m, _p, q in console.calls)

    monkeypatch.setenv(guard.ALLOW_REVEAL_ENV, "1")
    revealed = get_flow(console, flow_id="f1", client_id="app-a", reveal=True)  # type: ignore[arg-type]
    assert JWT not in json.dumps(revealed)
    assert "do-not-leak" not in json.dumps(revealed)


def test_read_only_blocks_mutations(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.READ_ONLY_ENV, "1")
    console = FakeConsole()
    result = app_mutate(  # type: ignore[arg-type]
        console, client_id="app-a", command="ui.highlight", payload={"id": "x"}, confirm=True
    )
    assert result["ok"] is False
    assert "read-only" in result["detail"]
    assert replay_flow(console, flow_id="f1", confirm=True)["ok"] is False  # type: ignore[arg-type]
    assert _posted(console) == []


def test_network_summary_and_failures() -> None:
    flows = [
        _flow("a", status=200, duration_ms=100.0),
        _flow("b", status=500, duration_ms=4000.0),
        _flow("c", status=401, duration_ms=200.0),
        _flow("d", status=None, error="timed out", duration_ms=None),
    ]
    console = FakeConsole(flows)
    summary = network_summary(console, client_id="app-a")  # type: ignore[arg-type]
    assert summary["requests"] == 4
    assert summary["failures"] == 3
    assert summary["repeated"][0]["count"] == 4
    assert any("server errors" in hint for hint in summary["hints"])
    assert summary["slowest"][0]["id"] == "b"
    failed = failed_requests(console, client_id="app-a")  # type: ignore[arg-type]
    assert failed["failed"] == 3
    assert "abc123secret" not in json.dumps(summary)


def test_percentile_nearest_rank() -> None:
    assert insights.percentile([1, 2, 3, 4], 50) == 2
    assert insights.percentile([1, 2, 3, 4], 95) == 4
    assert insights.percentile([], 50) is None


def test_summarize_logs_groups_signatures() -> None:
    summary = summarize_logs(FakeConsole(), client_id="app-a")  # type: ignore[arg-type]
    top = summary["top_issues"][0]
    assert top["signature"] == "Load failed id #"
    assert top["count"] == 2
    assert summary["levels"] == {"error": 2, "info": 1}


def test_export_request_keeps_secrets_masked() -> None:
    console = FakeConsole([_flow("f1", method="POST")])
    for language in ("curl", "swift", "python"):
        result = export_request(  # type: ignore[arg-type]
            console, flow_id="f1", client_id="app-a", language=language
        )
        assert JWT not in result["code"]
        assert "hunter2" not in result["code"]
        assert "api.example.com" in result["code"]
    bad = export_request(console, flow_id="f1", client_id="app-a", language="ruby")  # type: ignore[arg-type]
    assert bad["ok"] is False


def test_export_request_refuses_other_app() -> None:
    console = FakeConsole([_flow("f1", client_id="app-b")])
    result = export_request(console, flow_id="f1", client_id="app-a")  # type: ignore[arg-type]
    assert result["ok"] is False


def test_compare_flows_reports_differences() -> None:
    a = _flow("a")
    b = _flow("b", status=500, timing={"connect_ms": 5.0, "ttfb_ms": 900.0})
    b["response"] = {
        "headers": {"Content-Type": "application/json", "Retry-After": "5"},
        "body": {"kind": "json", "raw": '{"error":"boom"}'},
    }
    result = compare_flows(FakeConsole([a, b]), flow_a="a", flow_b="b", client_id="app-a")  # type: ignore[arg-type]
    assert result["overview"]["status"] == {"a": 200, "b": 500}
    assert result["timing"]["ttfb_ms"]["delta"] == 850.0
    assert result["response_headers"]["only_b"] == ["Retry-After"]
    assert "error" in result["response_body"]["only_b"]


def test_wait_for_flow_returns_new_row() -> None:
    console = FakeConsole([_flow("old")])
    ticks = {"n": 0}

    def sleep(_seconds: float) -> None:
        ticks["n"] += 1
        console.flows.insert(0, _flow("new"))

    result = wait_for_flow(console, client_id="app-a", timeout_s=5, sleep=sleep)  # type: ignore[arg-type]
    assert result["ok"] is True
    assert result["flow"]["id"] == "new"
    assert ticks["n"] == 1


def test_audit_scrubs_arguments() -> None:
    log = guard.AuditLog(capacity=2)
    log.record("get_flow", {"flow_id": "f", "token": "abc"}, {"ok": True}, 1.0)
    log.record("a", {}, {"ok": False}, 1.0)
    log.record("b", {}, {}, 1.0)
    rows = log.rows()
    assert [row["tool"] for row in rows] == ["b", "a"]
    assert rows[1]["ok"] is False
    log.record("c", {"token": "abc"}, {}, 1.0)
    assert log.rows()[0]["args"]["token"] == MASK
