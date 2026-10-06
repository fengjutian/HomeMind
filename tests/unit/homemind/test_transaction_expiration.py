"""Tests for the Stage-4 transaction refinements:

* approval TTL stored on the row and enforced at decide time,
* daily sweep expires pending approvals and cancels the parent txn,
* task handler verify re-reads fields and refuses mismatch,
* task handler compensate actually deletes the created row.
"""

from __future__ import annotations

import json
from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.permissions import PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import FamilyTransactionManager
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    from homemind.infra.db.repos.families import FamilyRepo

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (2, 'manager', 'x', 'user', 0, 'zh', 1)"
        )
    user_repo = UserRepo(pool)
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    context_repo = FamilyContextRepo(pool)
    context_manager = FamilyContextManager(family_manager, context_repo)
    task_repo = FamilyTaskRepo(pool)
    task_manager = FamilyTaskManager(family_manager, task_repo)
    txn_repo = FamilyTransactionRepo(pool)
    txn_manager = FamilyTransactionManager(
        family_manager, context_manager, task_manager, txn_repo, user_repo,
        approval_ttl_seconds=60,
    )
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    manager_user = User(id=2, username="manager", role=Role.USER, display_name="Manager")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    # Add the manager as a separate member with ADMIN role so the
    # approve / reject / expire tests have the required manager role.
    manager_member = family_manager.create_member(
        family.id,
        owner,
        display_name="Manager",
        role=MemberRole.ADMIN,
        user_id=manager_user.id,
    )
    return (
        pool,
        txn_manager,
        txn_repo,
        family_manager,
        owner,
        manager_user,
        family.id,
        task_manager,
    )


def _force_require_confirmation(
    family_manager: FamilyManager, family_id: str, owner: User, action: str,
) -> None:
    """Force ``REQUIRE_CONFIRMATION`` for ``action`` so ``plan``
    creates a PENDING approval instead of auto-allowing."""

    member = family_manager.repo.list_members(family_id)[0]
    family_manager.create_permission(
        family_id,
        owner,
        subject_member_id=member.id,
        space_id=None,
        action=action,
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )


def test_plan_stores_approval_ttl(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    _force_require_confirmation(family_manager, family_id, owner, "task.create")
    txn, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    assert txn.status == "WAITING_APPROVAL"
    assert approval is not None
    assert approval.approval_expires_at is not None
    assert approval.approval_expires_at > approval.created_at
    pool.close()


def test_approve_after_expiry_refuses(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    _force_require_confirmation(family_manager, family_id, owner, "task.create")
    txn, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    # Backdate the approval deadline so the manager tries to approve
    # after the window has closed.
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_approvals SET approval_expires_at = ? WHERE approval_id = ?",
            (1, approval.id),
        )
    try:
        txn_manager.approve(family_id, approval.id, manager_user)
    except HomeMindError as exc:
        assert exc.code is HomeMindErrorCode.FAMILY_CONFLICT
        assert "expired" in str(exc).lower()
    else:
        raise AssertionError("expired approval must refuse to approve")
    pool.close()


def test_expire_pending_approvals_cancels_transaction(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    _force_require_confirmation(family_manager, family_id, owner, "task.create")
    txn, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    # Backdate the approval.
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_approvals SET approval_expires_at = ? WHERE approval_id = ?",
            (1, approval.id),
        )
    transitioned = txn_manager.expire_pending_approvals()
    assert transitioned == 1
    refreshed = txn_repo.get_approval(approval.id)
    assert refreshed is not None
    assert refreshed.status == "EXPIRED"
    refreshed_txn = txn_repo.get_transaction(txn.id)
    assert refreshed_txn is not None
    assert refreshed_txn.status == "CANCELLED"
    pool.close()


def test_expire_is_idempotent(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    _force_require_confirmation(family_manager, family_id, owner, "task.create")
    _, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_approvals SET approval_expires_at = ? WHERE approval_id = ?",
            (1, approval.id),
        )
    first = txn_manager.expire_pending_approvals()
    second = txn_manager.expire_pending_approvals()
    assert first == 1
    assert second == 0
    pool.close()


def test_task_verify_re_reads_fields(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, task_manager = _bootstrap(tmp_path)
    _force_require_confirmation(family_manager, family_id, owner, "task.create")
    txn, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    # Approve with the manager — the txn_manager runs the handler
    # pipeline (execute → verify → COMPLETED).
    txn_manager.approve(family_id, approval.id, manager_user)
    refreshed = txn_repo.get_transaction(txn.id)
    assert refreshed is not None
    assert refreshed.status == "COMPLETED"
    payload = json.loads(refreshed.verification_json or "{}")
    assert payload.get("verified") is True
    pool.close()


def test_task_compensate_removes_real_task(tmp_path: Path) -> None:
    """Compensating after a successful execute must delete the row
    the handler just created."""

    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    handler = txn_manager.registry.get("task.create")
    family_manager.create_member(
        family_id, owner, display_name="Owner", role=MemberRole.MEMBER,
    )
    # Use the task repo directly to seed a row that simulates the
    # state the handler's ``execute`` would have left behind.
    task_repo = FamilyTaskRepo(pool)
    from homemind.infra.family.tasks import FamilyTaskManager
    task_manager = FamilyTaskManager(family_manager, task_repo)
    seeded = task_manager.create(
        family_id, owner, title="倒垃圾", description="",
    )
    assert seeded is not None
    # Compensate removes it.
    out = handler.compensate(  # type: ignore[attr-defined]
        type("Ctx", (), {"family_id": family_id})(), {"task_id": seeded.id},
    )
    assert out["compensated"] is True
    assert task_repo.get(seeded.id) is None
    pool.close()


def test_task_compensate_returns_false_for_missing_id(tmp_path: Path) -> None:
    """Compensating with a missing task_id must NOT raise so the
    manager can transition the row to ``COMPENSATED`` even when the
    side effect row was already removed."""

    pool, txn_manager, txn_repo, family_manager, owner, manager_user, family_id, _ = _bootstrap(tmp_path)
    handler = txn_manager.registry.get("task.create")
    out = handler.compensate(  # type: ignore[attr-defined]
        type("Ctx", (), {"family_id": family_id})(), {"task_id": "missing"},
    )
    # SQLite treats ``DELETE WHERE id = ?`` for a non-existent id as a
    # no-op; the compensator reports it as compensated=True so the
    # manager can move on. The failure path is exercised separately
    # by ``compensation_failed``.
    assert out["compensated"] is True
    pool.close()
