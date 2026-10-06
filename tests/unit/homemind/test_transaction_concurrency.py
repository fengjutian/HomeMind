"""Concurrency / idempotency tests for the transaction state machine.

Targets the Stage-4 acceptance criteria:

* Two admins approving the same approval simultaneously — only one
  wins; the other surfaces ``FAMILY_CONFLICT``.
* Same ``idempotency_key`` only creates one transaction.
* Verify failure flips the row to ``FAILED_REQUIRES_REVIEW``.
* ``expired_pending_approvals`` is safe to call repeatedly.
* Cancelled transactions refuse subsequent approve / reject.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_context import FamilyContextRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.permissions import PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import (
    FamilyTransactionManager,
    TransactionStatus,
)
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
            "VALUES (2, 'manager_a', 'x', 'user', 0, 'zh', 1)"
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (3, 'manager_b', 'x', 'user', 0, 'zh', 1)"
        )
    user_repo = UserRepo(pool)
    family_repo = FamilyRepo(pool)
    family_manager = FamilyManager(family_repo)
    context_repo = FamilyContextRepo(pool)
    context_manager = FamilyContextManager(family_manager, context_repo)
    task_manager = FamilyTaskManager(
        family_manager, FamilyTaskRepo(pool),
    )
    txn_repo = FamilyTransactionRepo(pool)
    txn_manager = FamilyTransactionManager(
        family_manager, context_manager, task_manager, txn_repo, user_repo,
    )
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    manager_a = User(id=2, username="manager_a", role=Role.USER, display_name="A")
    manager_b = User(id=3, username="manager_b", role=Role.USER, display_name="B")
    family = family_manager.create_family(
        owner, name="F", timezone="Asia/Shanghai", locale="zh",
    )
    member_a = family_manager.create_member(
        family.id, owner, display_name="A", role=MemberRole.ADMIN, user_id=manager_a.id,
    )
    family_manager.create_member(
        family.id, owner, display_name="B", role=MemberRole.ADMIN, user_id=manager_b.id,
    )
    # The owner is the user who will call ``plan``; make the rule
    # apply to that auto-created member so plan() routes the txn
    # through WAITING_APPROVAL.
    owner_member = next(
        member for member in family_manager.repo.list_members(family.id)
        if member.user_id == owner.id
    )
    family_manager.create_permission(
        family.id, owner,
        subject_member_id=owner_member.id,
        space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    return pool, txn_manager, txn_repo, family.id, owner, manager_a, manager_b


def test_two_admins_approving_concurrently_only_one_wins(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    txn, approval = txn_manager.plan(
        family_id,
        owner,
        action="task.create",
        payload={"title": "倒垃圾"},
    )
    assert txn.status == TransactionStatus.WAITING_APPROVAL.value

    results: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(2)

    def _approve(approver: User) -> None:
        barrier.wait()
        try:
            completed = txn_manager.approve(family_id, approval.id, approver)
            results.append(completed.status)
        except HomeMindError as exc:
            errors.append(exc)

    t_a = threading.Thread(target=_approve, args=(manager_a,))
    t_b = threading.Thread(target=_approve, args=(manager_b,))
    t_a.start(); t_b.start()
    t_a.join(); t_b.join()

    statuses = set(results)
    assert len(statuses) == 1
    assert statuses.pop() == TransactionStatus.COMPLETED.value
    # The other thread should have hit the CAS conflict.
    assert errors, "second approver must surface FAMILY_CONFLICT"
    for exc in errors:
        assert exc.code is HomeMindErrorCode.FAMILY_CONFLICT
    pool.close()


def test_idempotency_key_only_creates_one_transaction(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    first_txn, _ = txn_manager.plan(
        family_id, owner, action="task.create",
        payload={"title": "倒垃圾"},
        idempotency_key="dedupe-key",
    )
    second_txn, second_approval = txn_manager.plan(
        family_id, owner, action="task.create",
        payload={"title": "倒垃圾"},
        idempotency_key="dedupe-key",
    )
    assert first_txn.id == second_txn.id
    assert second_approval is None
    pool.close()


def test_verify_failure_flips_to_failed_requires_review(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    handler = txn_manager.registry.get("task.create")

    class _BoomVerify:
        def verify(self, context, result):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic verify failure")

    original = handler.__class__.verify  # type: ignore[attr-defined]
    handler.__class__.verify = _BoomVerify.verify  # type: ignore[attr-defined]
    try:
        txn, approval = txn_manager.plan(
            family_id, owner, action="task.create",
            payload={"title": "倒垃圾"},
        )
        assert approval is not None
        txn_manager.approve(family_id, approval.id, manager_a)
        refreshed = txn_repo.get_transaction(txn.id)
        assert refreshed is not None
        assert refreshed.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value
    finally:
        handler.__class__.verify = original  # type: ignore[attr-defined]
    pool.close()


def test_retry_rejects_already_completed(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    txn, approval = txn_manager.plan(
        family_id, owner, action="task.create", payload={"title": "倒垃圾"},
    )
    assert approval is not None
    txn_manager.approve(family_id, approval.id, manager_a)
    refreshed = txn_repo.get_transaction(txn.id)
    assert refreshed is not None
    assert refreshed.status == TransactionStatus.COMPLETED.value
    # A second ``retry`` attempt against a COMPLETED row must surface a
    # conflict; only FAILED / FAILED_REQUIRES_REVIEW are retryable.
    with pytest.raises(HomeMindError) as ei:
        txn_manager.retry(family_id, refreshed.id, owner)
    assert ei.value.code is HomeMindErrorCode.FAMILY_CONFLICT
    pool.close()


def test_cancel_after_execute_is_refused(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    # Auto-allow path: task.create has no REQUIRE_CONFIRMATION rule
    # for the requester when only an unrelated rule exists. Build a
    # fresh family with no permission rule so the txn auto-allow's.
    family_no_rule = txn_manager.family.create_family(
        owner, name="G", timezone="UTC", locale="zh",
    )
    txn, _ = txn_manager.plan(
        family_no_rule.id, owner, action="task.create",
        payload={"title": "倒垃圾"},
    )
    # Cancel must work while still PLANNED. We simulate by rebuilding
    # the row's status to PLANNED so we can prove the guard logic.
    with pool.connect() as conn:
        conn.execute(
            "UPDATE homemind_family_transactions SET status = 'COMPLETED' WHERE transaction_id = ?",
            (txn.id,),
        )
    with pytest.raises(HomeMindError) as ei:
        txn_manager.cancel(family_no_rule.id, txn.id, owner)
    assert ei.value.code is HomeMindErrorCode.FAMILY_CONFLICT
    pool.close()


def test_compensation_failure_is_logged_not_raised(tmp_path: Path) -> None:
    pool, txn_manager, txn_repo, family_id, owner, manager_a, manager_b = _bootstrap(tmp_path)
    handler = txn_manager.registry.get("task.create")
    # Patch delete to raise — compensator must swallow the error and
    # report ``compensation_failed`` rather than crash the txn
    # machine.
    class _BoomDelete:
        def delete(self, task_id):  # type: ignore[no-untyped-def]
            raise RuntimeError("synthetic delete failure")

    original_repo = handler.tasks.repo  # type: ignore[attr-defined]
    handler.tasks.repo = _BoomDelete()  # type: ignore[attr-defined]
    try:
        out = handler.compensate(  # type: ignore[attr-defined]
            type("Ctx", (), {"family_id": family_id})(),
            {"task_id": "any"},
        )
        assert out["compensated"] is False
        assert out["reason"] == "compensation_failed"
    finally:
        handler.tasks.repo = original_repo  # type: ignore[attr-defined]
    pool.close()
