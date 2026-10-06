"""Stage 3: unit tests for the active-family resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.family.manager import FamilyManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path) -> tuple[SettingsRepo, FamilyManager, FamilyRepo, SqlitePool]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'alice', 'x', 'user', 0, 'zh', 1), "
            "(2, 'bob', 'x', 'user', 0, 'zh', 1)"
        )
    family_repo = FamilyRepo(pool)
    settings = SettingsRepo(pool)
    return settings, FamilyManager(family_repo), family_repo, pool


def _alice() -> User:
    return User(id=1, username="alice", role=Role.USER, display_name="Alice")


def _bob() -> User:
    return User(id=2, username="bob", role=Role.USER, display_name="Bob")


def test_get_returns_none_when_unset(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    resolver = ActiveFamilyResolver(settings, manager)
    assert resolver.get(_alice()) is None
    pool.close()


def test_set_then_get_round_trip(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    family = manager.create_family(
        _alice(), name="Active", timezone="Asia/Shanghai", locale="zh",
    )
    resolver = ActiveFamilyResolver(settings, manager)
    resolver.set(_alice(), family.id)
    assert resolver.get(_alice()) == family.id
    pool.close()


def test_set_rejects_family_user_cannot_access(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    family = manager.create_family(
        _alice(), name="Private", timezone="Asia/Shanghai", locale="zh",
    )
    resolver = ActiveFamilyResolver(settings, manager)
    with pytest.raises(OctopError) as exc_info:
        resolver.set(_bob(), family.id)
    assert exc_info.value.code == ErrorCode.FORBIDDEN
    # And the state must NOT have been written.
    assert resolver.get(_bob()) is None
    pool.close()


def test_set_rejects_nonexistent_family(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    resolver = ActiveFamilyResolver(settings, manager)
    with pytest.raises(OctopError) as exc_info:
        resolver.set(_alice(), "ghost-family-id")
    assert exc_info.value.code == ErrorCode.NOT_FOUND
    pool.close()


def test_clear_resets_state(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    family = manager.create_family(
        _alice(), name="Family", timezone="Asia/Shanghai", locale="zh",
    )
    resolver = ActiveFamilyResolver(settings, manager)
    resolver.set(_alice(), family.id)
    resolver.clear(_alice())
    assert resolver.get(_alice()) is None
    pool.close()


def test_get_clears_stale_value_when_access_revoked(tmp_path: Path) -> None:
    settings, manager, family_repo, pool = _bootstrap(tmp_path)
    family = manager.create_family(
        _alice(), name="Family", timezone="Asia/Shanghai", locale="zh",
    )
    resolver = ActiveFamilyResolver(settings, manager)
    resolver.set(_alice(), family.id)
    assert resolver.get(_alice()) == family.id

    # Remove alice's membership directly from the DB so require_access fails.
    with pool.connect() as conn:
        conn.execute(
            "DELETE FROM homemind_family_memberships WHERE user_id = ?",
            (_alice().id,),
        )

    assert resolver.get(_alice()) is None
    pool.close()


def test_per_user_isolation(tmp_path: Path) -> None:
    settings, manager, _repo, pool = _bootstrap(tmp_path)
    family = manager.create_family(
        _alice(), name="Family", timezone="Asia/Shanghai", locale="zh",
    )
    resolver = ActiveFamilyResolver(settings, manager)
    resolver.set(_alice(), family.id)
    # Bob has no setting of his own.
    assert resolver.get(_bob()) is None
    pool.close()
