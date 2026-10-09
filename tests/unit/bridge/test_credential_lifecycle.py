"""Credential lifecycle: CAS writes and immediate invalidation (plan phase 14)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from octop.infra.bridge.states import BridgeState
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.bridge_connections import BridgeConnectionRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def pool(tmp_path: Path) -> SqlitePool:
    p = SqlitePool(tmp_path / "octop.db")
    run_migrations(p)
    return p


@pytest.fixture
def repo(pool: SqlitePool) -> BridgeConnectionRepo:
    user = UserRepo(pool).create(username="owner", password_hash="h", role="user")
    r = BridgeConnectionRepo(pool)
    r.create(
        connection_id="c1",
        owner_user_id=user,
        peer_base_url="http://peer:8080",
        peer_username="peer",
        display_name="Peer",
        credential_blob=b"pw",
    )
    return r


class TestTokenVersion:
    def test_starts_at_zero(self, repo: BridgeConnectionRepo) -> None:
        assert repo.get("c1").token_version == 0  # type: ignore[union-attr]

    def test_a_credential_write_bumps_the_version(self, repo: BridgeConnectionRepo) -> None:
        assert repo.update_credentials("c1", access_token_blob=b"t1") == 1
        assert repo.get("c1").token_version == 1  # type: ignore[union-attr]
        assert repo.update_credentials("c1", access_token_blob=b"t2") == 2

    def test_the_timestamp_is_recorded(self, repo: BridgeConnectionRepo) -> None:
        repo.update_credentials("c1", access_token_blob=b"t1")
        assert repo.get("c1").credentials_updated_at is not None  # type: ignore[union-attr]


class TestCompareAndSwap:
    def test_a_matching_version_commits(self, repo: BridgeConnectionRepo) -> None:
        assert (
            repo.update_credentials_if_current(
                "c1", expected_token_version=0, access_token_blob=b"fresh"
            )
            is True
        )
        assert repo.get("c1").access_token_blob == b"fresh"  # type: ignore[union-attr]
        assert repo.get("c1").token_version == 1  # type: ignore[union-attr]

    def test_a_stale_version_is_refused(self, repo: BridgeConnectionRepo) -> None:
        repo.update_credentials("c1", access_token_blob=b"newer")
        assert (
            repo.update_credentials_if_current(
                "c1", expected_token_version=0, access_token_blob=b"older"
            )
            is False
        )

    def test_a_refused_write_does_not_change_the_stored_value(
        self, repo: BridgeConnectionRepo
    ) -> None:
        """The whole point: a slow writer must not clobber a newer token."""
        repo.update_credentials("c1", access_token_blob=b"newer")
        repo.update_credentials_if_current(
            "c1", expected_token_version=0, access_token_blob=b"older"
        )
        row = repo.get("c1")
        assert row.access_token_blob == b"newer"  # type: ignore[union-attr]
        assert row.token_version == 1  # type: ignore[union-attr]

    def test_two_writers_race_and_exactly_one_wins(self, repo: BridgeConnectionRepo) -> None:
        """Simulates two connections refreshing the same peer token."""
        slow = repo.update_credentials_if_current(
            "c1", expected_token_version=0, access_token_blob=b"slow"
        )
        fast = repo.update_credentials_if_current(
            "c1", expected_token_version=0, access_token_blob=b"fast"
        )
        assert slow is not False
        assert slow is not fast, "exactly one writer may commit"
        assert repo.get("c1").token_version == 1  # type: ignore[union-attr]

    def test_an_unknown_connection_is_refused(self, repo: BridgeConnectionRepo) -> None:
        assert (
            repo.update_credentials_if_current(
                "nope", expected_token_version=0, access_token_blob=b"x"
            )
            is False
        )


class TestImmediateInvalidation:
    def _manager(self) -> Any:
        from octop.infra.bridge.manager import BridgeManager

        mgr = BridgeManager.__new__(BridgeManager)
        mgr._refreshers = {}  # noqa: SLF001
        mgr._user_stopped = set()  # noqa: SLF001
        mgr._client_tasks = {}  # noqa: SLF001
        mgr._sessions = {}  # noqa: SLF001
        mgr._reconnect_failures = {}  # noqa: SLF001
        mgr._lock = asyncio.Lock()  # noqa: SLF001
        mgr.states = []  # noqa: SLF001
        mgr._set_state = (  # type: ignore[method-assign]
            lambda cid, state, **kw: mgr.states.append((cid, state))  # noqa: SLF001
        )
        return mgr

    def _refresher_with_token(self, mgr: Any) -> Any:
        from octop.infra.bridge.token_refresh import TokenRefresher

        async def _login() -> str:  # pragma: no cover - never called
            return "tok"

        refresher = TokenRefresher(_login)
        refresher.seed("c1", "cached-token")
        mgr._refreshers["c1"] = refresher  # noqa: SLF001
        return refresher

    async def test_disconnect_forgets_the_cached_token(self) -> None:
        from octop.infra.bridge.manager import BridgeManager

        mgr = self._manager()
        refresher = self._refresher_with_token(mgr)
        mgr._repo = type("R", (), {"get": staticmethod(lambda _cid: None)})()  # noqa: SLF001

        await BridgeManager.disconnect(mgr, "c1")

        assert refresher.cached("c1") is None
        assert "c1" not in mgr._refreshers  # noqa: SLF001

    async def test_revoke_tears_down_and_clears(self) -> None:
        from octop.infra.bridge.manager import BridgeManager

        mgr = self._manager()
        self._refresher_with_token(mgr)
        closed: list[str] = []

        class _Sess:
            closed = False

            async def close(self) -> None:
                closed.append("c1")

        mgr._sessions["c1"] = _Sess()  # noqa: SLF001
        mgr._repo = _ClearingRepo()  # noqa: SLF001
        mgr.get_owned = lambda _cid, _uid: object()  # type: ignore[method-assign]

        await BridgeManager.revoke_credentials(mgr, "c1", owner_user_id=1)

        assert closed == ["c1"], "the socket must be closed immediately"
        assert mgr._refreshers == {}  # noqa: SLF001
        # The encrypted credential must leave the table too, not just memory.
        assert mgr._repo.cleared == [("c1", False)]  # type: ignore[attr-defined]

    async def test_revoke_marks_the_connection_for_reauth(self) -> None:
        from octop.infra.bridge.manager import BridgeManager

        mgr = self._manager()
        self._refresher_with_token(mgr)
        mgr._repo = _ClearingRepo()  # noqa: SLF001
        mgr.get_owned = lambda _cid, _uid: object()  # type: ignore[method-assign]

        await BridgeManager.revoke_credentials(mgr, "c1", owner_user_id=1)

        assert ("c1", BridgeState.REAUTH_REQUIRED) in mgr.states  # type: ignore[attr-defined]


class _ClearingRepo:
    """Records credential wipes, so revocation is verifiable."""

    def __init__(self) -> None:
        self.cleared: list[tuple[str, bool]] = []

    def clear_credentials(self, connection_id: str, *, keep_password: bool = False) -> bool:
        self.cleared.append((connection_id, keep_password))
        return True

    def get(self, _connection_id: str) -> Any:
        return None

    def update_status(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def test_revoke_ownership_is_checked(self) -> None:
        from octop.infra.errors import ErrorCode, OctopError

        mgr = self._manager()

        def _deny(_cid: str, _uid: int) -> Any:
            raise OctopError(ErrorCode.BRIDGE_NOT_FOUND, "not yours")

        mgr.get_owned = _deny  # type: ignore[method-assign]
        with pytest.raises(OctopError):
            await mgr.revoke_credentials("c1", owner_user_id=999)


class TestStateVisibility:
    def test_reauth_required_is_visible_in_the_public_payload(self) -> None:
        """The whole reason the state machine exists: tell the operator what to do."""
        from octop.infra.bridge.manager import BridgeManager
        from octop.infra.db.repos.bridge_connections import BridgeConnectionRow

        mgr = BridgeManager.__new__(BridgeManager)
        mgr._sessions = {}  # noqa: SLF001
        row = BridgeConnectionRow(
            pk=1,
            connection_id="c1",
            owner_user_id=1,
            peer_base_url="http://peer",
            peer_username="peer",
            display_name="Peer",
            notes=None,
            icon_name=None,
            credential_blob=None,
            access_token_blob=None,
            token_expires_at=None,
            status="error",
            last_error=None,
            last_seen_at=None,
            auto_reconnect=True,
            state=BridgeState.REAUTH_REQUIRED.value,
            created_at=0,
            updated_at=0,
        )
        body = mgr.connection_public(row)
        assert body["state"] == "REAUTH_REQUIRED"
        assert body["retry_may_help"] is False
