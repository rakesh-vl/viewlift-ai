"""GraphQL playground page, encrypted endpoints, and execute auth rules."""

from __future__ import annotations

import base64
import gzip
import json
import stat
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

import pytest
from fastapi.testclient import TestClient
from nacl.secret import SecretBox

from viewlift_ai.app import create_app
from viewlift_ai.config import AppConfig, SecretKeyState
from viewlift_ai.models.flow import FlowRecord, FlowSource, RetentionPolicy
from viewlift_ai.services.graphql_client import SameOriginRedirect
from viewlift_ai.services.graphql_store import GRAPHQL_ENDPOINTS_FILENAME, GraphQLError

ENDPOINT_ID = "a" * 32
OTHER_ID = "b" * 32
PASSWORD = "s3cret-value"
USERNAME = "motv-user"


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "ui.db",
        secret_key_path=tmp_path / "secret.key",
        retention=RetentionPolicy.SESSION,
        secret=SecretKeyState(
            key=b"a" * SecretBox.KEY_SIZE,
            used_keychain_fallback=True,
            fallback_path=tmp_path / "secret.key",
        ),
        proxy_enabled=False,
    )


class _Reply:
    def __init__(
        self,
        status: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b'{"data":{"ping":"ok"}}',
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.status = status
        self.headers = headers or {"Content-Type": "application/json"}
        self.body = body

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
    ) -> tuple[int, dict[str, str], bytes]:
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        return self.status, self.headers, self.body


def _client(tmp_path: Path, reply: _Reply | None = None) -> tuple[TestClient, _Reply]:
    opener = reply or _Reply()
    client = TestClient(create_app(_config(tmp_path)))
    client.__enter__()
    client.app.state.graphql_opener = opener
    return client, opener


def _save(client: TestClient, endpoints: list[dict[str, object]]) -> dict[str, object]:
    response = client.put(
        "/api/graphql/endpoints",
        json={"endpoints": endpoints},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _endpoint(**extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": ENDPOINT_ID,
        "name": "MOTV staging",
        "url": "https://api.example/graphql",
        "playground_url": "https://api.example/playground",
        "username": USERNAME,
        "password": PASSWORD,
    }
    row.update(extra)
    return row


def test_pages_render(tmp_path: Path) -> None:
    client, _opener = _client(tmp_path)
    try:
        page = client.get("/graphql")
        assert page.status_code == 200
        assert 'aria-current="page"' in page.text
        assert 'id="gql-query"' in page.text
        assert 'id="gql-schema"' in page.text
        assert "myoutdoortv.staging.api.viewlift.com/graphql" in page.text
        setup = client.get("/setup")
        assert setup.status_code == 200
        assert 'id="gql-setup-save"' in setup.text
        assert 'id="gql-seen"' in setup.text
        assert "/playground" in setup.text
        script = client.get("/static/js/app.js")
        assert b'id="edit-playground"' in script.content
        assert b"vl-graphql-prefill" in script.content
        assert b"data-gql-base" in script.content
    finally:
        client.__exit__(None, None, None)


def test_password_is_not_returned_or_stored_in_plaintext(tmp_path: Path) -> None:
    client, _opener = _client(tmp_path)
    try:
        saved = _save(client, [_endpoint()])
        endpoint = saved["endpoints"][0]
        assert endpoint["has_password"] is True
        assert endpoint["username"] == USERNAME
        assert "password" not in endpoint
        assert PASSWORD not in client.get("/api/graphql/endpoints").text
        raw = (tmp_path / GRAPHQL_ENDPOINTS_FILENAME).read_text(encoding="utf-8")
        assert PASSWORD not in raw
        assert USERNAME not in raw
        assert "https://api.example/graphql" in raw
        mode = (tmp_path / GRAPHQL_ENDPOINTS_FILENAME).stat().st_mode
        assert stat.S_IMODE(mode) == 0o600
    finally:
        client.__exit__(None, None, None)


def test_blank_password_keeps_the_saved_one_until_cleared(tmp_path: Path) -> None:
    client, opener = _client(tmp_path)
    try:
        _save(client, [_endpoint()])
        kept = _save(client, [{**_endpoint(), "password": ""}])
        assert kept["endpoints"][0]["has_password"] is True
        sent = client.post(
            "/api/graphql/execute",
            json={"endpoint_id": ENDPOINT_ID, "query": "{ ping }"},
        )
        assert sent.status_code == 200, sent.text
        expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
        assert opener.calls[-1]["headers"]["Authorization"] == expected
        assert PASSWORD not in sent.text
        cleared = _save(client, [{**_endpoint(), "password": "", "clear_password": True}])
        assert cleared["endpoints"][0]["has_password"] is False
        client.post(
            "/api/graphql/execute",
            json={"endpoint_id": ENDPOINT_ID, "query": "{ ping }"},
        )
        assert "Authorization" not in opener.calls[-1]["headers"]
    finally:
        client.__exit__(None, None, None)


def test_basic_auth_stays_on_the_saved_host(tmp_path: Path) -> None:
    client, opener = _client(tmp_path)
    try:
        _save(client, [_endpoint(), _endpoint(id=OTHER_ID, url="https://other.example/graphql")])
        other = client.post(
            "/api/graphql/execute",
            json={
                "endpoint_id": ENDPOINT_ID,
                "url": "https://other.example/graphql",
                "query": "{ ping }",
            },
        )
        assert other.status_code == 200, other.text
        assert "Authorization" not in opener.calls[-1]["headers"]
        assert "different host" in other.json()["auth_note"]
        bearer = client.post(
            "/api/graphql/execute",
            json={
                "endpoint_id": ENDPOINT_ID,
                "query": "{ ping }",
                "headers": {"Authorization": "Bearer captured-token"},
            },
        )
        assert bearer.json()["auth_note"].startswith("Saved basic auth was not added")
        assert opener.calls[-1]["headers"]["Authorization"] == "Bearer captured-token"
        matched = client.post(
            "/api/graphql/execute",
            json={"url": "https://api.example/v2", "query": "{ ping }"},
        )
        assert "More than one saved endpoint" not in matched.json()["auth_note"]
        assert opener.calls[-1]["headers"]["Authorization"].startswith("Basic ")
    finally:
        client.__exit__(None, None, None)


def test_two_saved_hosts_do_not_guess_basic_auth(tmp_path: Path) -> None:
    client, opener = _client(tmp_path)
    try:
        _save(
            client,
            [
                _endpoint(),
                _endpoint(
                    id=OTHER_ID,
                    name="Second",
                    username="other-user",
                    password="other-secret",
                ),
            ],
        )
        sent = client.post(
            "/api/graphql/execute",
            json={"url": "https://api.example/graphql", "query": "{ ping }"},
        )
        assert sent.status_code == 200, sent.text
        assert "Authorization" not in opener.calls[-1]["headers"]
        assert "More than one saved endpoint" in sent.json()["auth_note"]
        assert "other-secret" not in sent.text
    finally:
        client.__exit__(None, None, None)


def _graphql_flow(flow_id: str, host: str, protocol: str, when: datetime) -> FlowRecord:
    return FlowRecord(
        id=flow_id,
        method="POST",
        host=host,
        path="/graphql",
        status=200,
        duration_ms=10,
        size_bytes=20,
        protocol=protocol,
        source=FlowSource.SDK,
        created_at=when,
        request_body=b'{"query":"{ ping }"}',
        content_type="application/json",
    )


def test_pasted_playground_url_stores_one_base(tmp_path: Path) -> None:
    client, _opener = _client(tmp_path)
    try:
        saved = _save(
            client,
            [
                {
                    "id": ENDPOINT_ID,
                    "url": "https://dirtvision.api.viewlift.com/playground",
                }
            ],
        )
        row = saved["endpoints"][0]
        assert row["base"] == "https://dirtvision.api.viewlift.com"
        assert row["url"] == "https://dirtvision.api.viewlift.com/graphql"
        assert row["playground_url"] == "https://dirtvision.api.viewlift.com/playground"
        assert row["name"] == "dirtvision.api.viewlift.com"
        bare = _save(
            client,
            [{"id": ENDPOINT_ID, "base": "myoutdoortv.staging.api.viewlift.com", "name": "MOTV"}],
        )
        motv = bare["endpoints"][0]
        assert motv["base"] == "https://myoutdoortv.staging.api.viewlift.com"
        assert motv["url"].endswith("/graphql")
        assert motv["playground_url"].endswith("/playground")
    finally:
        client.__exit__(None, None, None)


def test_bases_come_from_captured_graphql_hosts(tmp_path: Path) -> None:
    client, _opener = _client(tmp_path)
    try:
        now = datetime.now(UTC)
        store = client.app.state.store
        store.save_flow(_graphql_flow("old", "plain.example", "HTTP", now - timedelta(minutes=5)))
        store.save_flow(
            _graphql_flow("new", "dirtvision.api.viewlift.com", "HTTPS/2", now)
        )
        store.save_flow(
            FlowRecord(
                id="rest",
                method="GET",
                host="other.example",
                path="/login",
                status=200,
                duration_ms=1,
                size_bytes=1,
                protocol="HTTPS",
                source=FlowSource.PROXY,
                created_at=now,
            )
        )
        body = client.get("/api/graphql/bases").json()
        assert [item["base"] for item in body["bases"]] == [
            "https://dirtvision.api.viewlift.com",
            "http://plain.example",
        ]
        assert body["bases"][0]["count"] == 1
    finally:
        client.__exit__(None, None, None)


def test_gzip_response_is_json(tmp_path: Path) -> None:
    payload = gzip.compress(b'{"data":{"site":"dirtvision"}}')
    reply = _Reply(
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
        body=payload,
    )
    client, opener = _client(tmp_path, reply)
    try:
        sent = client.post(
            "/api/graphql/execute",
            json={
                "url": "https://dirtvision.api.viewlift.com/graphql",
                "query": "{ site }",
                "headers": {"Accept-Encoding": "gzip, deflate, br", "X-Site": "dirtvision"},
            },
        )
        assert sent.status_code == 200, sent.text
        assert json.loads(sent.json()["body"]) == {"data": {"site": "dirtvision"}}
        sent_headers = {key.lower(): value for key, value in opener.calls[-1]["headers"].items()}
        assert "accept-encoding" not in sent_headers
        assert sent_headers["x-site"] == "dirtvision"
    finally:
        client.__exit__(None, None, None)


def test_deflate_response_is_json(tmp_path: Path) -> None:
    payload = zlib.compress(b'{"data":{"ok":true}}')
    reply = _Reply(
        headers={"Content-Type": "application/json", "Content-Encoding": "deflate"},
        body=payload,
    )
    client, _opener = _client(tmp_path, reply)
    try:
        sent = client.post(
            "/api/graphql/execute",
            json={"url": "https://api.example/graphql", "query": "{ ok }"},
        )
        assert json.loads(sent.json()["body"])["data"]["ok"] is True
    finally:
        client.__exit__(None, None, None)


def test_html_response_points_at_the_graphql_path(tmp_path: Path) -> None:
    reply = _Reply(
        status=200,
        headers={"Content-Type": "text/html; charset=utf-8"},
        body=b"<!DOCTYPE html><html><body>playground</body></html>",
    )
    client, _opener = _client(tmp_path, reply)
    try:
        sent = client.post(
            "/api/graphql/execute",
            json={"url": "https://api.example/playground", "query": "{ ping }"},
        )
        body = sent.json()
        assert body["html_page"] is True
        assert "/graphql" in body["html_hint"]
        assert body["graphql_errors"] is False
    finally:
        client.__exit__(None, None, None)


def test_userinfo_url_is_rejected_without_echoing_the_secret(tmp_path: Path) -> None:
    client, opener = _client(tmp_path)
    try:
        rejected = client.post(
            "/api/graphql/execute",
            json={"url": "https://user:s3cret@api.example/graphql", "query": "{ ping }"},
        )
        assert rejected.status_code == 400
        assert "s3cret" not in rejected.text
        assert opener.calls == []
        plain = client.post(
            "/api/graphql/execute",
            content=b'{"url":"https://api.example/graphql","query":"{ping}"}',
            headers={"Content-Type": "text/plain"},
        )
        assert plain.status_code == 415
    finally:
        client.__exit__(None, None, None)


def test_schema_is_cached_until_refresh(tmp_path: Path) -> None:
    schema = {
        "data": {
            "__schema": {
                "queryType": {"name": "Query"},
                "mutationType": None,
                "types": [
                    {
                        "kind": "OBJECT",
                        "name": "Query",
                        "fields": [
                            {
                                "name": "ping",
                                "args": [],
                                "type": {"kind": "SCALAR", "name": "String"},
                            }
                        ],
                    }
                ],
            }
        }
    }
    reply = _Reply(body=json.dumps(schema).encode())
    client, opener = _client(tmp_path, reply)
    try:
        _save(client, [_endpoint()])
        first = client.post("/api/graphql/schema", json={"endpoint_id": ENDPOINT_ID})
        second = client.post("/api/graphql/schema", json={"endpoint_id": ENDPOINT_ID})
        assert first.status_code == 200, first.text
        assert first.json()["cached"] is False
        assert "__schema" in first.json()["body"]
        assert second.json()["cached"] is True
        assert len(opener.calls) == 1
        assert b"IntrospectionQuery" in opener.calls[0]["body"]
        refreshed = client.post(
            "/api/graphql/schema",
            json={"endpoint_id": ENDPOINT_ID, "refresh": True},
        )
        assert refreshed.json()["cached"] is False
        assert len(opener.calls) == 2
    finally:
        client.__exit__(None, None, None)


def test_redirect_handler_stays_on_the_original_host() -> None:
    handler = SameOriginRedirect("https://api.example/graphql")
    request = urlrequest.Request(
        "https://api.example/graphql",
        data=b'{"query":"{ping}"}',
        headers={"Authorization": "Basic abc"},
        method="POST",
    )
    with pytest.raises(GraphQLError, match="left the GraphQL host"):
        handler.redirect_request(
            request, None, 302, "Found", {}, "https://evil.example/graphql"
        )
    moved = handler.redirect_request(
        request, None, 302, "Found", {}, "https://api.example/v2"
    )
    assert moved is not None
    assert moved.get_method() == "POST"
    assert moved.data == b'{"query":"{ping}"}'
    assert moved.get_header("Authorization") == "Basic abc"


def test_remote_client_is_refused(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path))
    with TestClient(app, client=("192.0.2.8", 50000)) as remote:
        denied = remote.get("/api/graphql/endpoints")
        assert denied.status_code == 403
