"""Manager-level token refresh behaviour (plan phase 14).

The policy is unit-tested in ``test_token_refresh``; these assert the manager
actually applies it — in particular that a revoked credential lands the
connection in ``REAUTH_REQUIRED`` rather than a generic error an operator
cannot act on.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import octop.infra.bridge.manager as mod
from octop.infra.bridge.manager import BridgeManager, RefreshRefused, _status_of
from octop.infra.bridge.states import BridgeState
from octop.infra.bridge.token_refresh import RefreshFailure
from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.bridge_connections import BridgeConnectionRow
from octop.infra.errors import ErrorCode, OctopError


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
        "credential_blob": b"encrypted",
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


def _manager() -> tuple[BridgeManager, list[Any]]:
    mgr = BridgeManager.__new__(BridgeManager)
    mgr._refreshers = {}  # noqa: SLF001
    mgr._instance_id = "inst-local"  # noqa: SLF001
    mgr._sessions = {}  # noqa: SLF001
    mgr._secrets = object()  # noqa: SLF001
    mgr._repo = _RecordingRepo()  # noqa: SLF001
    states: list[Any] = []
    mgr._set_state = lambda cid, state, **kw: states.append((cid, state, kw))  # type: ignore[method-assign]
    return mgr, states


class _RecordingRepo:
    """Enough of the repo for token paths; records the destructive calls."""

    def __init__(self) -> None:
        self.cleared: list[str] = []
        self.writes = 0

    def clear_credentials(self, connection_id: str, *, keep_password: bool = False) -> bool:
        self.cleared.append(f"{connection_id}:{'pw' if keep_password else 'full'}")
        return True

    def update_credentials(self, *_a: Any, **_kw: Any) -> int:
        self.writes += 1
        return self.writes

    def update_credentials_if_current(self, *_a: Any, **_kw: Any) -> bool:
        self.writes += 1
        return True

    def get(self, connection_id: str) -> Any:
        return None

    def update_status(self, *_a: Any, **_kw: Any) -> None:
        return None


def _stored_token(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    """Make the encrypted-token blob decrypt to ``value``."""

    def _fake(_secrets: Any, _blob: bytes) -> dict[str, Any]:
        return {"access_token": value}

    monkeypatch.setattr(mod, "decrypt_payload", _fake)


class TestStatusRecovery:
    def test_no_status_means_the_peer_was_never_reached(self) -> None:
        err = OctopError(ErrorCode.BRIDGE_PEER_UNREACHABLE, "peer login failed: timed out")
        assert _status_of(err) is None

    def test_a_401_is_recovered_from_the_message(self) -> None:
        err = OctopError(ErrorCode.BRIDGE_AUTH_FAILED, "peer login failed: 401 rejected")
        assert _status_of(err) == 401

    def test_a_503_is_recovered(self) -> None:
        err = OctopError(ErrorCode.BRIDGE_PEER_UNREACHABLE, "peer login failed: 503")
        assert _status_of(err) == 503


class TestFailureBecomesAState:
    async def test_revoked_credentials_land_in_reauth_required(self) -> None:
        mgr, states = _manager()

        async def _login(_self: Any = None) -> str:
            raise RefreshRefused(RefreshFailure.CREDENTIAL_REVOKED, "peer login failed: 401")

        mgr._login_peer = _login  # type: ignore[method-assign]

        with pytest.raises(OctopError) as excinfo:
            await mgr._ensure_peer_token(_row())

        assert excinfo.value.code is ErrorCode.BRIDGE_AUTH_FAILED
        assert "CREDENTIAL_REVOKED" in str(excinfo.value)
        assert states[0][1] is BridgeState.REAUTH_REQUIRED

    async def test_missing_credentials_land_in_reauth_required(self) -> None:
        mgr, states = _manager()

        async def _login(_self: Any = None) -> str:
            raise RefreshRefused(RefreshFailure.NO_CREDENTIAL, "no peer credentials stored")

        mgr._login_peer = _login  # type: ignore[method-assign]
        with pytest.raises(OctopError):
            await mgr._ensure_peer_token(_row(credential_blob=None))
        assert states[0][1] is BridgeState.REAUTH_REQUIRED

    async def test_a_transient_failure_lands_in_degraded(self) -> None:
        """Retryable, so it must not be reported as needing a re-login."""
        mgr, states = _manager()

        async def _login(_self: Any = None) -> str:
            raise RefreshRefused(RefreshFailure.TRANSIENT, "peer unreachable")

        mgr._login_peer = _login  # type: ignore[method-assign]
        with pytest.raises(OctopError):
            await mgr._ensure_peer_token(_row())
        assert states[0][1] is BridgeState.DEGRADED

    async def test_a_server_error_lands_in_degraded(self) -> None:
        mgr, states = _manager()

        async def _login(_self: Any = None) -> str:
            raise RefreshRefused(RefreshFailure.PROTOCOL_OR_SERVER, "peer login failed: 426")

        mgr._login_peer = _login  # type: ignore[method-assign]
        with pytest.raises(OctopError):
            await mgr._ensure_peer_token(_row())
        assert states[0][1] is BridgeState.DEGRADED

    async def test_a_rejected_token_is_forgotten_so_the_link_can_recover(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: a peer-rejected token used to be re-presented forever.

        The token looked fine by expiry, so the login path was never taken and
        every reconnect re-sent the same rejected token.
        """
        from octop.infra.bridge.manager import is_auth_rejection
        from octop.infra.bridge.token_refresh import TokenRefresher

        mgr, _states = _manager()
        logins = 0

        async def _login(_self: Any = None) -> str:
            nonlocal logins
            logins += 1
            return "tok"

        mgr._login_peer = _login  # type: ignore[method-assign]
        row = _row(access_token_blob=b"blob", token_expires_at=now_ts() + 3600)
        cid = row.connection_id
        refresher = TokenRefresher(lambda: _login())
        refresher.seed(cid, "rejected-token")
        mgr._refreshers[cid] = refresher  # noqa: SLF001
        _stored_token(monkeypatch, "rejected-token")

        # The stored token is still "fresh", so it is handed straight back.
        assert await mgr._ensure_peer_token(row) == "rejected-token"
        assert refresher.cached(cid) == "rejected-token"

        # The peer then refuses it on the socket, not on the login.
        assert is_auth_rejection(_Closed(4001, "auth: FORBIDDEN")) is True
        mgr._forget_token(cid)  # noqa: SLF001
        mgr._repo.clear_credentials(cid, keep_password=True)  # type: ignore[call-arg]

        assert refresher.cached(cid) is None
        # Next attempt really re-authenticates rather than replaying the token.
        assert await mgr._ensure_peer_token(_row(access_token_blob=None)) == "tok"
        assert logins == 1


class _Closed(Exception):
    def __init__(self, code: int, reason: str) -> None:
        super().__init__(f"code={code} reason={reason}")
        self.rcvd = SimpleNamespace(code=code, reason=reason)


class TestTokenReuse:
    async def test_a_long_lived_token_is_reused_without_logging_in(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr, _states = _manager()
        calls = 0

        async def _login(_self: Any = None) -> str:
            nonlocal calls
            calls += 1
            return "fresh-token"

        mgr._login_peer = _login  # type: ignore[method-assign]
        _stored_token(monkeypatch, "stored-token")

        row = _row(access_token_blob=b"blob", token_expires_at=now_ts() + 24 * 3600)
        assert await mgr._ensure_peer_token(row) == "stored-token"
        assert calls == 0

    async def test_an_expiring_token_triggers_exactly_one_login(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mgr, _states = _manager()
        calls = 0

        async def _login(_self: Any = None) -> str:
            nonlocal calls
            calls += 1
            return "new-token"

        mgr._login_peer = _login  # type: ignore[method-assign]
        mgr._repo = _FakeRepo()  # type: ignore[method-assign]
        _stored_token(monkeypatch, "stale")

        row = _row(access_token_blob=b"blob", token_expires_at=now_ts() + 10)
        first = await mgr._ensure_peer_token(row)
        second = await mgr._ensure_peer_token(row)

        assert first == second == "new-token"
        assert calls == 1, "the refreshed token must be cached, not re-fetched"

    async def test_a_burst_of_callers_logs_in_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The failure being fixed: a peer restart used to mean N logins."""
        mgr, _states = _manager()
        calls = 0

        async def _login(_self: Any = None) -> str:
            nonlocal calls
            calls += 1
            return "tok"

        mgr._login_peer = _login  # type: ignore[method-assign]
        mgr._repo = _FakeRepo()  # type: ignore[method-assign]
        _stored_token(monkeypatch, "stale")

        row = _row(access_token_blob=b"blob", token_expires_at=now_ts())
        import asyncio

        tokens = await asyncio.gather(*(mgr._ensure_peer_token(row) for _ in range(4)))
        assert tokens == ["tok"] * 4
        assert calls == 1


class _FakeRepo:
    def __init__(self) -> None:
        self.writes = 0

    def update_credentials(self, *_a: Any, **_kw: Any) -> None:
        self.writes += 1
