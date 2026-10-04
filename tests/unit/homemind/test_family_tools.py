from __future__ import annotations

import json
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager, PermissionEffect
from homemind.tools.family import build_family_tools
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


def test_family_tools_enforce_permission_and_create_task(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    families = FamilyManager(FamilyRepo(pool))
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner_member = families.repo.list_members(family.id)[0]
    tools = {
        tool.name: tool
        for tool in build_family_tools(pool, user_repo=UserRepo(pool))
    }
    config = {"configurable": {"user": user.id}}

    denied = json.loads(
        tools["family.create_task"].invoke(
            {"family_id": family.id, "title": "买牛奶"}, config=config
        )
    )
    assert denied == {"error": "permission denied for task.create"}

    families.create_permission(
        family.id,
        user,
        subject_member_id=owner_member.id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.ALLOW,
        expires_at=None,
    )
    created = json.loads(
        tools["family.create_task"].invoke(
            {"family_id": family.id, "title": "买牛奶"}, config=config
        )
    )
    listed = json.loads(
        tools["family.list_tasks"].invoke({"family_id": family.id}, config=config)
    )

    assert created["title"] == "买牛奶"
    assert created["status"] == "TODO"
    assert [task["id"] for task in listed] == [created["id"]]
    assert "family.search_assets" in tools
    assert "family.search_memory" in tools
    assert HomeMindServices.from_pool(pool).family_task_repo.get(created["id"]) is not None
