"""Bridge forwarding for resumable upload sessions (plan phase 4).

The session state machine must live on the instance that will hold the file, so
the whole session is forwarded through the tunnel instead of being proxied part
by part from the hub.
"""

from __future__ import annotations

import pytest

from octop.api.middleware.bridge_proxy import (
    _is_header_tunneled_path,
    resolve_tunnel_target,
)
from octop.infra.bridge.ids import format_bridge_agent_id
from octop.infra.bridge.tunnel_policy import is_tunnel_path_allowed

CONN = "01HQCONN"
REMOTE = "agremote"
SHADOW = format_bridge_agent_id(CONN, REMOTE)


class TestTunnelPolicy:
    @pytest.mark.parametrize(
        "method,path",
        [
            ("POST", "/api/uploads/sessions"),
            ("GET", "/api/uploads/sessions/01HQ0000000000000000000001"),
            ("PUT", "/api/uploads/sessions/01HQ0000000000000000000001/parts/1"),
            ("POST", "/api/uploads/sessions/01HQ0000000000000000000001/complete"),
            ("DELETE", "/api/uploads/sessions/01HQ0000000000000000000001"),
            ("GET", "/api/uploads/blobs/01HQ0000000000000000000001"),
        ],
    )
    def test_upload_session_routes_are_allowed(self, method: str, path: str) -> None:
        assert is_tunnel_path_allowed(method, path) is True

    @pytest.mark.parametrize(
        "method,path",
        [
            # No write verbs on blob download.
            ("POST", "/api/uploads/blobs/abc"),
            ("DELETE", "/api/uploads/blobs/abc"),
            ("PUT", "/api/uploads/blobs/abc"),
            # No nested path smuggling under a session id.
            ("PUT", "/api/uploads/sessions/abc/parts/1/extra"),
            ("POST", "/api/uploads/sessions/abc/complete/extra"),
            # Adjacent, unrelated upload surface stays denied.
            ("GET", "/api/uploads/sessions-extra"),
            ("POST", "/api/uploads/other"),
            ("GET", "/api/uploads/blobs"),
            ("GET", "/api/uploads/blobs/abc/extra"),
        ],
    )
    def test_everything_else_stays_denied(self, method: str, path: str) -> None:
        assert is_tunnel_path_allowed(method, path) is False


class TestHeaderRouting:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/uploads/sessions",
            "/api/uploads/sessions/abc",
            "/api/uploads/sessions/abc/parts/2",
            "/api/uploads/blobs/abc",
        ],
    )
    def test_upload_paths_accept_header_routing(self, path: str) -> None:
        assert _is_header_tunneled_path(path) is True

    def test_unrelated_paths_still_reject_header_routing(self) -> None:
        assert _is_header_tunneled_path("/api/agents") is False
        assert _is_header_tunneled_path("/api/uploads/internal") is False

    def test_shadow_agent_header_selects_the_hop(self) -> None:
        target = resolve_tunnel_target("/api/uploads/sessions", agent_header=SHADOW)
        assert target is not None
        assert target.remote_path == "/api/uploads/sessions"
        assert target.ref.connection_id == CONN
        assert target.ref.remote_agent_id == REMOTE

    def test_local_request_is_not_tunnelled(self) -> None:
        assert resolve_tunnel_target("/api/uploads/sessions") is None
        assert resolve_tunnel_target("/api/uploads/sessions", agent_header="ag-local") is None
