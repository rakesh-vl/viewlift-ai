"""Host allowlist: default deny, exact and glob match."""

from __future__ import annotations

from viewlift_ai.proxy.allowlist import HostAllowlist


def test_default_deny_when_empty() -> None:
    allow = HostAllowlist()
    assert allow.allows("api.example.com") is False
    assert allow.allows("") is False
    assert allow.allows(None) is False


def test_exact_match_case_insensitive() -> None:
    allow = HostAllowlist(["API.Example.COM"])
    assert allow.allows("api.example.com") is True
    assert allow.allows("api.example.com.") is True
    assert allow.allows("other.example.com") is False


def test_glob_match() -> None:
    allow = HostAllowlist(["*.viewlift.com", "cdn.example.org"])
    assert allow.allows("api.viewlift.com") is True
    assert allow.allows("staging.api.viewlift.com") is True
    assert allow.allows("viewlift.com") is False
    assert allow.allows("cdn.example.org") is True
    assert allow.allows("evil.com") is False


def test_set_patterns_replaces() -> None:
    allow = HostAllowlist(["a.com"])
    assert allow.allows("a.com") is True
    allow.set_patterns(["b.com"])
    assert allow.allows("a.com") is False
    assert allow.allows("b.com") is True
