"""Family task scheduling, dependencies and agent execution (Stage 4).

The failure modes worth pinning down are the ones a scheduler only
reveals under concurrency or a restart: two workers claiming one task, a
crashed worker stranding a task in ``IN_PROGRESS``, a dependency cycle
that would wait forever, and a recurring task that generates a year of
rows instead of one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_SCHEDULED,
    TASK_STATUS_TODO,
    TASK_STATUS_WAITING_APPROVAL,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.task_scheduler import (
    FamilyTaskScheduler,
    TaskExecutionResult,
    TaskSchedulerRunner,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Scheduler Family", timezone="Asia/Shanghai", locale="zh"
    )
    return {
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
        "spouse": User(2, "spouse", Role.USER, "Spouse"),
        "repo": services.family_task_repo,
        "scheduler": FamilyTaskScheduler(families, services.family_task_repo),
    }


def _agent_task(
    env,
    title: str = "Organise photos",
    *,
    family_id: str | None = None,
    **overrides,  # noqa: ANN001
):  # noqa: ANN201
    payload = {
        "title": title,
        "task_type": "AGENT",
        "agent_id": "agent-home",
        "schedule_at": 1000,
    }
    payload.update(overrides)
    scheduler = env["scheduler"]
    owner = env["owner"]
    target = family_id or env["family"].id
    # A task for another family must be created by a member of *that*
    # family, or the access check would reject it for the wrong reason.
    if family_id is not None:
        owner = env["spouse"]
    return scheduler.create_scheduled(target, owner, **payload)


# ------------------------------------------------------------- state machine


def test_legacy_task_created_without_scheduling_stays_manual(env) -> None:  # noqa: ANN001
    """A to-do written before Stage 4 must not become a schedulable job."""
    from homemind.infra.family.tasks import FamilyTaskManager

    tasks = FamilyTaskManager(env["families"], env["repo"])
    task = tasks.create(env["family"].id, env["owner"], title="Just a to-do")
    assert task.task_type == "MANUAL"
    assert task.schedule_at is None
    assert task.status == TASK_STATUS_TODO
    # And the scheduler never offers it up.
    assert all(t.id != task.id for t in env["repo"].list_claimable(now=2000))


def test_agent_task_requires_a_schedule(env) -> None:  # noqa: ANN001
    with pytest.raises(HomeMindError) as excinfo:
        env["scheduler"].create_scheduled(
            env["family"].id,
            env["owner"],
            title="No schedule",
            task_type="AGENT",
            agent_id="agent-home",
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_agent_task_requires_an_agent(env) -> None:  # noqa: ANN001
    with pytest.raises(HomeMindError) as excinfo:
        env["scheduler"].create_scheduled(
            env["family"].id,
            env["owner"],
            title="No agent",
            task_type="AGENT",
            schedule_at=1000,
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_unknown_task_type_is_rejected(env) -> None:  # noqa: ANN001
    with pytest.raises(HomeMindError):
        env["scheduler"].create_scheduled(
            env["family"].id,
            env["owner"],
            title="Weird",
            task_type="ROBOT",
            schedule_at=1000,
        )


def test_illegal_transition_is_refused(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["repo"].transition(task.id, to_status=TASK_STATUS_DONE, from_status=TASK_STATUS_IN_PROGRESS)
    with pytest.raises(ValueError):
        env["repo"].transition(
            task.id, to_status=TASK_STATUS_IN_PROGRESS, from_status=TASK_STATUS_DONE
        )


def test_transition_cas_lets_exactly_one_writer_win(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["repo"].claim_for_run(task.id, owner="worker-a", now=1000)
    # A second writer still believes the task is IN_PROGRESS.
    loser = env["repo"].transition(
        task.id, to_status=TASK_STATUS_DONE, from_status=TASK_STATUS_IN_PROGRESS
    )
    winner = env["repo"].transition(
        task.id, to_status=TASK_STATUS_CANCELLED, from_status=TASK_STATUS_IN_PROGRESS
    )
    assert loser is not None  # the first writer won
    assert winner is None  # the second correctly observed a stale state
    assert env["repo"].get(task.id).status == TASK_STATUS_DONE


# ------------------------------------------------------------------- leases


def test_only_one_worker_can_claim_a_task(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    first = env["repo"].claim_for_run(task.id, owner="worker-a", now=2000)
    second = env["repo"].claim_for_run(task.id, owner="worker-b", now=2000)
    assert first is not None
    assert second is None
    assert first.attempt_count == 1


def test_an_expired_lease_can_be_taken_over(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["repo"].claim_for_run(task.id, owner="dead-worker", lease_seconds=10, now=2000)
    stolen = env["repo"].claim_for_run(task.id, owner="live-worker", lease_seconds=60, now=2200)
    assert stolen is not None
    assert stolen.lease_owner == "live-worker"


def test_stale_lease_recovery_returns_a_task_to_the_queue(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    # Lease of 10s taken at t=2000 lapses at t=2010.
    env["repo"].claim_for_run(task.id, owner="dead-worker", lease_seconds=10, now=2000)
    assert env["repo"].recover_stale_leases(now=2005) == 0
    assert env["repo"].recover_stale_leases(now=2050) == 1
    recovered = env["repo"].get(task.id)
    assert recovered.status == TASK_STATUS_TODO
    assert recovered.lease_owner is None


def test_a_worker_whose_lease_was_stolen_cannot_report_success(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["repo"].claim_for_run(task.id, owner="worker-a", lease_seconds=10, now=2000)
    env["repo"].claim_for_run(task.id, owner="worker-b", lease_seconds=60, now=2200)
    result = TaskExecutionResult(status=TASK_STATUS_DONE, summary="too late")
    assert env["scheduler"].finish(task.id, owner="worker-a", result=result) is None


def test_cancelled_task_is_never_claimed_again(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["scheduler"].cancel(env["family"].id, task.id, env["owner"])
    assert env["repo"].claim_for_run(task.id, owner="worker-a", now=2000) is None
    assert env["repo"].list_claimable(now=9999) == []


# -------------------------------------------------------------- dependencies


def test_dependency_cycle_is_refused_at_write_time(env) -> None:  # noqa: ANN001
    a = _agent_task(env, title="A")
    b = _agent_task(env, title="B")
    env["scheduler"].add_dependency(env["family"].id, b.id, a.id, env["owner"])
    # b depends on a; making a depend on b would close the loop.
    with pytest.raises(HomeMindError) as excinfo:
        env["scheduler"].add_dependency(env["family"].id, a.id, b.id, env["owner"])
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID
    assert "cycle" in str(excinfo.value)


def test_self_dependency_is_refused(env) -> None:  # noqa: ANN001
    a = _agent_task(env, title="A")
    with pytest.raises(HomeMindError):
        env["scheduler"].add_dependency(env["family"].id, a.id, a.id, env["owner"])


def test_a_task_waits_for_its_predecessor(env) -> None:  # noqa: ANN001
    first = _agent_task(env, title="Sort photos")
    second = _agent_task(env, title="Make album")
    env["scheduler"].add_dependency(env["family"].id, second.id, first.id, env["owner"])

    claimed_ids = {t.id for t in env["scheduler"].claim_due(owner="worker", now=5000)}
    assert first.id in claimed_ids
    # The successor is not claimable while its predecessor is running.
    assert second.id not in claimed_ids
    assert "Sort photos" in (env["scheduler"].blocked_reason(env["repo"].get(second.id)) or "")


def test_a_task_becomes_claimable_once_its_predecessor_finishes(env) -> None:  # noqa: ANN001
    first = _agent_task(env, title="Sort photos")
    second = _agent_task(env, title="Make album")
    env["scheduler"].add_dependency(env["family"].id, second.id, first.id, env["owner"])

    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        first.id,
        owner="worker",
        result=TaskExecutionResult(status=TASK_STATUS_DONE, summary="sorted"),
    )
    claimed_ids = {t.id for t in env["scheduler"].claim_due(owner="worker", now=5000)}
    assert second.id in claimed_ids


def test_a_failed_predecessor_never_unblocks_its_successor(env) -> None:  # noqa: ANN001
    first = _agent_task(env, title="Sort photos", max_attempts=1)
    second = _agent_task(env, title="Make album")
    env["scheduler"].add_dependency(env["family"].id, second.id, first.id, env["owner"])

    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        first.id,
        owner="worker",
        result=TaskExecutionResult(status=TASK_STATUS_FAILED, error="no disk space"),
    )
    claimed_ids = {t.id for t in env["scheduler"].claim_due(owner="worker", now=5000)}
    assert second.id not in claimed_ids


def test_duplicate_dependency_edge_is_a_no_op(env) -> None:  # noqa: ANN001
    a = _agent_task(env, title="A")
    b = _agent_task(env, title="B")
    env["scheduler"].add_dependency(env["family"].id, b.id, a.id, env["owner"])
    env["scheduler"].add_dependency(env["family"].id, b.id, a.id, env["owner"])
    assert len(env["repo"].list_dependencies(b.id)) == 1


def test_dependency_cannot_cross_families(env) -> None:  # noqa: ANN001
    other = env["families"].create_family(
        env["spouse"], name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    mine = _agent_task(env, title="Mine")
    theirs = _agent_task(env, title="Theirs", family_id=other.id)
    with pytest.raises(HomeMindError) as excinfo:
        env["scheduler"].add_dependency(env["family"].id, mine.id, theirs.id, env["owner"])
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_NOT_FOUND


# ------------------------------------------------------------- concurrency


def test_only_one_task_per_family_runs_at_a_time(env) -> None:  # noqa: ANN001
    _agent_task(env, title="Task A")
    _agent_task(env, title="Task B")
    claimed = env["scheduler"].claim_due(owner="worker", now=5000)
    assert len(claimed) == 1


def test_different_families_run_in_parallel(env) -> None:  # noqa: ANN001
    other = env["families"].create_family(
        env["spouse"], name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    _agent_task(env, title="Mine A")
    _agent_task(env, title="Mine B")
    _agent_task(env, title="Theirs A", family_id=other.id, agent_id="agent-other")
    claimed = env["scheduler"].claim_due(owner="worker", now=5000)
    # The per-family cap stops the second of ours; the per-agent cap
    # also applies, so the neighbour needs its own agent to run at all.
    assert {t.family_id for t in claimed} == {env["family"].id, other.id}


def test_one_agent_runs_one_task_at_a_time_across_families(env) -> None:  # noqa: ANN001
    other = env["families"].create_family(
        env["spouse"], name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    _agent_task(env, title="Mine")
    _agent_task(env, title="Theirs", family_id=other.id)
    claimed = env["scheduler"].claim_due(owner="worker", now=5000)
    # Both tasks name the same agent, so the second one waits rather
    # than putting two turns of one agent in flight at once.
    assert len(claimed) == 1


def test_priority_orders_the_claim_order(env) -> None:  # noqa: ANN001
    env["scheduler"].create_scheduled(
        env["family"].id,
        env["owner"],
        title="Low priority",
        task_type="DEVICE",
        schedule_at=1000,
        priority=0,
    )
    high = env["scheduler"].create_scheduled(
        env["family"].id,
        env["owner"],
        title="High priority",
        task_type="DEVICE",
        schedule_at=1000,
        priority=9,
    )
    claimed = env["scheduler"].claim_due(owner="worker", now=5000)
    assert claimed[0].id == high.id


def test_a_task_is_not_claimed_before_its_schedule(env) -> None:  # noqa: ANN001
    _agent_task(env, title="Later", schedule_at=9000)
    assert env["scheduler"].claim_due(owner="worker", now=5000) == []


def test_an_exhausted_task_is_not_claimed_again(env) -> None:  # noqa: ANN001
    task = _agent_task(env, max_attempts=1)
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id,
        owner="worker",
        result=TaskExecutionResult(status=TASK_STATUS_FAILED, error="boom"),
    )
    assert env["repo"].get(task.id).status == TASK_STATUS_FAILED
    assert env["scheduler"].claim_due(owner="worker", now=5000) == []


def test_a_failed_task_retries_up_to_its_limit(env) -> None:  # noqa: ANN001
    task = _agent_task(env, max_attempts=2)
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id, owner="worker", result=TaskExecutionResult(status=TASK_STATUS_FAILED, error="1")
    )
    assert env["repo"].get(task.id).status == TASK_STATUS_TODO
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id, owner="worker", result=TaskExecutionResult(status=TASK_STATUS_FAILED, error="2")
    )
    assert env["repo"].get(task.id).status == TASK_STATUS_FAILED


# ----------------------------------------------------------------- approval


def test_an_awaiting_approval_task_is_not_reclaimed(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id,
        owner="worker",
        result=TaskExecutionResult(
            status=TASK_STATUS_WAITING_APPROVAL,
            summary="wants to delete 40 files",
            transaction_id="txn-9",
        ),
    )
    assert env["repo"].get(task.id).status == TASK_STATUS_WAITING_APPROVAL
    assert env["scheduler"].claim_due(owner="worker", now=5000) == []


def test_approval_returns_the_task_to_the_queue(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id,
        owner="worker",
        result=TaskExecutionResult(status=TASK_STATUS_WAITING_APPROVAL, transaction_id="txn-9"),
    )
    resumed = env["scheduler"].resume_after_approval(
        env["family"].id, task.id, transaction_id="txn-9", user=env["owner"]
    )
    assert resumed.status == TASK_STATUS_TODO
    assert resumed.transaction_id == "txn-9"
    assert [t.id for t in env["scheduler"].claim_due(owner="worker", now=5000)] == [task.id]


# -------------------------------------------------------------- recurrence


def test_a_finished_recurring_task_produces_exactly_one_next_run(env) -> None:  # noqa: ANN001
    task = env["scheduler"].create_scheduled(
        env["family"].id,
        env["owner"],
        title="Daily tidy",
        task_type="DEVICE",
        schedule_at=1000,
        recurrence_rule="FREQ=DAILY",
    )
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id, owner="worker", result=TaskExecutionResult(status=TASK_STATUS_DONE)
    )
    children = env["repo"].list(env["family"].id, status=TASK_STATUS_SCHEDULED)
    assert len(children) == 1
    assert children[0].parent_task_id == task.id
    assert children[0].recurrence_rule == "FREQ=DAILY"
    assert children[0].schedule_at is not None and children[0].schedule_at > 1000


def test_a_non_recurring_task_produces_no_next_run(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["scheduler"].claim_due(owner="worker", now=5000)
    env["scheduler"].finish(
        task.id, owner="worker", result=TaskExecutionResult(status=TASK_STATUS_DONE)
    )
    assert env["repo"].list(env["family"].id, status=TASK_STATUS_SCHEDULED) == []


def test_natural_language_recurrence_is_refused(env) -> None:  # noqa: ANN001
    with pytest.raises(HomeMindError):
        env["scheduler"].create_scheduled(
            env["family"].id,
            env["owner"],
            title="Bad rule",
            task_type="DEVICE",
            schedule_at=1000,
            recurrence_rule="every other tuesday",
        )


# ------------------------------------------------------------------ runner


@pytest.mark.asyncio
async def test_runner_runs_a_claimed_task_and_records_the_attempt(env) -> None:  # noqa: ANN001
    ran: list[str] = []

    async def executor(task):  # noqa: ANN001, ANN202
        ran.append(task.title)
        return TaskExecutionResult(status=TASK_STATUS_DONE, summary="done")

    task = _agent_task(env, title="Sort the hall")
    runner = TaskSchedulerRunner(
        scheduler=env["scheduler"], executor=executor, worker_id="test-runner"
    )
    assert await runner.drain_once() == 1
    assert ran == ["Sort the hall"]
    assert env["repo"].get(task.id).status == TASK_STATUS_DONE
    attempts = env["repo"].list_attempts(task.id)
    assert len(attempts) == 1
    assert attempts[0].status == TASK_STATUS_DONE
    assert attempts[0].summary == "done"


@pytest.mark.asyncio
async def test_runner_records_an_executor_crash_as_a_failure(env) -> None:  # noqa: ANN001
    async def executor(task):  # noqa: ANN001, ANN202
        raise RuntimeError("agent exploded")

    task = _agent_task(env, max_attempts=1)
    runner = TaskSchedulerRunner(
        scheduler=env["scheduler"], executor=executor, worker_id="test-runner"
    )
    assert await runner.drain_once() == 1
    stored = env["repo"].get(task.id)
    assert stored.status == TASK_STATUS_FAILED
    assert "agent exploded" in (stored.last_error or "")
    assert env["repo"].list_attempts(task.id)[0].status == TASK_STATUS_FAILED


@pytest.mark.asyncio
async def test_runner_reports_a_missing_executor_instead_of_idling(env) -> None:  # noqa: ANN001
    task = _agent_task(env, max_attempts=1)
    runner = TaskSchedulerRunner(scheduler=env["scheduler"], worker_id="test-runner")
    assert await runner.drain_once() == 1
    assert env["repo"].get(task.id).status == TASK_STATUS_FAILED


@pytest.mark.asyncio
async def test_runner_does_nothing_when_nothing_is_due(env) -> None:  # noqa: ANN001
    # Far enough ahead that "now" cannot reach it during this test.
    _agent_task(env, schedule_at=4102444800)
    runner = TaskSchedulerRunner(
        scheduler=env["scheduler"],
        executor=lambda task: None,
        worker_id="test-runner",
    )
    assert await runner.drain_once() == 0


@pytest.mark.asyncio
async def test_runner_start_recovers_a_dead_workers_task(env) -> None:  # noqa: ANN001
    """Booting hands back a task whose worker died mid-run.

    ``start()`` also spawns the poll loop, so this asserts on the
    recovered *state* and stops. Racing the loop with a second
    ``drain_once`` from the test would just measure which of two
    identical workers won the claim.
    """
    task = _agent_task(env)
    env["repo"].claim_for_run(task.id, owner="dead-worker", lease_seconds=1, now=2000)
    assert env["repo"].get(task.id).status == TASK_STATUS_IN_PROGRESS

    runner = TaskSchedulerRunner(
        scheduler=env["scheduler"],
        poll_interval_seconds=3600,
        worker_id="new-worker",
    )
    await runner.start()
    try:
        # The boot sweep runs synchronously inside start(); give the
        # spawned loop no reason to have claimed anything yet.
        recovered = env["repo"].get(task.id)
        assert recovered.status in {TASK_STATUS_TODO, TASK_STATUS_IN_PROGRESS}
        if recovered.status == TASK_STATUS_IN_PROGRESS:
            # The loop beat us to it; either outcome is valid, but the
            # lease must never still belong to the dead worker.
            assert recovered.lease_owner != "dead-worker"
        else:
            assert recovered.lease_owner is None
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_recovered_task_runs_on_the_next_sweep(env) -> None:  # noqa: ANN001
    task = _agent_task(env)
    env["repo"].claim_for_run(task.id, owner="dead-worker", lease_seconds=1, now=2000)
    assert env["scheduler"].recover_stale() == 1

    async def executor(row):  # noqa: ANN001, ANN202
        return TaskExecutionResult(status=TASK_STATUS_DONE, summary="recovered")

    runner = TaskSchedulerRunner(
        scheduler=env["scheduler"], executor=executor, worker_id="new-worker"
    )
    assert await runner.drain_once() == 1
    assert env["repo"].get(task.id).status == TASK_STATUS_DONE


@pytest.mark.asyncio
async def test_runner_stop_is_idempotent(env) -> None:  # noqa: ANN001
    runner = TaskSchedulerRunner(scheduler=env["scheduler"], worker_id="idle")
    await runner.start()
    await runner.stop()
    await runner.stop()


# ------------------------------------------------------------- permissions


def test_an_outsider_cannot_schedule_into_a_family(env) -> None:  # noqa: ANN001
    from octop.infra.errors import ErrorCode, OctopError

    stranger = User(99, "stranger", Role.USER, "Stranger")
    with pytest.raises(OctopError) as excinfo:
        env["scheduler"].create_scheduled(
            env["family"].id,
            stranger,
            title="Not yours",
            task_type="DEVICE",
            schedule_at=1000,
        )
    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_a_blocked_task_is_reported_with_a_reason(env) -> None:  # noqa: ANN001
    task = _agent_task(env, title="Waiting")
    stored = env["repo"].get(task.id)
    # The CAS must name the status the task is actually in.
    assert (
        env["repo"].transition(
            task.id,
            to_status=TASK_STATUS_BLOCKED,
            from_status=stored.status,
            error="upstream died",
        )
        is not None
    )
    assert env["scheduler"].blocked_reason(env["repo"].get(task.id)) == "upstream died"
