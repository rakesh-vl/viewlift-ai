"""Classify beacon and VLAnalytics client flows."""

from __future__ import annotations

from datetime import UTC, datetime

from viewlift_ai.models.flow import FlowRecord, FlowSource
from viewlift_ai.services.analytics_inspect import (
    classify_flow,
    extract_params,
    list_analytics_traffic,
)


def _flow(
    *,
    host: str,
    path: str = "/",
    query: str | None = None,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> FlowRecord:
    return FlowRecord(
        id="f1",
        method="POST",
        host=host,
        path=path,
        status=200,
        duration_ms=12,
        size_bytes=100,
        protocol="https",
        source=FlowSource.SDK,
        created_at=datetime.now(UTC),
        request_headers=headers or {"content-type": "application/json"},
        request_body=body,
        query=query,
    )


def test_classify_user_and_player_beacons() -> None:
    assert (
        classify_flow(_flow(host="api.example.com", path="/userBeacon/v1"))
        == "user_beacon"
    )
    assert (
        classify_flow(_flow(host="api.example.com", path="/playerBeacon/events"))
        == "player_beacon"
    )


def test_classify_by_configured_beacon_host() -> None:
    flow = _flow(host="beacons.viewlift.com", path="/ingest")
    assert (
        classify_flow(
            flow,
            user_beacon_hosts={"beacons.viewlift.com"},
        )
        == "user_beacon"
    )


def test_classify_vlanalytics_clients() -> None:
    assert classify_flow(_flow(host="launches.appsflyer.com", path="/api")) == "client:appsFlyer"
    assert classify_flow(_flow(host="clevertap-prod.com", path="/a")) == "client:cleverTap"
    assert classify_flow(_flow(host="dpm.demdex.net", path="/id")) == "client:adobe"


def test_extract_params_from_query_and_json_body() -> None:
    flow = _flow(
        host="beacons.viewlift.com",
        path="/player",
        query="eventName=play&vid=abc",
        body=b'{"bitrate":3200,"userId":"u1"}',
    )
    params = extract_params(flow)
    assert params["eventName"] == "play"
    assert params["vid"] == "abc"
    assert params["bitrate"] == 3200
    assert params["userId"] == "u1"


def test_list_analytics_traffic_buckets() -> None:
    flows = [
        _flow(host="x.com", path="/userBeacon", query="eventName=login"),
        _flow(host="launches.appsflyer.com", path="/v1", body=b'{"event":"af_purchase"}'),
        _flow(host="cdn.example.com", path="/image.png"),
    ]
    result = list_analytics_traffic(flows, redact_params=lambda p: p)
    assert result["total"] == 2
    assert result["counts"]["user_beacon"] == 1
    assert result["counts"]["client:appsFlyer"] == 1
    assert result["user_beacon"][0]["event_hint"] == "login"
