"""LAN bind policy: private interface IPs only."""

from __future__ import annotations

import pytest

from viewlift_ai.services.bind_policy import (
    SetupValidationError,
    interface_scope,
    preferred_lan_host,
    validate_lan_bind_ip,
    validate_loopback_or_refuse_env,
    validate_receiver_bind_ip,
)

IFACES = [
    {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
    {"iface": "en0", "ip": "192.168.1.20", "scope": "lan"},
]


def test_refuse_env_non_loopback() -> None:
    with pytest.raises(SetupValidationError, match="not loopback"):
        validate_loopback_or_refuse_env("192.168.1.20")
    assert validate_loopback_or_refuse_env("127.0.0.1") == "127.0.0.1"


def test_reject_unspecified_multicast_public_and_missing() -> None:
    with pytest.raises(SetupValidationError, match="unspecified"):
        validate_lan_bind_ip("0.0.0.0", interfaces=IFACES)
    with pytest.raises(SetupValidationError, match="multicast"):
        validate_lan_bind_ip("224.0.0.1", interfaces=IFACES)
    with pytest.raises(SetupValidationError, match="private"):
        validate_lan_bind_ip("8.8.8.8", interfaces=IFACES)
    with pytest.raises(SetupValidationError, match="not on an active"):
        validate_lan_bind_ip("192.168.1.99", interfaces=IFACES)
    assert validate_lan_bind_ip("192.168.1.20", interfaces=IFACES) == "192.168.1.20"


def test_receiver_bind_accepts_tailscale_and_rejects_public() -> None:
    interfaces = [
        {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
        {"iface": "utun4", "ip": "100.100.20.5", "scope": "vpn"},
        {"iface": "en0", "ip": "8.8.8.8", "scope": "public"},
    ]
    assert interface_scope("utun4", "100.100.20.5") == "vpn"
    assert validate_receiver_bind_ip("100.100.20.5", interfaces=interfaces) == "100.100.20.5"
    assert validate_receiver_bind_ip("127.0.0.1", interfaces=interfaces) == "127.0.0.1"
    with pytest.raises(SetupValidationError, match="public"):
        validate_receiver_bind_ip("8.8.8.8", interfaces=interfaces)


def test_preferred_lan_host_follows_wifi_and_skips_link_local() -> None:
    interfaces = [
        {"iface": "lo0", "ip": "127.0.0.1", "scope": "loopback"},
        {"iface": "en10", "ip": "169.254.140.52", "scope": "lan"},
        {"iface": "en0", "ip": "10.117.22.7", "scope": "lan"},
    ]
    assert preferred_lan_host(interfaces) == "10.117.22.7"
    assert preferred_lan_host([interfaces[0], interfaces[1]]) is None


def test_injected_interface_lister() -> None:
    def lister() -> list[dict[str, str]]:
        return [{"iface": "en1", "ip": "10.0.0.5", "scope": "lan"}]

    assert validate_lan_bind_ip("10.0.0.5", interface_lister=lister) == "10.0.0.5"
