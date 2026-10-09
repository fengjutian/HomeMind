"""Tunnel header hardening (plan phase 15).

The rule being enforced: a tunneled request is executed as the connection
owner, so nothing in the initiator's headers may assert an identity, a
credential, or an address the peer never observed.
"""

from __future__ import annotations

import pytest

from octop.infra.bridge.http_tunnel import (
    sanitize_request_headers,
    sanitize_response_headers,
)


class TestRequestHeaders:
    def test_keeps_ordinary_content_headers(self) -> None:
        out = sanitize_request_headers(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        assert out == {"content-type": "application/json", "accept": "application/json"}

    def test_drops_credentials(self) -> None:
        out = sanitize_request_headers(
            {
                "Authorization": "Bearer attacker-token",
                "Cookie": "session=abc",
                "X-Api-Key": "k",
                "X-Auth-Token": "t",
            }
        )
        assert out == {}

    def test_drops_forwarded_metadata_so_the_peer_cannot_be_fooled(self) -> None:
        """A forged client IP would make the peer's audit log record a lie."""
        out = sanitize_request_headers(
            {
                "X-Forwarded-For": "10.0.0.1",
                "X-Forwarded-Host": "internal",
                "X-Forwarded-Proto": "https",
                "X-Real-IP": "10.0.0.2",
                "Forwarded": "for=10.0.0.3",
                "Via": "1.1 somewhere",
            }
        )
        assert out == {}

    def test_drops_hop_by_hop(self) -> None:
        out = sanitize_request_headers(
            {
                "Connection": "keep-alive",
                "Transfer-Encoding": "chunked",
                "Upgrade": "websocket",
                "TE": "trailers",
                "Keep-Alive": "timeout=5",
            }
        )
        assert out == {}

    def test_unknown_headers_are_dropped_by_default(self) -> None:
        """A denylist would forward anything nobody thought of."""
        out = sanitize_request_headers({"X-Something-Only-The-Peer-Invented": "1", "X-Debug": "2"})
        assert out == {}

    def test_output_keys_are_lowercased(self) -> None:
        assert list(sanitize_request_headers({"Accept-Language": "zh"})) == ["accept-language"]

    def test_the_upload_headers_we_relied_on_survive(self) -> None:
        """Resumable uploads send these; the allow-list must not break them."""
        out = sanitize_request_headers(
            {
                "X-Chunk-SHA256": "a" * 64,
                "X-Octop-Agent-Id": "bridge:c1:ag1",
                "Content-Type": "application/octet-stream",
                "Content-Length": "1234",
            }
        )
        assert out["x-chunk-sha256"] == "a" * 64
        assert out["x-octop-agent-id"] == "bridge:c1:ag1"
        assert out["content-type"] == "application/octet-stream"
        assert "content-length" not in out, "the transport sets it"

    def test_empty_input(self) -> None:
        assert sanitize_request_headers({}) == {}


class TestResponseHeaders:
    def test_keeps_type_and_range_metadata(self) -> None:
        out = sanitize_response_headers(
            {"content-type": "application/json", "content-range": "bytes 0-1/2"}
        )
        assert out == {"content-type": "application/json", "content-range": "bytes 0-1/2"}

    def test_never_returns_a_set_cookie(self) -> None:
        """A peer must not be able to plant a session in the owner's browser."""
        out = sanitize_response_headers({"Set-Cookie": "session=pwned; Path=/"})
        assert out == {}

    def test_drops_content_length_and_encoding(self) -> None:
        out = sanitize_response_headers({"content-length": "999", "content-encoding": "gzip"})
        assert out == {}

    def test_location_is_kept_for_redirects(self) -> None:
        assert sanitize_response_headers({"Location": "/api/x"}) == {"location": "/api/x"}


class TestNoHeaderIsBothAllowedAndDropped:
    @pytest.mark.parametrize(
        "name",
        ["authorization", "cookie", "x-forwarded-for", "connection", "set-cookie"],
    )
    def test_named_threat_is_never_forwarded(self, name: str) -> None:
        assert name not in sanitize_request_headers({name: "value"})
        assert name not in sanitize_response_headers({name: "value"})
