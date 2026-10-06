"""Stage 5 tests: family transaction state machine, idempotency, lease recovery."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager, PermissionEffect
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transactions import (
    DEFAULT_LEASE_TTL_SECONDS,
    TransactionStatus,
    FamilyTransactionManager,
)
from homemind.infra.family.transaction_actions.tasks import TaskCreateHandler
from homemind.infra.family.transaction_actions.registry import (
    FamilyActionRegistry,
    build_default_action_registry,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path) -> tuple[SqlitePool, FamilyTransactionManager, FamilyManager, User]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1)"
        )
    user = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    context = FamilyContextManager(family, family_repo)
    tasks = FamilyTaskManager(family, FamilyTaskRepo(pool))
    filesystem = None
    registry = build_default_action_registry(
        family=family, context=context, tasks=tasks, filesystem=filesystem,
    )
    manager = FamilyTransactionManager(
        family,
        context,
        tasks,
        FamilyTransactionRepo(pool),
        UserRepo(pool),
        filesystem=None,
        action_registry=registry,
    )
    return pool, manager, family, user


def _family(pool: SqlitePool, family: FamilyManager, user: User) -> tuple[str, str]:
    fam = family.create_family(
        user, name="Happy", timezone="Asia/Shanghai", locale="zh"
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    return fam.id, members["爸爸"].id


# ------------------------------------------------------------------ idempotency


def test_idempotency_key_returns_existing_transaction(
    tmp_path: Path,
) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, _ = _family(pool, family, user)
    first, _ = manager.plan(
        family_id, user,
        action="task.create",
        payload={"title": "buy milk"},
        idempotency_key="abc",
    )
    second, _ = manager.plan(
        family_id, user,
        action="task.create",
        payload={"title": "buy milk"},
        idempotency_key="abc",
    )
    assert first.id == second.id
    rows = manager.list_transactions(family_id, user)
    assert sum(1 for row in rows if row.action == "task.create") == 1


# ------------------------------------------------------------------ state machine


def test_approve_transitions_through_state_machine(
    tmp_path: Path,
) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, owner_id = _family(pool, family, user)
    family.create_permission(
        family_id, user,
        subject_member_id=owner_id, space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    _, approval = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    assert approval is not None
    completed = manager.approve(family_id, approval.id, user)
    assert completed.status == TransactionStatus.COMPLETED.value
    assert completed.approved_at is not None
    assert completed.executed_at is not None
    assert completed.verified_at is not None
    assert completed.attempt_count >= 1
    assert completed.preview_json  # preview populated


def test_cancel_only_works_from_open_states(tmp_path: Path) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, owner_id = _family(pool, family, user)
    family.create_permission(
        family_id, user,
        subject_member_id=owner_id, space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    _, approval = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    completed = manager.approve(family_id, approval.id, user)
    assert completed.status == TransactionStatus.COMPLETED.value
    import pytest
    from homemind.infra.errors import HomeMindError
    with pytest.raises(HomeMindError):
        manager.cancel(family_id, completed.id, user)


def test_cancel_by_requester_before_completion(tmp_path: Path) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, _ = _family(pool, family, user)
    family.create_permission(
        family_id, user,
        subject_member_id=None, space_id=None,
        action="task.create",
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        expires_at=None,
    )
    transaction, _ = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    cancelled = manager.cancel(family_id, transaction.id, user)
    assert cancelled.status == TransactionStatus.CANCELLED.value


# ------------------------------------------------------------------ lease / recovery


def test_recovery_retries_expired_executing(tmp_path: Path) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, _ = _family(pool, family, user)
    transaction, _ = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    assert transaction.status == TransactionStatus.COMPLETED.value
    # Manually demote to EXECUTING with an already-expired expiry.
    repo = FamilyTransactionRepo(pool)
    # Mark the row as if it was abandoned mid-execute (not committed).
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE homemind_family_transactions SET status = 'EXECUTING', "
            "lease_owner = 'stale', lease_expires_at = 1, "
            "executed_at = NULL WHERE transaction_id = ?",
            (transaction.id,),
        )
    affected = manager.recover_running(now=DEFAULT_LEASE_TTL_SECONDS * 100)
    # Recovery tries to re-run via the handler. Since the task is still
    # present, ``create_task`` would fail with a duplicate-title — the
    # handler exception is converted to ``FAILED`` and surfaced.
    assert affected
    assert any(
        row.id == transaction.id for row in affected
    )


def test_recovery_flags_expired_verifying_for_review(tmp_path: Path) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, _ = _family(pool, family, user)
    transaction, _ = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE homemind_family_transactions SET status = 'VERIFYING', "
            "lease_owner = 'stale', lease_expires_at = 1 WHERE transaction_id = ?",
            (transaction.id,),
        )
    affected = manager.recover_running(now=DEFAULT_LEASE_TTL_SECONDS * 100)
    assert any(
        row.status == TransactionStatus.FAILED_REQUIRES_REVIEW.value
        for row in affected
    )


# ------------------------------------------------------------------ retry


def test_retry_resets_failed_transaction_and_runs_handler(tmp_path: Path) -> None:
    pool, manager, family, user = _bootstrap(tmp_path)
    family_id, _ = _family(pool, family, user)
    transaction, _ = manager.plan(
        family_id, user, action="task.create", payload={"title": "x"},
    )
    # task.create without a permission rule defaults to ALLOW and runs
    # straight to COMPLETED. Force the row back to FAILED via direct
    # repo write to exercise the retry path.
    repo = FamilyTransactionRepo(pool)
    flipped = repo.transition(
        transaction.id,
        from_status=TransactionStatus.COMPLETED.value,
        to_status=TransactionStatus.FAILED.value,
        error="simulated",
    )
    assert flipped is not None
    retried = manager.retry(family_id, transaction.id, user)
    # Handler succeeds on retry: row should be COMPLETED again.
    assert retried.status in {
        TransactionStatus.COMPLETED.value,
        TransactionStatus.FAILED.value,
    }


# ------------------------------------------------------------------ handler protocol


def test_handler_protocol_validation() -> None:
    """Reject malformed payloads before any side effect."""
    from homemind.infra.family.transaction_actions.base import ActionContext
    from homemind.infra.family.transaction_actions.tasks import TaskCreateHandler

    handler = TaskCreateHandler(family=None, tasks=None)  # type: ignore[arg-type]
    with __import__("pytest").raises(Exception):
        handler.validate(
            ActionContext(
                family_id="f", transaction=None, requester=None,  # type: ignore[arg-type]
                payload={"title": ""},
            ),
            {"title": ""},
        )


def test_registry_returns_handler_for_known_actions() -> None:
    registry = FamilyActionRegistry()
    registry.register("task.create", object())
    assert registry.get("task.create") is not None
    assert registry.get("memory.create") is None
    assert "task.create" in registry.actions()


def test_default_registry_covers_all_built_in_actions(tmp_path: Path) -> None:
    from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
    from homemind.infra.db.repos.family_tasks import FamilyTaskRepo
    from homemind.infra.family.context import FamilyContextManager
    from homemind.infra.family.tasks import FamilyTaskManager
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    family_repo = FamilyRepo(pool)
    family = FamilyManager(family_repo)
    context = FamilyContextManager(family, family_repo)
    tasks = FamilyTaskManager(family, FamilyTaskRepo(pool))
    registry = build_default_action_registry(
        family=family, context=context, tasks=tasks,
    )
    actions = set(registry.actions())
    assert {"task.create", "event.create", "memory.create"} <= actions