"""Best-effort LAN address discovery for the dashboard login QR code.

The login page is reachable at ``localhost`` on the host machine, but that
address is useless when scanned by a phone on the same LAN: ``localhost``
resolves to the phone itself. This module finds a routable IPv4 address so the
login QR code encodes something a phone can actually open.

Discovery strategy (first hit wins):

1. A UDP socket "connect" to a public address — this makes the OS pick the
   outbound interface without sending any packet. No traffic is emitted.
2. ``socket.gethostbyname(socket.gethostname())`` as a fallback for hosts
   whose resolver cannot route a UDP socket.
3. Loopback as the last resort, so callers always get a usable value.

Every step is wrapped: on a host with no routable IPv4 (air-gapped, IPv6-only)
this returns ``127.0.0.1`` rather than raising, because the QR code is a
convenience surface and must never break the login page.
"""

from __future__ import annotations

import ipaddress
import socket

_LOOPBACK = "127.0.0.1"


def _is_usable_lan_ip(candidate: str) -> bool:
    """True when ``candidate`` is a private/globally-routable IPv4 address.

    Loopback, link-local (169.254.x.x, DHCP APIPA), and unspecified addresses
    are rejected because a phone on the LAN cannot use them.
    """
    try:
        addr = ipaddress.IPv4Address(candidate)
    except ValueError:
        return False
    return not (addr.is_loopback or addr.is_link_local or addr.is_unspecified)


def _via_udp_probe() -> str | None:
    """Ask the OS which outbound IPv4 interface it would use.

    ``connect`` on a UDP socket sends nothing; it only binds the local address
    for that destination, which is exactly the routing information we need.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 192.0.2.0/24 is TEST-NET-1 (RFC 5737) — reserved for documentation
        # and never routed, so this cannot leak traffic to a real host.
        sock.connect(("192.0.2.1", 9))
        local = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return local if _is_usable_lan_ip(local) else None


def _via_hostname() -> str | None:
    """Resolve the machine hostname to an IPv4 address."""
    try:
        resolved = socket.gethostbyname(socket.gethostname())
    except OSError:
        return None
    return resolved if _is_usable_lan_ip(resolved) else None


def detect_lan_ipv4() -> str:
    """Return this host's LAN-reachable IPv4 address, or ``127.0.0.1``.

    Never raises — callers use the result for a convenience QR code.
    """
    for probe in (_via_udp_probe, _via_hostname):
        found = probe()
        if found:
            return found
    return _LOOPBACK


def build_lan_origin(port: int, *, https: bool = False) -> str:
    """Build ``http://<lan-ip>:<port>`` for QR-code embedding.

    IPv4 addresses never need bracket wrapping (unlike IPv6 literals).
    """
    scheme = "https" if https else "http"
    return f"{scheme}://{detect_lan_ipv4()}:{port}"
