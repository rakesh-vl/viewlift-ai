"""LAN bind IP policy: private unicast present on an active interface."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Sequence


class SetupValidationError(ValueError):
    """Invalid IP/port/bind request from Setup or config."""


InterfaceLister = Callable[[], list[dict[str, str]]]


def validate_ip(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        msg = "IP address is required"
        raise SetupValidationError(msg)
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError as exc:
        msg = f"Invalid IP address: {raw}"
        raise SetupValidationError(msg) from exc
    if addr.version != 4:
        msg = "Only IPv4 addresses are supported for proxy bind"
        raise SetupValidationError(msg)
    return str(addr)


def validate_port(value: int | str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        msg = "Port must be an integer"
        raise SetupValidationError(msg) from exc
    if port < 1 or port > 65535:
        msg = "Port must be between 1 and 65535"
        raise SetupValidationError(msg)
    return port


def is_loopback_ip(value: str) -> bool:
    return ipaddress.ip_address(value).is_loopback


_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_VPN_PREFIXES = ("utun", "tailscale", "wt", "nebula", "ham")


def interface_scope(iface: str, ip: str) -> str:
    """loopback, link-local, vpn (NetBird/Tailscale/utun), lan, or public."""
    addr = ipaddress.ip_address(ip)
    if not isinstance(addr, ipaddress.IPv4Address):
        return "public"
    if addr.is_loopback:
        return "loopback"
    if addr.is_link_local:
        return "link-local"
    if iface.lower().startswith(_VPN_PREFIXES) or addr in _CGNAT:
        return "vpn"
    if addr.is_private:
        return "lan"
    return "public"


def validate_receiver_bind_ip(
    host: str,
    *,
    interfaces: Sequence[dict[str, str]] | None = None,
    interface_lister: InterfaceLister | None = None,
) -> str:
    """Accept localhost, a private LAN address, or a VPN address present on this Mac."""
    ip = validate_ip(host)
    addr = ipaddress.ip_address(ip)
    if addr.is_unspecified:
        msg = "Receiver bind must not use 0.0.0.0"
        raise SetupValidationError(msg)
    if addr.is_multicast:
        msg = "Receiver bind must not use a multicast address"
        raise SetupValidationError(msg)
    if addr.is_loopback:
        return ip
    if interfaces is not None:
        rows = list(interfaces)
    elif interface_lister is not None:
        rows = interface_lister()
    else:
        from viewlift_ai.services.setup import list_ipv4_interfaces

        rows = list_ipv4_interfaces()
    match = next((row for row in rows if str(row.get("ip") or "") == ip), None)
    if match is None:
        msg = f"IP {ip} is not on an active network interface"
        raise SetupValidationError(msg)
    scope = interface_scope(str(match.get("iface") or ""), ip)
    if scope not in {"lan", "vpn"}:
        msg = f"IP {ip} is {scope}. Choose localhost, Wi-Fi/Ethernet, or a VPN address."
        raise SetupValidationError(msg)
    return ip


def preferred_lan_host(interfaces: Sequence[dict[str, str]]) -> str | None:
    """Current Wi-Fi/Ethernet address. Skips loopback and link-local."""
    candidates: list[tuple[int, str, str]] = []
    for row in interfaces:
        ip = str(row.get("ip") or "").strip()
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if addr.version != 4 or addr.is_loopback or addr.is_link_local or not addr.is_private:
            continue
        iface = str(row.get("iface") or "")
        rank = 0 if iface == "en0" else 1
        candidates.append((rank, iface, ip))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def validate_loopback_or_refuse_env(host: str) -> str:
    """Startup proxy host must be loopback. LAN bind is Setup-only."""
    ip = validate_ip(host)
    if not is_loopback_ip(ip):
        msg = (
            f"VIEWLIFT_PROXY_HOST={ip!r} is not loopback. "
            "Fail-closed: start on 127.0.0.1 and enable LAN bind from Setup "
            "(session basic-auth is minted there)."
        )
        raise SetupValidationError(msg)
    return ip


def validate_lan_bind_ip(
    host: str,
    *,
    interfaces: Sequence[dict[str, str]] | None = None,
    interface_lister: InterfaceLister | None = None,
) -> str:
    """
    Accept only a private unicast IPv4 that appears on an active interface.

    Rejects unspecified (0.0.0.0), multicast, loopback, and non-private addresses.
    """
    ip = validate_ip(host)
    addr = ipaddress.ip_address(ip)
    if addr.is_unspecified:
        msg = "LAN bind must not use the unspecified address 0.0.0.0"
        raise SetupValidationError(msg)
    if addr.is_multicast:
        msg = "LAN bind must not use a multicast address"
        raise SetupValidationError(msg)
    if addr.is_loopback:
        msg = "LAN bind requires a non-loopback interface address"
        raise SetupValidationError(msg)
    if not addr.is_private:
        msg = "LAN bind requires a private IPv4 address on a local interface"
        raise SetupValidationError(msg)

    if interfaces is not None:
        rows = list(interfaces)
    elif interface_lister is not None:
        rows = interface_lister()
    else:
        from viewlift_ai.services.setup import list_ipv4_interfaces

        rows = list_ipv4_interfaces()

    listed = {
        str(row.get("ip") or "").strip()
        for row in rows
        if str(row.get("ip") or "").strip()
        and not is_loopback_ip(str(row.get("ip") or "127.0.0.1"))
    }
    if ip not in listed:
        msg = f"IP {ip} is not on an active network interface"
        raise SetupValidationError(msg)
    return ip
