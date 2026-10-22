"""Addon snapshot mapping from mitmproxy flows (no live proxy)."""

from __future__ import annotations

from mitmproxy.test import tflow

from viewlift_ai.proxy.addon import _looks_like_tls_failure, _to_snapshot


def test_to_snapshot_maps_http_flow() -> None:
    flow = tflow.tflow(resp=True)
    flow.request.headers["X-Request-Id"] = "req-abc-123"
    snap = _to_snapshot(flow, record_bodies=True)
    assert snap.id == flow.id
    assert snap.method == flow.request.method
    assert snap.status == flow.response.status_code
    assert snap.request_body == bytes(flow.request.raw_content or b"")
    assert snap.record_bodies is True
    assert snap.request_id == "req-abc-123"


def test_to_snapshot_without_bodies() -> None:
    flow = tflow.tflow(resp=True)
    snap = _to_snapshot(flow, record_bodies=False)
    assert snap.request_body == b""
    assert snap.response_body == b""


def test_tls_failure_heuristic() -> None:
    assert _looks_like_tls_failure("Client TLS handshake failed. certificate unknown")
    assert _looks_like_tls_failure("SSL error")
    assert not _looks_like_tls_failure("connection reset by peer")
