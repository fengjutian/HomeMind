from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager, TaskStatus
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def test_family_task_lifecycle(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    tasks = FamilyTaskManager(families, services.family_task_repo)
    user = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(user, name="My Family", timezone="Asia/Shanghai", locale="zh")

    task = tasks.create(family.id, user, title=" Buy milk ")
    assert task.title == "Buy milk"
    updated = tasks.update(
        family.id,
        task.id,
        user,
        {"status": TaskStatus.DONE, "description": " Finished "},
    )
    assert updated.status == "DONE"
    assert updated.description == "Finished"

    tasks.delete(family.id, task.id, user)
    assert tasks.list(family.id, user) == []
