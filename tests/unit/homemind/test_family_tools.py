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
from octop.infra.db.repos.secrets import SecretRepo
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

    # Per the Stage 1 default-risk table, ``task.create`` defaults to ALLOW
    # for any family member, so the first call should succeed without an
    # explicit permission row.
    completed = json.loads(
        tools["family.create_task"].invoke(
            {"family_id": family.id, "title": "买牛奶"}, config=config
        )
    )
    assert completed["status"] == "COMPLETED"
    assert completed["result"]["title"] == "买牛奶"

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

    assert created["status"] == "COMPLETED"
    assert created["result"]["title"] == "买牛奶"
    assert created["result"]["status"] == "TODO"
    # Stage 1 default-risk makes task.create ALLOW by default, so we expect
    # two completed tasks (one before, one after the explicit ALLOW row).
    listed_ids = [task["id"] for task in listed]
    assert created["result"]["id"] in listed_ids
    assert len(listed_ids) == 2
    assert "family.search_assets" in tools
    assert "family.search_memory" in tools
    assert json.loads(
        tools["family.get_devices"].invoke({"family_id": family.id}, config=config)
    ) == []
    assert "family.get_device" in tools
    assert (
        HomeMindServices.from_pool(pool)
        .family_task_repo.get(created["result"]["id"])
        is not None
    )


def test_family_tools_expose_smart_home_through_approval_pipeline(tmp_path: Path) -> None:
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
    family = families.create_family(user, name="My Family", timezone="Asia/Shanghai", locale="zh")
    services = HomeMindServices.from_pool(pool)
    SecretRepo(pool).get_or_create("ha-token", lambda: b"token")
    provider = services.smart_home_repo.create_provider(
        family.id,
        kind="HOME_ASSISTANT",
        name="Home Assistant",
        base_url="http://ha.local:8123",
        secret_ref="ha-token",
        created_by=user.id,
    )
    entity = services.smart_home_repo.upsert_entity(
        family.id,
        provider_id=provider.id,
        external_entity_id="light.living_room",
        domain="light",
        name="Living room lamp",
        capabilities=["turn_on", "turn_off"],
        state={"state": "off", "attributes": {}},
    )
    tools = {tool.name: tool for tool in build_family_tools(pool, user_repo=UserRepo(pool))}
    config = {"configurable": {"user": user.id}}

    listed = json.loads(
        tools["smart_home.list_entities"].invoke({"family_id": family.id}, config=config)
    )
    fetched = json.loads(
        tools["smart_home.get_entity"].invoke(
            {"family_id": family.id, "entity_id": entity.id}, config=config
        )
    )
    commands = json.loads(
        tools["smart_home.get_commands"].invoke(
            {"family_id": family.id, "entity_id": entity.id}, config=config
        )
    )
    preview = json.loads(
        tools["smart_home.preview_command"].invoke(
            {
                "family_id": family.id,
                "entity_id": entity.id,
                "command": "turn_on",
            },
            config=config,
        )
    )
    planned = json.loads(
        tools["smart_home.command"].invoke(
            {
                "family_id": family.id,
                "entity_id": entity.id,
                "command": "turn_on",
                "idempotency_key": "agent-turn-1",
            },
            config=config,
        )
    )

    assert [row["id"] for row in listed] == [entity.id]
    assert fetched["state"]["state"] == "off"
    assert {row["name"] for row in commands} == {"turn_on", "turn_off", "toggle"}
    assert preview["entity_external_id"] == "light.living_room"
    assert preview["risk"] == "LOW"
    assert planned["status"] == "WAITING_APPROVAL"
    assert planned["approval_id"]
