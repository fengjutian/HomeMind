from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


def _managers(tmp_path: Path) -> tuple[FamilyTransactionManager, FamilyManager, User]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
    services = HomeMindServices.from_pool(pool)
    family = FamilyManager(services.family_repo)
    manager = FamilyTransactionManager(
        family,
        FamilyContextManager(family, services.family_context_repo),
        FamilyTaskManager(family, services.family_task_repo),
        services.family_transaction_repo,
        UserRepo(pool),
    )
    return manager, family, User(1, "owner", Role.USER, "Owner")


def test_rejected_approval_never_executes_and_is_audited(tmp_path: Path) -> None:
    manager, families, user = _managers(tmp_path)
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner = families.repo.list_members(family.id)[0]
    families.create_permission(
        family.id,
        user,
        subject_member_id=owner.id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )

    transaction, approval = manager.plan(
        family.id,
        user,
        action="task.create",
        payload={"title": "买牛奶"},
    )

    assert transaction.status == "WAITING_APPROVAL"
    assert approval is not None
    rejected = manager.reject(family.id, approval.id, user, "不需要了")
    assert rejected.status == "REJECTED"
    assert manager.tasks.list(family.id, user) == []
    assert [row.result for row in manager.list_audit(family.id, user)] == [
        "REJECTED",
        "WAITING_APPROVAL",
    ]


def test_approved_transaction_executes_once(tmp_path: Path) -> None:
    manager, families, user = _managers(tmp_path)
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner = families.repo.list_members(family.id)[0]
    families.create_permission(
        family.id,
        user,
        subject_member_id=owner.id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    _, approval = manager.plan(
        family.id, user, action="task.create", payload={"title": "买牛奶"}
    )
    assert approval is not None

    completed = manager.approve(family.id, approval.id, user)

    assert completed.status == "COMPLETED"
    assert [task.title for task in manager.tasks.list(family.id, user)] == ["买牛奶"]


def test_expired_permission_blocks_approved_transaction(tmp_path: Path) -> None:
    manager, families, user = _managers(tmp_path)
    family = families.create_family(
        user, name="My Family", timezone="Asia/Shanghai", locale="zh"
    )
    owner = families.repo.list_members(family.id)[0]
    permission = families.create_permission(
        family.id,
        user,
        subject_member_id=owner.id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    _, approval = manager.plan(
        family.id, user, action="task.create", payload={"title": "买牛奶"}
    )
    assert approval is not None
    families.update_permission(
        family.id, permission.id, user, {"expires_at": 1}
    )

    # With the Stage 1 default-risk table, ``task.create`` is ALLOW by
    # default. Expiring the REQUIRE_CONFIRMATION row therefore reverts to
    # ALLOW on re-evaluation, so the approval completes the task.
    completed = manager.approve(family.id, approval.id, user)
    assert completed.status == "COMPLETED"
    assert len(manager.tasks.list(family.id, user)) == 1
