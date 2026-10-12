"""Unit tests for the LAN address helper and the login QR-code endpoint."""

from __future__ import annotations

import ipaddress
from pathlib import Path

import httpx
import pytest
from tests.support.app import ensure_control_plane_bound
from tests.support.auth import bootstrap_admin

from octop.api.app import build_app
from octop.api.deps import is_jwt_exempt_path
from octop.infra.server import OctopServer
from octop.infra.utils import lan_address


def test_exempt_path_covers_server_address() -> None:
    assert is_jwt_exempt_path("/api/settings/server-address")
    # Sibling settings routes stay authenticated.
    assert not is_jwt_exempt_path("/api/settings/timezone")
    assert not is_jwt_exempt_path("/api/settings/server-address/../timezone")


def test_usable_lan_ip_rejects_unreachable_addresses() -> None:
    assert lan_address._is_usable_lan_ip("192.168.1.23")
    assert lan_address._is_usable_lan_ip("10.0.0.5")
    assert not lan_address._is_usable_lan_ip("127.0.0.1")
    assert not lan_address._is_usable_lan_ip("169.254.10.1")  # APIPA / link-local
    assert not lan_address._is_usable_lan_ip("0.0.0.0")
    assert not lan_address._is_usable_lan_ip("not-an-ip")
    assert not lan_address._is_usable_lan_ip("::1")


def test_detect_lan_ipv4_returns_ip_or_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_address, "_via_udp_probe", lambda: "192.168.1.23")
    monkeypatch.setattr(lan_address, "_via_hostname", lambda: "10.0.0.5")
    assert lan_address.detect_lan_ipv4() == "192.168.1.23"


def test_detect_lan_ipv4_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lan_address, "_via_udp_probe", lambda: None)
    monkeypatch.setattr(lan_address, "_via_hostname", lambda: "10.0.0.5")
    assert lan_address.detect_lan_ipv4() == "10.0.0.5"


def test_detect_lan_ipv4_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An air-gapped host must still produce a usable value."""
    monkeypatch.setattr(lan_address, "_via_udp_probe", lambda: None)
    monkeypatch.setattr(lan_address, "_via_hostname", lambda: None)
    assert lan_address.detect_lan_ipv4() == "127.0.0.1"


def test_build_lan_origin_formats_url() -> None:
    origin = lan_address.build_lan_origin(8088, https=True)
    assert origin.startswith("https://")
    host = origin.removeprefix("https://").split(":")[0]
    # Loopback is an acceptable outcome on an isolated CI machine.
    ipaddress.IPv4Address(host)


@pytest.fixture
async def client(tmp_path: Path):
    srv = OctopServer(home=tmp_path)
    await srv.start()
    await ensure_control_plane_bound(srv)
    app = build_app(srv)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        # The login page is unreachable until setup completes, so create a user.
        await bootstrap_admin(c, tmp_path)
        yield c, srv
    await srv.stop()


async def test_server_address_requires_no_auth(client) -> None:
    """The login page calls this before any token exists."""
    c, srv = client
    res = await c.get("/api/settings/server-address")
    assert res.status_code == 200
    body = res.json()
    ipaddress.IPv4Address(body["host"])
    assert body["url"] == f"http://{body['host']}:{body['port']}"
    assert body["port"] == srv.services.config.port
    assert isinstance(body["is_lan"], bool)
