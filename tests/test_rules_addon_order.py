"""Integration: request rule order throttle → map local → map remote → breakpoint."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mitmproxy.test import tflow

from viewlift_ai.proxy.rules import apply_request_rules, apply_response_rules
from viewlift_ai.services.rule_engine import RuleEngine
from viewlift_ai.services.rule_store import RuleConfigStore


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_request_order_map_local_short_circuits_before_map_remote(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "local.txt").write_text("from-local", encoding="utf-8")
    engine = RuleEngine(RuleConfigStore(tmp_path / "rules.json"))
    engine.set_map_local_root(str(root))
    engine.add_throttle(
        {"enabled": True, "host": "*", "latency_ms": 0, "offline": False}
    )
    engine.add_map_local(
        {
            "enabled": True,
            "match": {"method": "GET", "host": "api.test", "path": "/item"},
            "relative_path": "local.txt",
        }
    )
    engine.add_map_remote(
        {
            "enabled": True,
            "match": {"method": "GET", "host": "api.test", "path": "/item"},
            "target_url": "https://should-not-hit.example/",
        }
    )

    flow = tflow.tflow(resp=False)
    flow.request.host = "api.test"
    flow.request.path = "/item"
    flow.request.method = "GET"
    flow.request.scheme = "https"

    _run(apply_request_rules(engine, flow))
    assert flow.response is not None
    assert flow.response.status_code == 200
    assert b"from-local" in flow.response.content
    # Map remote must not have rewritten the URL after map local hit.
    assert "should-not-hit" not in flow.request.url


def test_offline_throttle_before_maps(tmp_path: Path) -> None:
    engine = RuleEngine(RuleConfigStore(tmp_path / "rules.json"))
    engine.add_throttle({"enabled": True, "host": "*", "offline": True})
    engine.add_map_local(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "relative_path": "x",
        }
    )
    flow = tflow.tflow(resp=False)
    _run(apply_request_rules(engine, flow))
    assert flow.response is not None
    assert flow.response.status_code == 503


def test_response_rewrite_after_body(tmp_path: Path) -> None:
    engine = RuleEngine(RuleConfigStore(tmp_path / "rules.json"))
    engine.add_rewrite(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "actions": [
                {"kind": "set_status", "status": 299},
                {
                    "kind": "replace_body_literal",
                    "find": "world",
                    "replace": "viewlift",
                },
            ],
        }
    )
    flow = tflow.tflow(resp=True)
    flow.response.content = b"hello world"
    _run(apply_response_rules(engine, flow))
    assert flow.response.status_code == 299
    assert flow.response.content == b"hello viewlift"
