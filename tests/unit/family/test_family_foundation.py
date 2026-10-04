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
    assert (
        manager.evaluate_permission(
            family.id,
            owner,
            subject_member_id=child.id,
            space_id=private_space.id,
            action="photo.delete",
        )
        is PermissionEffect.REQUIRE_CONFIRMATION
    )
    assert (
        manager.evaluate_permission(
            family.id,
            owner,
            subject_member_id=child.id,
            action="photo.read",
        )
        is PermissionEffect.DENY
    )


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


def test_update_and_delete_family_resources(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    family = manager.create_family(
        owner, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner_member = repo.list_members(family.id)[0]
    child = manager.create_member(
        family.id, owner, display_name="Child", role=MemberRole.CHILD
    )
    relationship = manager.create_relationship(
        family.id,
        owner,
        from_member_id=owner_member.id,
        to_member_id=child.id,
        relationship_type=RelationshipType.PARENT,
    )
    space = manager.create_space(
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
        space_id=space.id,
        action="photo.delete",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )

    updated_family = manager.update_family(family.id, owner, {"name": "Our Family"})
    updated_child = manager.update_member(
        family.id, child.id, owner, {"display_name": "Teen", "role": MemberRole.MEMBER}
    )
    updated_space = manager.update_space(
        family.id, space.id, owner, {"name": "Teen private"}
    )
    updated_permission = manager.update_permission(
        family.id,
        permission.id,
        owner,
        {"effect": PermissionEffect.DENY},
    )

    assert updated_family.name == "Our Family"
    assert (updated_child.display_name, updated_child.role) == ("Teen", "MEMBER")
    assert updated_space.name == "Teen private"
    assert updated_permission.effect == "DENY"

    manager.delete_permission(family.id, permission.id, owner)
    manager.delete_relationship(family.id, relationship.id, owner)
    manager.delete_space(family.id, space.id, owner)
    manager.delete_member(family.id, child.id, owner)
    assert repo.list_permissions(family.id) == []
    assert repo.list_relationships(family.id) == []
    assert [member.id for member in repo.list_members(family.id)] == [owner_member.id]


def test_owner_member_is_protected(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    family = manager.create_family(
        owner, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner_member = repo.list_members(family.id)[0]

    with pytest.raises(OctopError) as exc_info:
        manager.delete_member(family.id, owner_member.id, owner)

    assert exc_info.value.code is ErrorCode.FORBIDDEN


def test_duplicate_space_returns_stable_conflict(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)
    family = manager.create_family(
        owner, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )

    with pytest.raises(OctopError) as exc_info:
        manager.create_space(
            family.id, owner, name="Shared", space_type=SpaceType.SHARED
        )

    assert exc_info.value.code is ErrorCode.FAMILY_CONFLICT


def test_invalid_family_timezone_is_rejected(repo: FamilyRepo, owner: User) -> None:
    manager = FamilyManager(repo)

    with pytest.raises(OctopError) as exc_info:
        manager.create_family(
            owner, name="My Family", timezone="Invalid/Timezone", locale="zh"
        )

    assert exc_info.value.code is ErrorCode.FAMILY_INVALID
