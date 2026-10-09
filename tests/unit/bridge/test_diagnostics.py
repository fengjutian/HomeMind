"""Bridge diagnostics summary (plan phase 16).

The load-bearing property: the summary is designed to be pasted into a public
issue, so it must not carry a credential, a token, or a query value out of the
process.
"""

from __future__ import annotations

from typing import Any

import pytest

from octop.infra.bridge.diagnostics import (
    BridgeDiagnostic,
    connection_diagnostic,
    path_only,
    redact_query,
    summarize,
)
from octop.infra.bridge.protocol import LOCAL_VERSION_STRING, Capability
from octop.infra.bridge.states import BridgeState
from octop.infra.db.repos.bridge_connections import BridgeConnectionRow


def _row(**kw: Any) -> BridgeConnectionRow:
    base: dict[str, Any] = {
        "pk": 1,
        "connection_id": "c1",
        "owner_user_id": 1,
        "peer_base_url": "http://peer:8080",
        "peer_username": "peer",
        "display_name": "Peer",
        "notes": None,
        "icon_name": None,
        "credential_blob": b"encrypted-password",
        "access_token_blob": b"encrypted-token",
        "token_expires_at": None,
        "status": "connected",
        "last_error": None,
        "last_seen_at": 1000,
        "auto_reconnect": True,
        "created_at": 0,
        "updated_at": 0,
        "state": BridgeState.ONLINE.value,
        "connected_since": 1_000,
    }
    base.update(kw)
    return BridgeConnectionRow(**base)


class TestQueryRedaction:
    def test_only_key_names_survive(self) -> None:
        assert redact_query("path=a%2Fb.txt&mime=text%2Fplain") == "path,mime"

    @pytest.mark.parametrize(
        "key", ["token", "access_token", "api_key", "secret", "password", "sig"]
    )
    def test_sensitive_values_are_marked_not_printed(self, key: str) -> None:
        out = redact_query(f"{key}=hunter2&page=2")
        assert "hunter2" not in out
        assert f"{key}=<redacted>" in out
        assert "page" in out

    def test_empty_query(self) -> None:
        assert redact_query("") == ""

    def test_unparseable_query_is_marked(self) -> None:
        assert redact_query("%%%%") == "<unparseable>"


class TestPathOnly:
    def test_drops_scheme_host_and_query(self) -> None:
        assert path_only("http://peer:8080/api/x?token=abc") == "/api/x"

    def test_a_bare_path_is_unchanged(self) -> None:
        assert path_only("/api/x") == "/api/x"


class TestConnectionDiagnostic:
    def test_reports_online_seconds(self) -> None:
        diag = connection_diagnostic(_row(), now=6_000)
        assert diag.state == "ONLINE"
        assert diag.online_seconds == 5_000

    def test_no_uptime_while_disconnected(self) -> None:
        diag = connection_diagnostic(
            _row(state=BridgeState.DISCONNECTED.value, status="disconnected"), now=6_000
        )
        assert diag.online_seconds is None

    def test_degraded_still_counts_as_live(self) -> None:
        """Degraded means up-but-suspect, so uptime keeps accruing."""
        diag = connection_diagnostic(_row(state=BridgeState.DEGRADED.value), now=6_000)
        assert diag.online_seconds == 5_000
        assert diag.retry_may_help is True

    def test_flags_what_needs_a_human(self) -> None:
        diag = connection_diagnostic(
            _row(state=BridgeState.REAUTH_REQUIRED.value, status="error"), now=6_000
        )
        assert diag.retry_may_help is False

    def test_never_carries_the_credential(self) -> None:
        blob = str(connection_diagnostic(_row(), now=6_000).as_dict())
        assert "encrypted-password" not in blob
        assert "encrypted-token" not in blob


class TestSummary:
    def _two(self) -> list[BridgeDiagnostic]:
        return [
            connection_diagnostic(_row(), now=6_000),
            connection_diagnostic(
                _row(
                    connection_id="c2",
                    display_name="Other",
                    state=BridgeState.REAUTH_REQUIRED.value,
                    status="error",
                ),
                now=6_000,
            ),
        ]

    def test_counts_online_and_needs_human(self) -> None:
        out = summarize(self._two())
        assert out["connections_total"] == 2
        assert out["connections_online"] == 1
        assert out["connections_needing_human"] == ["c2"]

    def test_reports_local_protocol_and_capabilities(self) -> None:
        out = summarize(
            [], local_instance_id="inst-a", local_capabilities=frozenset({Capability.CANCEL})
        )
        assert out["local_protocol"] == LOCAL_VERSION_STRING
        assert out["local_capabilities"] == ["cancel"]

    def test_only_bridge_counters_are_included(self) -> None:
        out = summarize([])
        assert out["metrics"]
        assert all(k.startswith("bridge_") for k in out["metrics"])

    def test_summary_is_json_safe(self) -> None:
        import json

        json.dumps(summarize(self._two()))
