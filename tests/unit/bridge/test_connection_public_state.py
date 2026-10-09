"""Bridge state machine reaches the API surface (plan phase 13).

The legacy ``status`` field is a frozen compatibility contract and must not
change. These tests assert the new fields are *added* alongside it.
"""

from __future__ import annotations

from typing import Any

from octop.infra.bridge.manager import BridgeManager
from octop.infra.bridge.states import BridgeState
from octop.infra.db.repos.bridge_connections import BridgeConnectionRow


def _row(**kw: Any) -> BridgeConnectionRow:
    base: dict[str, Any] = {
        "pk": 1,
        "connection_id": "cid1",
        "owner_user_id": 1,
        "peer_base_url": "http://peer:8080",
        "peer_username": "peer",
        "display_name": "Peer",
        "notes": None,
        "icon_name": None,
        "credential_blob": None,
        "access_token_blob": None,
        "token_expires_at": None,
        "status": "disconnected",
        "last_error": None,
        "last_seen_at": None,
        "auto_reconnect": True,
        "created_at": 0,
        "updated_at": 0,
    }
    base.update(kw)
    return BridgeConnectionRow(**base)


def _manager() -> BridgeManager:
    return BridgeManager.__new__(BridgeManager)


def _public(mgr: BridgeManager, row: BridgeConnectionRow) -> dict[str, Any]:
    mgr._sessions = {}
    return mgr.connection_public(row)


class TestFrozenContract:
    def test_legacy_fields_are_untouched(self) -> None:
        mgr = _manager()
        body = _public(mgr, _row())
        for key in (
            "connection_id",
            "peer_base_url",
            "peer_username",
            "display_name",
            "notes",
            "icon_name",
            "status",
            "last_error",
            "last_seen_at",
            "auto_reconnect",
            "created_at",
            "updated_at",
            "has_password",
            "inbound",
        ):
            assert key in body, key
        assert body["status"] == "disconnected"

    def test_no_field_was_removed(self) -> None:
        """A superset, never a rewrite — older dashboards keep working."""
        mgr = _manager()
        body = _public(mgr, _row())
        assert len(body) >= 14
        assert set(body) >= {
            "status",
            "last_error",
            "auto_reconnect",
            "has_password",
            "inbound",
        }


class TestStateFields:
    def test_state_is_exposed(self) -> None:
        body = _public(_manager(), _row(state="REAUTH_REQUIRED"))
        assert body["state"] == "REAUTH_REQUIRED"
        assert body["retry_may_help"] is False

    def test_degraded_reports_as_recoverable(self) -> None:
        body = _public(_manager(), _row(state="DEGRADED", status="error"))
        assert body["state"] == "DEGRADED"
        assert body["retry_may_help"] is True

    def test_a_stale_online_row_is_reported_as_disconnected(self) -> None:
        """The row says ONLINE but no socket exists — do not report a live link."""
        body = _public(_manager(), _row(state="ONLINE", status="connected"))
        assert body["state"] == "DISCONNECTED"
        assert body["status"] == "disconnected"

    def test_a_live_socket_overrides_a_stale_row(self) -> None:
        class _Live:
            closed = False

        mgr = _manager()
        mgr._sessions = {"cid1": _Live()}  # type: ignore[dict-item]
        body = mgr.connection_public(_row(state="DISCONNECTED", status="disconnected"))
        assert body["state"] == "ONLINE"
        assert body["status"] == "connected"

    def test_a_closed_socket_does_not_override_the_row(self) -> None:
        class _Closed:
            closed = True

        mgr = _manager()
        mgr._sessions = {"cid1": _Closed()}  # type: ignore[dict-item]
        body = mgr.connection_public(_row(state="DEGRADED", status="error"))
        assert body["state"] == "DEGRADED"

    def test_unreadable_state_degrades_instead_of_raising(self) -> None:
        body = _public(_manager(), _row(state="SOMETHING_FROM_THE_FUTURE"))
        assert body["state"] == "DISCONNECTED"
        assert body["retry_may_help"] is True

    def test_diagnostics_fields_are_present(self) -> None:
        body = _public(
            _manager(),
            _row(
                state=BridgeState.ONLINE.value,
                peer_protocol="1.0",
                peer_instance_id="inst-a",
                connected_since=1234,
                reconnect_attempts=2,
                state_detail=None,
            ),
        )
        assert body["peer_protocol"] == "1.0"
        assert body["peer_instance_id"] == "inst-a"
        assert body["connected_since"] == 1234
        assert body["reconnect_attempts"] == 2
        assert "state_detail" in body

    def test_no_credential_is_ever_exposed(self) -> None:
        body = _public(
            _manager(),
            _row(credential_blob=b"secret", access_token_blob=b"token"),
        )
        assert body["has_password"] is True
        assert "secret" not in str(body)
        assert "token" not in str(body)
