"""Header and nested JSON redaction."""

from __future__ import annotations

import json

from viewlift_ai.proxy.redact import REDACTED, RedactionService


def test_redacts_sensitive_headers_case_insensitive() -> None:
    svc = RedactionService()
    out = svc.redact_headers(
        {
            "Authorization": "Bearer secret",
            "COOKIE": "sid=1",
            "Set-Cookie": "sid=1",
            "Content-Type": "application/json",
        }
    )
    assert out["Authorization"] == "Bearer secret"
    assert out["COOKIE"] == REDACTED
    assert out["Set-Cookie"] == REDACTED
    assert out["Content-Type"] == "application/json"


def test_redacts_nested_json_keys_case_insensitive() -> None:
    svc = RedactionService()
    body = {
        "user": "a",
        "Password": "p",
        "nested": {
            "TOKEN": "t",
            "refresh_Token": "r",
            "access_token": "a",
            "api_Key": "k",
            "Secret": "s",
            "AUTHORIZATION": "h",
            "keep": "ok",
        },
        "list": [{"token": "x"}, {"safe": 1}],
    }
    redacted = svc.redact_json_value(body)
    assert redacted["Password"] == REDACTED
    nested = redacted["nested"]
    assert nested["TOKEN"] == "t"
    assert nested["refresh_Token"] == "r"
    assert nested["access_token"] == "a"
    assert nested["api_Key"] == "k"
    assert nested["Secret"] == REDACTED
    assert nested["AUTHORIZATION"] == "h"
    assert nested["keep"] == "ok"
    assert redacted["list"][0]["token"] == "x"
    assert redacted["list"][1]["safe"] == 1


def test_redact_body_json_bytes() -> None:
    svc = RedactionService()
    raw = json.dumps({"password": "abc", "token": "keep", "n": 1}).encode()
    out = svc.redact_body(raw)
    parsed = json.loads(out)
    assert parsed["password"] == REDACTED
    assert parsed["token"] == "keep"
    assert parsed["n"] == 1


def test_redact_body_non_json_unchanged() -> None:
    svc = RedactionService()
    raw = b"not-json token=abc"
    assert svc.redact_body(raw) == raw
