"""Stage 9 acceptance tests for family member lifecycle.

Covers the spec's rules:

* an owner can transfer ownership, and the previous owner is demoted,
* the owner cannot leave without transferring,
* the last manager cannot leave or be deleted,
* one user cannot bind to two members in the same family,
* a member cannot be rebound to a different user,
* binding and unbinding round-trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.errors import HomeMindError
from homemind.infra.family.manager import FamilyManager, MemberRole
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'spouse', 'x', 'user', 0, 'zh', 1), "
            "(3, 'friend', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="owner", role=Role.USER, display_name="爸爸")
    spouse = User(id=2, username="spouse", role=Role.USER, display_name="妈妈")
    friend = User(id=3, username="friend", role=Role.USER, display_name="朋友")
    family_repo = FamilyRepo(pool)
    manager = FamilyManager(family_repo)
    family = manager.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    return pool, manager, family_repo, owner, spouse, friend, family.id


# ----------------------------------------------------------------- transfer


def test_owner_can_transfer_ownership(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    # Ownership can only land on a member bound to a real account —
    # ``owner_user_id`` is a NOT NULL FK to ``users``.
    target = manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN, user_id=spouse.id,
    )
    updated = manager.transfer_ownership(family_id, owner, to_member_id=target.id)
    assert updated.role == MemberRole.OWNER

    members = {row.id: row for row in manager.repo.list_members(family_id)}
    old_owner = next(
        row for row in members.values() if row.user_id == owner.id and row.id != target.id
    )
    assert old_owner.role == MemberRole.ADMIN, "previous owner must be demoted"
    assert manager.repo.get_family(family_id).owner_user_id == target.user_id
    pool.close()


def test_cannot_transfer_to_unbound_member(tmp_path: Path) -> None:
    """A placeholder member with no account cannot become owner —
    ``owner_user_id`` is a NOT NULL foreign key to ``users``."""

    pool, manager, _repo, owner, _spouse, _friend, family_id = _bootstrap(tmp_path)
    unbound = manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN,
    )
    with pytest.raises(HomeMindError) as info:
        manager.transfer_ownership(family_id, owner, to_member_id=unbound.id)
    assert "bind" in str(info.value).lower()
    pool.close()


def test_non_owner_cannot_transfer(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, friend, family_id = _bootstrap(tmp_path)
    target = manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN, user_id=spouse.id,
    )
    manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER, user_id=friend.id,
    )
    with pytest.raises(OctopError):
        manager.transfer_ownership(family_id, spouse, to_member_id=target.id)
    pool.close()


def test_cannot_transfer_to_itself(tmp_path: Path) -> None:
    pool, manager, _repo, owner, _spouse, _friend, family_id = _bootstrap(tmp_path)
    owner_member = manager.repo.get_membership(family_id, owner.id)
    assert owner_member is not None
    with pytest.raises(HomeMindError):
        manager.transfer_ownership(
            family_id, owner, to_member_id=str(owner_member["member_id"]),
        )
    pool.close()


# -------------------------------------------------------------------- leave


def test_owner_cannot_leave_without_transferring(tmp_path: Path) -> None:
    pool, manager, _repo, owner, _spouse, _friend, family_id = _bootstrap(tmp_path)
    with pytest.raises(HomeMindError) as info:
        manager.leave_family(family_id, owner)
    assert "transfer" in str(info.value).lower()
    pool.close()


def test_last_manager_cannot_leave(tmp_path: Path) -> None:
    """A family where the only remaining manager is ``spouse`` — the
    owner row was demoted and then handed the admin role, so leaving
    would strand the family with nobody able to approve anything."""

    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN, user_id=spouse.id,
    )
    # Demote the owner to a plain member so the admin is the only
    # manager left.
    owner_membership = manager.repo.get_membership(family_id, owner.id)
    assert owner_membership is not None
    manager.repo.update_member(
        str(owner_membership["member_id"]), role=MemberRole.MEMBER.value,
    )
    with pytest.raises(HomeMindError) as info:
        manager.leave_family(family_id, spouse)
    assert "manager" in str(info.value).lower()
    pool.close()


def test_member_can_leave_when_another_manager_exists(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN, user_id=2,
    )
    leaving = manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER, user_id=3,
    )
    manager.leave_family(family_id, User(id=3, username="friend", role=Role.USER, display_name="朋友"))
    assert manager.repo.get_member(leaving.id) is None
    pool.close()


def test_last_manager_cannot_be_deleted(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    admin = manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.ADMIN, user_id=2,
    )
    # Same setup: the owner is demoted so the admin is the last manager.
    owner_membership = manager.repo.get_membership(family_id, owner.id)
    assert owner_membership is not None
    manager.repo.update_member(
        str(owner_membership["member_id"]), role=MemberRole.MEMBER.value,
    )
    with pytest.raises(HomeMindError) as info:
        manager.delete_member(family_id, admin.id, spouse)
    assert "manager" in str(info.value).lower()
    pool.close()


# ------------------------------------------------------------------ binding


def test_bind_and_unbind_user(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    unbound = manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER,
    )
    assert unbound.user_id is None

    bound = manager.bind_user(
        family_id, unbound.id, owner, target_user_id=spouse.id,
    )
    assert bound.user_id == spouse.id

    released = manager.unbind_user(family_id, unbound.id, owner)
    assert released.user_id is None
    pool.close()


def test_user_cannot_bind_to_two_members(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    first = manager.create_member(
        family_id, owner, display_name="朋友A", role=MemberRole.MEMBER,
    )
    second = manager.create_member(
        family_id, owner, display_name="朋友B", role=MemberRole.MEMBER,
    )
    manager.bind_user(family_id, first.id, owner, target_user_id=spouse.id)
    with pytest.raises(HomeMindError) as info:
        manager.bind_user(family_id, second.id, owner, target_user_id=spouse.id)
    assert info.value.code is not None
    pool.close()


def test_member_cannot_be_rebound_to_another_user(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, friend, family_id = _bootstrap(tmp_path)
    member = manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER,
    )
    manager.bind_user(family_id, member.id, owner, target_user_id=spouse.id)
    with pytest.raises(HomeMindError):
        manager.bind_user(family_id, member.id, owner, target_user_id=friend.id)
    pool.close()


def test_binding_unknown_user_is_refused(tmp_path: Path) -> None:
    pool, manager, _repo, owner, _spouse, _friend, family_id = _bootstrap(tmp_path)
    member = manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER,
    )
    with pytest.raises(OctopError):
        manager.bind_user(family_id, member.id, owner, target_user_id=999)
    pool.close()


def test_non_manager_cannot_bind(tmp_path: Path) -> None:
    pool, manager, _repo, owner, spouse, _friend, family_id = _bootstrap(tmp_path)
    member = manager.create_member(
        family_id, owner, display_name="朋友", role=MemberRole.MEMBER,
    )
    with pytest.raises(OctopError) as info:
        manager.bind_user(family_id, member.id, spouse, target_user_id=spouse.id)
    assert info.value.code is ErrorCode.FORBIDDEN
    pool.close()
