"""Breakpoint pause/edit/resume/drop/timeout, stage constraints, ordering, serialization."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mitmproxy.test import tflow
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.breakpoints import pending_to_dict
from viewlift_ai.models.flow import RetentionPolicy
from viewlift_ai.models.rules import BREAKPOINT_TIMED_OUT, BreakpointStage, RequestMatchContext
from viewlift_ai.proxy.breakpoints import BreakpointRegistry, apply_decision_to_response
from viewlift_ai.proxy.rules import apply_request_rules, apply_response_rules
from viewlift_ai.services.rule_engine import RuleEngine, RuleValidationError
from viewlift_ai.services.rule_store import RuleConfigStore


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def _engine(tmp_path: Path) -> RuleEngine:
    return RuleEngine(RuleConfigStore(tmp_path / "rules.json"))


def _client(tmp_path: Path) -> TestClient:
    cfg = AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"t" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )
    return TestClient(create_app(cfg))


def test_breakpoint_defaults_disabled(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    assert engine.config.breakpoints == []
    rule = engine.add_breakpoint(
        {
            "match": {"method": "GET", "host": "api.test", "path": "/x"},
            "request_enabled": True,
        }
    )
    assert rule.enabled is False
    assert rule.request_enabled is True
    assert rule.response_enabled is False
    assert engine.any_mutating_enabled() is False
    engine.toggle_breakpoint(rule.id, True)
    assert engine.any_mutating_enabled() is True


def test_evaluate_independent_stages(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.add_breakpoint(
        {
            "enabled": True,
            "match": {"method": "*", "host": "api.test", "path": "*"},
            "request_enabled": True,
            "response_enabled": False,
        }
    )
    ctx = RequestMatchContext(method="GET", host="api.test", path="/a")
    assert engine.evaluate_breakpoint(ctx, stage=BreakpointStage.REQUEST) is not None
    assert engine.evaluate_breakpoint(ctx, stage=BreakpointStage.RESPONSE) is None


def test_pending_serialization() -> None:
    from viewlift_ai.models.breakpoints import PendingBreakpoint

    item = PendingBreakpoint(
        id="p1",
        flow_id="f1",
        rule_id="r1",
        stage=BreakpointStage.REQUEST,
        method="POST",
        host="h",
        path="/p",
        headers={"A": "1"},
        body=b'{"ok":true}',
        status=None,
        created_at=1.0,
        timeout_at=121.0,
    )
    data = pending_to_dict(item)
    assert data["id"] == "p1"
    assert data["stage"] == "request"
    assert data["body"] == '{"ok":true}'
    assert data["body_bytes"] == len(b'{"ok":true}')
    assert data["timed_out"] is False


def test_pause_edit_resume_and_drop() -> None:
    async def scenario() -> None:
        reg = BreakpointRegistry(timeout_s=5)
        pause_task = asyncio.create_task(
            reg.pause(
                flow_id="f1",
                rule_id="r1",
                stage=BreakpointStage.REQUEST,
                method="GET",
                host="api.test",
                path="/item",
                headers={"X": "1"},
                body=b"old",
            )
        )
        await asyncio.sleep(0.05)
        pending = reg.list_pending()
        assert len(pending) == 1
        pause_id = pending[0].id
        reg.resume(pause_id, {"headers": {"X": "2"}, "body": "new"})
        decision = await pause_task
        assert decision.action == "resume"
        assert decision.headers == {"X": "2"}
        assert decision.body == b"new"

        drop_task = asyncio.create_task(
            reg.pause(
                flow_id="f2",
                rule_id="r1",
                stage=BreakpointStage.RESPONSE,
                method="GET",
                host="api.test",
                path="/item",
                headers={},
                body=b"resp",
                status=200,
            )
        )
        await asyncio.sleep(0.05)
        pid = reg.list_pending()[0].id
        reg.drop(pid)
        dropped = await drop_task
        assert dropped.action == "drop"

    _run(scenario())


def test_timeout_auto_resumes_original_unchanged() -> None:
    async def scenario() -> None:
        events: list[str] = []
        reg = BreakpointRegistry(timeout_s=0.05)
        reg.subscribe_sync(lambda e: events.append(str(e.kind)))
        decision = await reg.pause(
            flow_id="f1",
            rule_id="r1",
            stage=BreakpointStage.REQUEST,
            method="GET",
            host="api.test",
            path="/t",
            headers={"H": "v"},
            body=b"keep",
        )
        assert decision.action == "timeout"
        assert decision.headers is None
        assert decision.body is None
        assert "timed_out" in events
        assert reg.list_pending() == []

    _run(scenario())


def test_stage_constraints_status_request_rejected() -> None:
    async def scenario() -> None:
        reg = BreakpointRegistry(timeout_s=5)
        task = asyncio.create_task(
            reg.pause(
                flow_id="f1",
                rule_id="r1",
                stage=BreakpointStage.REQUEST,
                method="GET",
                host="h",
                path="/",
                headers={},
                body=b"",
            )
        )
        await asyncio.sleep(0.05)
        pid = reg.list_pending()[0].id
        with pytest.raises(RuleValidationError, match="status edits"):
            reg.resume(pid, {"status": 201})
        reg.resume(pid, {"body": "ok"})
        await task

    _run(scenario())


def test_edit_body_header_limits() -> None:
    async def scenario() -> None:
        reg = BreakpointRegistry(timeout_s=5)
        task = asyncio.create_task(
            reg.pause(
                flow_id="f1",
                rule_id="r1",
                stage=BreakpointStage.RESPONSE,
                method="GET",
                host="h",
                path="/",
                headers={},
                body=b"x",
                status=200,
            )
        )
        await asyncio.sleep(0.05)
        pid = reg.list_pending()[0].id
        with pytest.raises(RuleValidationError, match="body exceeds"):
            reg.resume(pid, {"body": "x" * (1024 * 1024 + 1)})
        with pytest.raises(RuleValidationError, match="header"):
            reg.resume(pid, {"headers": {"Bad\nName": "1"}})
        reg.resume(pid, {"status": 299, "body": "ok", "headers": {"X": "y"}})
        decision = await task
        assert decision.status == 299
        assert decision.body == b"ok"

    _run(scenario())


def test_request_order_breakpoint_after_map_remote(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reg = BreakpointRegistry(timeout_s=2)
    engine.add_map_remote(
        {
            "enabled": True,
            "match": {"method": "GET", "host": "api.test", "path": "/item"},
            "target_url": "https://mapped.example/item",
            "preserve_path": False,
            "preserve_query": False,
        }
    )
    engine.add_breakpoint(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "request_enabled": True,
        }
    )

    flow = tflow.tflow(resp=False)
    flow.request.host = "api.test"
    flow.request.path = "/item"
    flow.request.method = "GET"
    flow.request.scheme = "https"

    async def scenario() -> None:
        task = asyncio.create_task(
            apply_request_rules(engine, flow, breakpoints=reg)
        )
        await asyncio.sleep(0.05)
        assert "mapped.example" in flow.request.url
        pending = reg.list_pending()
        assert len(pending) == 1
        assert pending[0].stage == BreakpointStage.REQUEST
        reg.resume(pending[0].id, {"headers": {"X-Edited": "1"}, "body": "edited"})
        await task
        assert flow.request.headers.get("X-Edited") == "1"
        assert flow.request.content == b"edited"

    _run(scenario())


def test_response_breakpoint_before_rewrite(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reg = BreakpointRegistry(timeout_s=2)
    engine.add_breakpoint(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "response_enabled": True,
        }
    )
    engine.add_rewrite(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "actions": [
                {"kind": "replace_body_literal", "find": "WORLD", "replace": "VIEWLIFT"},
            ],
        }
    )
    flow = tflow.tflow(resp=True)
    flow.response.content = b"hello WORLD"

    async def scenario() -> None:
        task = asyncio.create_task(
            apply_response_rules(engine, flow, breakpoints=reg)
        )
        await asyncio.sleep(0.05)
        pending = reg.list_pending()
        assert len(pending) == 1
        assert pending[0].stage == BreakpointStage.RESPONSE
        reg.resume(pending[0].id, {"body": "hello WORLD", "status": 200})
        await task
        assert flow.response.content == b"hello VIEWLIFT"

    _run(scenario())


def test_timeout_marks_exact_string_on_flow(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reg = BreakpointRegistry(timeout_s=0.05)
    engine.add_breakpoint(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "request_enabled": True,
        }
    )
    flow = tflow.tflow(resp=False)
    _run(apply_request_rules(engine, flow, breakpoints=reg))
    assert flow.metadata.get("breakpoint_timed_out") is True
    assert flow.metadata.get("breakpoint_mark") == BREAKPOINT_TIMED_OUT


def test_shutdown_resolves_pending() -> None:
    async def scenario() -> None:
        reg = BreakpointRegistry(timeout_s=30)
        task = asyncio.create_task(
            reg.pause(
                flow_id="f1",
                rule_id="r1",
                stage=BreakpointStage.REQUEST,
                method="GET",
                host="h",
                path="/",
                headers={},
                body=b"",
            )
        )
        await asyncio.sleep(0.05)
        assert len(reg.list_pending()) == 1
        await reg.shutdown()
        decision = await task
        assert decision.action == "resume"
        assert reg.list_pending() == []

    _run(scenario())


def test_api_configure_toggle_resume_drop(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        created = client.post(
            "/api/rules/breakpoints",
            json={
                "match": {"method": "GET", "host": "api.test", "path": "/x"},
                "request_enabled": True,
                "response_enabled": True,
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["enabled"] is False
        rule_id = body["id"]

        bad = client.post(
            "/api/rules/breakpoints",
            json={"match": {"method": "*", "host": "*", "path": "*"}},
        )
        assert bad.status_code == 400

        toggled = client.post(
            f"/api/rules/breakpoints/{rule_id}/toggle",
            params={"enabled": True},
        )
        assert toggled.json()["enabled"] is True
        assert client.get("/api/rules/status").json()["mutating_enabled"] is True

        page = client.get("/rules")
        assert b"Breakpoints" in page.content
        assert b'class="mutating-banner show"' in page.content

        pending = client.get("/api/breakpoints/pending")
        assert pending.status_code == 200
        assert pending.json()["pending"] == []

        # Drive registry via app state for resume/drop API coverage.
        registry: BreakpointRegistry = client.app.state.breakpoints

        async def pause_one() -> None:
            await registry.pause(
                flow_id="flow-api",
                rule_id=rule_id,
                stage=BreakpointStage.RESPONSE,
                method="GET",
                host="api.test",
                path="/x",
                headers={"A": "1"},
                body=b"body",
                status=200,
            )

        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(pause_one())
            loop.run_until_complete(asyncio.sleep(0.05))
            listed = client.get("/api/breakpoints/pending").json()["pending"]
            assert len(listed) == 1
            pause_id = listed[0]["id"]
            assert listed[0]["stage"] == "response"

            resumed = client.post(
                f"/api/breakpoints/{pause_id}/resume",
                json={"status": 201, "body": "edited", "headers": {"A": "2"}},
            )
            assert resumed.status_code == 200
            loop.run_until_complete(task)

            task2 = loop.create_task(pause_one())
            loop.run_until_complete(asyncio.sleep(0.05))
            pause_id2 = client.get("/api/breakpoints/pending").json()["pending"][0]["id"]
            dropped = client.post(f"/api/breakpoints/{pause_id2}/drop")
            assert dropped.status_code == 200
            loop.run_until_complete(task2)
        finally:
            loop.close()


def test_map_local_skips_request_breakpoint(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "local.txt").write_text("local", encoding="utf-8")
    engine = _engine(tmp_path)
    reg = BreakpointRegistry(timeout_s=2)
    engine.set_map_local_root(str(root))
    engine.add_map_local(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "relative_path": "local.txt",
        }
    )
    engine.add_breakpoint(
        {
            "enabled": True,
            "match": {"method": "*", "host": "*", "path": "*"},
            "request_enabled": True,
        }
    )
    flow = tflow.tflow(resp=False)
    _run(apply_request_rules(engine, flow, breakpoints=reg))
    assert flow.response is not None
    assert reg.list_pending() == []


def test_apply_timeout_metadata_response() -> None:
    from viewlift_ai.models.breakpoints import BreakpointDecision

    flow = tflow.tflow(resp=True)
    apply_decision_to_response(flow, BreakpointDecision(action="timeout"))
    assert flow.metadata["breakpoint_mark"] == BREAKPOINT_TIMED_OUT
