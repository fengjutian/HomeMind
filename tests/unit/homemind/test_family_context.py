from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    RelationshipType,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _manager(tmp_path: Path) -> tuple[FamilyContextManager, FamilyManager, User]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'child', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    family = FamilyManager(FamilyRepo(pool))
    return FamilyContextManager(family, FamilyContextRepo(pool)), family, user


def test_event_and_memory_crud(tmp_path: Path) -> None:
    context, families, user = _manager(tmp_path)
    family = families.create_family(user, name="My Family", timezone="Asia/Shanghai", locale="zh")
    event = context.create_event(
        family.id, user, event_type="TRIP", title="日本旅行", start_at=1, end_at=2,
        location="京都", description="春季旅行", metadata={"country": "JP"},
    )
    memory = context.create_memory(
        family.id, user, subject_type="EVENT", subject_id=event.id,
        content="我们去了京都", memory_type=MemoryType.EXPERIENCE,
        importance=0.8, confidence=0.9, visibility="FAMILY",
        source_type="USER", source_id=None, expires_at=None,
    )

    assert context.list_events(family.id, user) == [event]
    assert context.search_memories(family.id, user, "京都") == [memory]
    updated = context.update_memory(
        family.id, memory.id, user, {"content": "我们去了京都和大阪"}
    )
    assert updated.content.endswith("大阪")
    context.delete_memory(family.id, memory.id, user)
    context.delete_event(family.id, event.id, user)
    assert context.search_memories(family.id, user) == []
    assert context.list_events(family.id, user) == []


def test_resolve_last_year_context(tmp_path: Path) -> None:
    context, families, user = _manager(tmp_path)
    family = families.create_family(user, name="My Family", timezone="Asia/Shanghai", locale="zh")
    child = families.create_member(
        family.id, user, display_name="孩子", role=MemberRole.CHILD, user_id=2
    )
    owner = families.repo.list_members(family.id)[0]
    families.create_relationship(
        family.id, user, from_member_id=owner.id, to_member_id=child.id,
        relationship_type=RelationshipType.PARENT,
    )
    year = datetime.now(ZoneInfo("Asia/Shanghai")).year - 1
    trip_time = int(datetime(year, 4, 1, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())
    event = context.create_event(
        family.id, user, event_type="TRIP", title="日本旅行", start_at=trip_time,
        end_at=trip_time + 86400, location="京都", description="", metadata={},
    )
    memory = context.create_memory(
        family.id, user, subject_type="FAMILY", subject_id=None,
        content="全家喜欢旅行", memory_type=MemoryType.PREFERENCE,
        importance=0.7, confidence=0.9, visibility="FAMILY", source_type="USER",
        source_id=None, expires_at=None,
    )
    families.create_permission(
        family.id, user, subject_member_id=owner.id, space_id=None,
        action="event.read", effect=PermissionEffect.ALLOW, expires_at=None,
    )

    resolved = context.resolve(family.id, user, "去年我们去了哪里？")

    assert resolved.current_member_id == owner.id
    assert resolved.event_ids == [event.id]
    assert resolved.memory_ids == [memory.id]
    assert resolved.permissions == ["event.read"]
