"""End-to-end tests for ``FamilyContextManager.resolve`` against the
Stage 2 resolvers and the centralized permission evaluator.

Covers the four mandated scenarios from the plan spec §2.6:

* "我妈妈去年生日的照片"
* "去年春节我们去了哪里"
* "找孩子的照片" (ambiguity when 2 children exist)
* "妈妈的私人文件" (resolved but permission denied)
"""

from __future__ import annotations

import json
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    RelationshipType,
    SpaceType,
)
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _pool(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
            "(3, 'outsider', 'x', 'user', 0, 'zh', 1)"
        )
    return pool


def _seed(pool: SqlitePool) -> tuple[FamilyContextManager, FamilyManager, User, dict[str, str]]:
    family = FamilyManager(FamilyRepo(pool))
    papa = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    fam = family.create_family(
        papa, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    papa_id = members["爸爸"].id
    family.create_member(
        fam.id, papa, display_name="妈妈", role=MemberRole.MEMBER, user_id=2,
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    mama_id = members["妈妈"].id
    child1 = family.create_member(fam.id, papa, display_name="大宝", role=MemberRole.CHILD)
    child2 = family.create_member(fam.id, papa, display_name="小宝", role=MemberRole.CHILD)
    family.create_relationship(
        fam.id, papa, from_member_id=papa_id, to_member_id=mama_id,
        relationship_type=RelationshipType.SPOUSE,
    )
    family.create_relationship(
        fam.id, papa, from_member_id=papa_id, to_member_id=child1.id,
        relationship_type=RelationshipType.PARENT,
    )
    family.create_relationship(
        fam.id, papa, from_member_id=papa_id, to_member_id=child2.id,
        relationship_type=RelationshipType.PARENT,
    )
    family.create_space(
        fam.id, papa, name="妈妈的私人", space_type=SpaceType.PRIVATE,
        owner_member_id=mama_id,
    )
    permissions = FamilyPermissionEvaluator(FamilyRepo(pool))
    context = FamilyContextManager(family, FamilyContextRepo(pool), permission_evaluator=permissions)
    return context, family, papa, {
        "family_id": fam.id,
        "papa_id": papa_id,
        "mama_id": mama_id,
        "child1_id": child1.id,
        "child2_id": child2.id,
    }


def test_resolve_mom_last_year_birthday_photos(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, _family, user, ids = _seed(pool)
    resolved = context.resolve(ids["family_id"], user, "我妈妈去年生日的照片")
    assert ids["mama_id"] in resolved.member_ids
    assert resolved.time_range is not None
    # time_range.start_at should be 2025-01-01
    from datetime import datetime
    from zoneinfo import ZoneInfo
    expected = int(datetime(2025, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    assert resolved.time_range.start_at == expected


def test_resolve_last_year_spring_festival(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, _family, user, ids = _seed(pool)
    resolved = context.resolve(ids["family_id"], user, "去年春节我们去了哪里")
    assert resolved.time_range is not None
    from datetime import datetime
    from zoneinfo import ZoneInfo
    expected = int(datetime(2025, 1, 21, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    assert resolved.time_range.start_at == expected


def test_resolve_children_photos_returns_ambiguity(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, _family, user, ids = _seed(pool)
    resolved = context.resolve(ids["family_id"], user, "找孩子的照片")
    # Two children, both must appear and the ambiguity list must be non-empty.
    assert ids["child1_id"] in resolved.member_ids
    assert ids["child2_id"] in resolved.member_ids
    assert len(resolved.ambiguities) > 0
    ambiguous_ids = {a.entity_id for a in resolved.ambiguities}
    assert ids["child1_id"] in ambiguous_ids
    assert ids["child2_id"] in ambiguous_ids


def test_resolve_mom_private_file_permission_denied(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, family, user, ids = _seed(pool)
    # Resolve and confirm time_range / members are populated.
    resolved = context.resolve(ids["family_id"], user, "妈妈的私人文件")
    assert ids["mama_id"] in resolved.member_ids
    # The permission decisions must include filesystem.read with DENY
    # because papa does not own mama's private space.
    filesystem_decisions = [
        d for d in resolved.permission_decisions if d.action == "filesystem.read"
    ]
    assert filesystem_decisions, "expected filesystem.read decision"
    assert filesystem_decisions[0].effect is PermissionEffect.DENY
    assert filesystem_decisions[0].reason == "private_space_not_owned"


def test_resolve_non_member_is_denied(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, _family, _user, ids = _seed(pool)
    outsider = User(id=3, username="outsider", role=Role.USER, display_name="外人")
    import pytest
    from octop.infra.errors import OctopError
    with pytest.raises(OctopError):
        context.resolve(ids["family_id"], outsider, "找孩子的照片")


def test_resolved_context_has_permission_decisions(tmp_path: Path) -> None:
    pool = _pool(tmp_path)
    context, _family, user, ids = _seed(pool)
    resolved = context.resolve(ids["family_id"], user, "今天的家庭安排")
    action_names = {d.action for d in resolved.permission_decisions}
    assert {"family.read", "family.search", "filesystem.read"} <= action_names