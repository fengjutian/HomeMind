from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.families import FamilyRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    RelationshipType,
    SpaceType,
)
from octop.infra.users.identity import Role, User


@pytest.fixture
def repo(tmp_path: Path) -> FamilyRepo:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'member', 'x', 'user', 0, 'zh', 1), "
            "(3, 'outsider', 'x', 'user', 0, 'zh', 1)"
        )
    return FamilyRepo(pool)


@pytest.fixture
def owner() -> User:
    return User(id=1, username="owner", role=Role.USER, display_name="Owner")


def test_create_family_creates_owner_membership_and_shared_space(
    repo: FamilyRepo, owner: User
) -> None:
    manager = FamilyManager(repo)

    family = manager.create_family(owner, name="My Family", timezone="Asia/Shanghai", locale="zh")

    assert manager.list_families(owner) == [family]
    members = repo.list_members(family.id)
    assert len(members) == 1
    assert members[0].user_id == owner.id
    assert members[0].role == "OWNER"
    spaces = repo.list_spaces(family.id)
    assert [(space.name, space.space_type) for space in spaces] == [("Shared", "SHARED")]


def test_manager_builds_family_structure(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    family = manager.create_family(owner, name="My Family", timezone="Asia/Shanghai", locale="zh")
    owner_member = repo.list_members(family.id)[0]
    child = manager.create_member(
        family.id,
        owner,
        display_name="Child",
        role=MemberRole.CHILD,
        user_id=2,
    )

    relationship = manager.create_relationship(
        family.id,
        owner,
        from_member_id=owner_member.id,
        to_member_id=child.id,
        relationship_type=RelationshipType.PARENT,
    )
    private_space = manager.create_space(
        family.id,
        owner,
        name="Child private",
        space_type=SpaceType.PRIVATE,
        owner_member_id=child.id,
    )
    permission = manager.create_permission(
        family.id,
        owner,
        subject_member_id=child.id,
        space_id=private_space.id,
        action="photo.delete",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )

    assert relationship.relationship_type == "PARENT"
    assert private_space.owner_member_id == child.id
    assert permission.effect == "REQUIRE_CONFIRMATION"
    assert repo.get_membership(family.id, 2) is not None


def test_non_member_cannot_read_family(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    family = manager.create_family(owner, name="My Family", timezone="Asia/Shanghai", locale="zh")
    outsider = User(id=3, username="outsider", role=Role.USER, display_name=None)

    with pytest.raises(OctopError) as exc_info:
        manager.require_access(family.id, outsider)

    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_related_members_must_belong_to_same_family(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    first = manager.create_family(owner, name="First", timezone="Asia/Shanghai", locale="zh")
    other_owner = User(id=3, username="outsider", role=Role.USER, display_name=None)
    second = manager.create_family(
        other_owner, name="Second", timezone="Asia/Shanghai", locale="zh"
    )

    with pytest.raises(OctopError) as exc_info:
        manager.create_relationship(
            first.id,
            owner,
            from_member_id=repo.list_members(first.id)[0].id,
            to_member_id=repo.list_members(second.id)[0].id,
            relationship_type=RelationshipType.OTHER,
        )

    assert exc_info.value.code is ErrorCode.NOT_FOUND
