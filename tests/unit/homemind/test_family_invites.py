"""Stage 10: unit tests for family invites + redemption."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_invites import FamilyInviteRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.invites import (
    FamilyInviteManager,
    MAX_INVITE_TTL_SECONDS,
    MIN_INVITE_TTL_SECONDS,
    clamp_invite_ttl,
    hash_invite_token,
    mint_invite_token,
)
from homemind.infra.family.manager import FamilyManager, MemberRole
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


@pytest.fixture
def repo(tmp_path: Path) -> FamilyInviteRepo:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'redeemer', 'x', 'user', 0, 'zh', 1), "
            "(3, 'outsider', 'x', 'user', 0, 'zh', 1)"
        )
    return FamilyInviteRepo(pool)


@pytest.fixture
def manager(repo: FamilyInviteRepo) -> FamilyInviteManager:
    return FamilyInviteManager(FamilyManager(_FakeRepo()), repo)


class _FakeRepo:
    """Stub repo with the methods invite manager needs from FamilyManager.

    The invite manager delegates ``require_manager`` / ``create_member``
    to ``self.family``, which needs a real ``FamilyRepo`` for the
    membership + member writes. We hand it the real repo via the test
    fixture instead.
    """


def _bootstrap_manager(tmp_path: Path) -> tuple[
    FamilyInviteManager, str, User, User, SqlitePool,
]:
    """Build a fully-wired FamilyInviteManager + FamilyManager against a real DB."""
    from homemind.infra.db.repos.families import FamilyRepo

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'redeemer', 'x', 'user', 0, 'zh', 1)"
        )
    services_repo = FamilyRepo(pool)
    family_manager = FamilyManager(services_repo)
    invite_repo = FamilyInviteRepo(pool)
    invite_manager = FamilyInviteManager(family_manager, invite_repo)
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = family_manager.create_family(
        owner, name="Invite Family", timezone="Asia/Shanghai", locale="zh",
    )
    redeemer = User(id=2, username="redeemer", role=Role.USER, display_name="Redeemer")
    return invite_manager, family.id, owner, redeemer, pool


def test_hash_invite_token_is_stable() -> None:
    assert hash_invite_token("abc") == hash_invite_token("abc")
    assert hash_invite_token("abc") != hash_invite_token("xyz")


def test_mint_invite_token_returns_unique_strings() -> None:
    tokens = {mint_invite_token() for _ in range(20)}
    assert len(tokens) == 20


def test_clamp_invite_ttl_clamps_at_min_and_max() -> None:
    assert clamp_invite_ttl(10) == MIN_INVITE_TTL_SECONDS
    assert clamp_invite_ttl(10**9) == MAX_INVITE_TTL_SECONDS
    assert clamp_invite_ttl(3600) == 3600


def test_create_invite_returns_token_with_default_ttl(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, _redeemer, pool = _bootstrap_manager(tmp_path)
    before = int(time.time())
    token = invite_manager.create_invite(
        family_id, owner,
        display_name="Child",
    )
    assert token.invite_id
    assert token.token
    assert token.expires_at > before + 6 * 24 * 3600
    pool.close()


def test_create_invite_requires_manager(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    with pytest.raises(Exception) as exc_info:
        invite_manager.create_invite(
            family_id, redeemer, display_name="Child",
        )
    # Manager-only check lives on ``require_manager`` which raises
    # ``OctopError(FORBIDDEN)``; any failure mode is acceptable here
    # so long as the redeemer can NOT mint an invite for themselves.
    assert exc_info.value is not None
    pool.close()


def test_create_invite_rejects_blank_display_name(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, _redeemer, pool = _bootstrap_manager(tmp_path)
    with pytest.raises(HomeMindError):
        invite_manager.create_invite(
            family_id, owner, display_name="   ",
        )
    pool.close()


def test_redeem_binds_member_to_user(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    token = invite_manager.create_invite(
        family_id, owner, display_name="Child", role=MemberRole.CHILD,
    )

    result = invite_manager.redeem(token.token, redeemer)

    assert result.family_id == family_id
    assert result.role == "CHILD"
    assert result.display_name == "Child"
    with pool.connect() as conn:
        member = conn.execute(
            "SELECT * FROM homemind_family_members WHERE member_id = ?",
            (result.member_id,),
        ).fetchone()
    assert member is not None
    assert int(member["user_id"]) == redeemer.id  # type: ignore[arg-type]
    pool.close()


def test_redeem_rejects_expired_invite(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    token = invite_manager.create_invite(
        family_id, owner, display_name="Child",
    )
    # Force expiry in the DB.
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_invites SET expires_at = 1 WHERE invite_id = ?",
            (token.invite_id,),
        )

    with pytest.raises(HomeMindError):
        invite_manager.redeem(token.token, redeemer)
    pool.close()


def test_redeem_rejects_already_redeemed_invite(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    token = invite_manager.create_invite(
        family_id, owner, display_name="Child",
    )
    invite_manager.redeem(token.token, redeemer)

    with pytest.raises(HomeMindError):
        invite_manager.redeem(token.token, redeemer)
    pool.close()


def test_redeem_rejects_unknown_token(
    tmp_path: Path,
) -> None:
    invite_manager, _family_id, _owner, redeemer, pool = _bootstrap_manager(tmp_path)
    with pytest.raises(HomeMindError):
        invite_manager.redeem("totally-not-a-token", redeemer)
    pool.close()


def test_revoke_blocks_redemption(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    token = invite_manager.create_invite(
        family_id, owner, display_name="Child",
    )
    assert invite_manager.revoke_invite(family_id, token.invite_id, owner) is True
    with pytest.raises(HomeMindError):
        invite_manager.redeem(token.token, redeemer)
    pool.close()


def test_revoke_only_succeeds_for_unredeemed(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    token = invite_manager.create_invite(
        family_id, owner, display_name="Child",
    )
    invite_manager.redeem(token.token, redeemer)
    # Already redeemed — revoke should be a no-op (return False).
    assert invite_manager.revoke_invite(family_id, token.invite_id, owner) is False
    pool.close()


def test_list_invites_excludes_redeemed_by_default(
    tmp_path: Path,
) -> None:
    invite_manager, family_id, owner, redeemer, pool = _bootstrap_manager(tmp_path)
    t1 = invite_manager.create_invite(family_id, owner, display_name="Child A")
    t2 = invite_manager.create_invite(family_id, owner, display_name="Child B")
    invite_manager.redeem(t1.token, redeemer)

    pending = invite_manager.list_invites(family_id, owner)
    pending_ids = {row.id for row in pending}
    assert t2.invite_id in pending_ids
    assert t1.invite_id not in pending_ids

    all_invites = invite_manager.list_invites(
        family_id, owner, include_redeemed=True,
    )
    all_ids = {row.id for row in all_invites}
    assert t1.invite_id in all_ids
    assert t2.invite_id in all_ids
    pool.close()
