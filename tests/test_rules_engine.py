"""Rule engine: path jail, map remote scheme, body cap, order, defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from viewlift_ai.models.rules import (
    RequestMatchContext,
    RewriteAction,
    RewriteActionKind,
    RewriteRule,
    RuleMatch,
    RulesConfig,
)
from viewlift_ai.services.rule_engine import (
    MAX_LATENCY_MS,
    MAX_REPLACEMENT_CHARS,
    MAX_RESULT_BODY_BYTES,
    PathJailError,
    RuleEngine,
    RuleValidationError,
    apply_rewrite_actions,
    build_map_remote_url,
    clamp_latency_ms,
    resolve_map_local_path,
    validate_http_url,
)
from viewlift_ai.services.rule_store import RuleConfigStore


def _engine(tmp_path: Path) -> RuleEngine:
    return RuleEngine(RuleConfigStore(tmp_path / "rules.json"))


def test_defaults_empty_and_mutating_off(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cfg = engine.config
    assert cfg.allowlist == []
    assert cfg.map_local == []
    assert cfg.map_remote == []
    assert cfg.rewrite == []
    assert cfg.throttle == []
    assert cfg.breakpoints == []
    assert engine.any_mutating_enabled() is False


def test_path_jail_rejects_absolute_and_traversal(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "ok.json").write_text("{}", encoding="utf-8")

    assert resolve_map_local_path(root, "ok.json").name == "ok.json"

    with pytest.raises(PathJailError):
        resolve_map_local_path(root, "/etc/passwd")
    with pytest.raises(PathJailError):
        resolve_map_local_path(root, "../outside.txt")
    with pytest.raises(PathJailError):
        resolve_map_local_path(root, "a/../../outside.txt")
    with pytest.raises(PathJailError):
        resolve_map_local_path(root, "")


def test_path_jail_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    link = root / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink not permitted")
    with pytest.raises(PathJailError):
        resolve_map_local_path(root, "escape")


def test_map_local_missing_file_404(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    engine = _engine(tmp_path)
    engine.set_map_local_root(str(root))
    rule = engine.add_map_local(
        {
            "enabled": True,
            "match": {"method": "GET", "host": "api.test", "path": "/x"},
            "relative_path": "missing.json",
        }
    )
    assert rule.enabled is True
    result = engine.evaluate_map_local(
        RequestMatchContext(method="GET", host="api.test", path="/x")
    )
    assert result is not None
    assert result.status == 404
    assert result.missing is True
    assert result.body == b"Not Found"


def test_map_remote_scheme_rejection() -> None:
    with pytest.raises(RuleValidationError):
        validate_http_url("ftp://files.example/a")
    with pytest.raises(RuleValidationError):
        validate_http_url("file:///tmp/x")
    with pytest.raises(RuleValidationError):
        validate_http_url("javascript:alert(1)")
    with pytest.raises(RuleValidationError):
        validate_http_url("https://user:pass@evil.com/")
    assert validate_http_url("https://ok.example/path").startswith("https://")


def test_map_remote_build_url() -> None:
    url = build_map_remote_url(
        original_url="https://prod.example/v1/items?q=1",
        target_url="https://staging.example/",
        preserve_path=True,
        preserve_query=True,
    )
    assert url == "https://staging.example/v1/items?q=1"

    with pytest.raises(RuleValidationError):
        build_map_remote_url(
            original_url="https://a/x",
            target_url="gopher://x",
            preserve_path=True,
            preserve_query=True,
        )


def test_rewrite_body_cap() -> None:
    huge = "x" * (MAX_REPLACEMENT_CHARS + 1)
    result = apply_rewrite_actions(
        status=200,
        headers={},
        body=b"hello",
        actions=[
            RewriteAction(
                kind=RewriteActionKind.REPLACE_BODY_LITERAL,
                find="hello",
                replace=huge,
            )
        ],
    )
    assert result.error == "replacement exceeds size cap"
    assert result.body == b"hello"

    # Result body cap: near-full body + expansion past MAX_RESULT_BODY_BYTES
    body = (b"a" * (MAX_RESULT_BODY_BYTES - 10)) + b"TOKEN"
    result2 = apply_rewrite_actions(
        status=200,
        headers={},
        body=body,
        actions=[
            RewriteAction(
                kind=RewriteActionKind.REPLACE_BODY_LITERAL,
                find="TOKEN",
                replace="Y" * 20,
            )
        ],
    )
    assert result2.error == "result body exceeds size cap"


def test_rewrite_regex_safe_error() -> None:
    result = apply_rewrite_actions(
        status=200,
        headers={},
        body=b"abc",
        actions=[
            RewriteAction(
                kind=RewriteActionKind.REPLACE_BODY_REGEX,
                find="(",
                replace="x",
            )
        ],
    )
    assert result.error is not None
    assert "invalid regex" in result.error
    assert result.body == b"abc"


def test_ordered_execution(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.replace(
        RulesConfig(
            rewrite=[
                RewriteRule(
                    id="r2",
                    enabled=True,
                    order=2,
                    match=RuleMatch(method="*", host="*", path="*"),
                    actions=[
                        RewriteAction(kind=RewriteActionKind.SET_STATUS, status=201),
                    ],
                ),
                RewriteRule(
                    id="r1",
                    enabled=True,
                    order=1,
                    match=RuleMatch(method="*", host="*", path="*"),
                    actions=[
                        RewriteAction(
                            kind=RewriteActionKind.SET_HEADER,
                            header_name="X-Order",
                            header_value="first",
                        ),
                    ],
                ),
            ]
        )
    )
    out = engine.evaluate_response_rewrite(
        RequestMatchContext(method="GET", host="h", path="/"),
        status=200,
        headers={},
        body=b"",
    )
    assert out.headers["X-Order"] == "first"
    assert out.status == 201


def test_disabled_rules_skipped(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    engine.add_map_remote(
        {
            "enabled": False,
            "match": {"method": "*", "host": "a.com", "path": "*"},
            "target_url": "https://b.com/",
        }
    )
    assert (
        engine.evaluate_map_remote(
            RequestMatchContext(method="GET", host="a.com", path="/"),
            original_url="https://a.com/",
        )
        is None
    )


def test_throttle_caps(tmp_path: Path) -> None:
    assert clamp_latency_ms(999_999) == MAX_LATENCY_MS
    engine = _engine(tmp_path)
    rule = engine.add_throttle(
        {
            "enabled": True,
            "host": "*",
            "latency_ms": 999_999,
            "upload_kbps": 9_999_999,
            "offline": False,
        }
    )
    assert rule.latency_ms == MAX_LATENCY_MS
    effect = engine.evaluate_throttle("any.host")
    assert effect.latency_ms == MAX_LATENCY_MS
    assert effect.upload_kbps is not None and effect.upload_kbps <= 100_000


def test_atomic_persist_mode_0600(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    store = RuleConfigStore(path)
    engine = RuleEngine(store)
    engine.set_allowlist(["a.com"])
    assert path.is_file()
    mode = stat_mode(path)
    assert mode & 0o777 == 0o600
    reloaded = RuleConfigStore(path).load()
    assert reloaded.allowlist == ["a.com"]


def stat_mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_map_local_serves_file(tmp_path: Path) -> None:
    root = tmp_path / "fixtures"
    root.mkdir()
    (root / "body.json").write_text('{"ok":true}', encoding="utf-8")
    engine = _engine(tmp_path)
    engine.set_map_local_root(str(root))
    engine.add_map_local(
        {
            "enabled": True,
            "match": {"method": "GET", "host": "cdn.test", "path": "/body.json"},
            "relative_path": "body.json",
        }
    )
    result = engine.evaluate_map_local(
        RequestMatchContext(method="GET", host="cdn.test", path="/body.json")
    )
    assert result is not None
    assert result.status == 200
    assert b'"ok":true' in result.body
