"""Family task scheduling and agent execution (Stage 4).

A Family Task stops being a to-do line and becomes a unit of work the
system can run: it can be scheduled, it can recur, it can be owned by an
agent, it can declare what it depends on, and a worker can claim it
exactly once through a database lease.

Three rules shape everything here:

* **The database is the scheduler.** Claims are compare-and-swap on
  ``status``/``lease_expires_at``. Running two schedulers is safe — the
  loser simply gets no task — and restarting the process loses nothing,
  because a task's next run lives in a row, not in a timer.
* **A dependency is a row, not a JSON blob.** "Is B blocked?" is one
  indexed query, and a cycle is detectable at write time rather than
  discovered when a task waits forever.
* **An agent proposes; a transaction disposes.** When a scheduled agent
  task reaches a high-risk step it does not act. It opens a Family
  Transaction and parks in ``WAITING_APPROVAL``, so a human approves the
  dangerous part while the agent keeps the bookkeeping.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homemind.infra.db.repos.family_calendars import FamilyCalendarEventRow
from homemind.infra.db.repos.family_tasks import (
    TASK_STATUS_BLOCKED,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_DONE,
    TASK_STATUS_FAILED,
    TASK_STATUS_IN_PROGRESS,
    TASK_STATUS_SCHEDULED,
    TASK_STATUS_TODO,
    TASK_STATUS_WAITING_APPROVAL,
    TASK_TYPE_AGENT,
    TASK_TYPE_MANUAL,
    TASK_TYPES,
    FamilyTaskRepo,
    FamilyTaskRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.calendar import expand_occurrences, normalize_recurrence_rule
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import inc as _hm_inc
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_SECONDS = 30.0

#: At most this many agent tasks of one family run at once. A family
#: asking an agent to organise their photos should not have four copies
#: of that agent reorganising them simultaneously.
MAX_CONCURRENT_PER_FAMILY = 1
MAX_CONCURRENT_PER_AGENT = 1


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


@dataclass(frozen=True)
class TaskExecutionResult:
    """What one agent run produced.

    ``transaction_id`` set means the agent hit something it may not do
    on its own; the scheduler parks the task in ``WAITING_APPROVAL``
    instead of finishing it.
    """

    status: str
    summary: str = ""
    error: str | None = None
    transaction_id: str | None = None


class FamilyTaskScheduler:
    """Create, schedule and advance schedulable tasks."""

    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyTaskRepo,
        *,
        agent_resolver: Callable[[str], Any] | None = None,
        transaction_opener: Callable[..., str] | None = None,
    ) -> None:
        self.family = family
        self.repo = repo
        # Both optional so the scheduler is testable and usable in a
        # deployment without agents; a task that needs them and has
        # none fails loudly rather than silently doing nothing.
        self._agent_resolver = agent_resolver
        self._transaction_opener = transaction_opener

    # ------------------------------------------------------------- creation

    def create_scheduled(
        self,
        family_id: str,
        user: User,
        *,
        title: str,
        task_type: str = TASK_TYPE_AGENT,
        description: str = "",
        schedule_at: int | None = None,
        recurrence_rule: str | None = None,
        agent_id: str | None = None,
        parent_task_id: str | None = None,
        priority: int = 0,
        max_attempts: int = 3,
        depends_on: list[str] | None = None,
        assigned_member_id: str | None = None,
    ) -> FamilyTaskRow:
        """Create a task the scheduler may claim.

        Validation happens here rather than at claim time, so a family
        does not set up a recurring job that can never run.
        """
        self.family.require_access(family_id, user)
        if task_type not in TASK_TYPES:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "unknown task type")
        if task_type != TASK_TYPE_MANUAL and schedule_at is None and recurrence_rule is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "an agent or device task needs a schedule or a recurrence rule",
            )
        if task_type == TASK_TYPE_AGENT and not agent_id:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "an agent task needs an agent")
        rule = normalize_recurrence_rule(recurrence_rule)
        if parent_task_id is not None:
            self._assert_task(family_id, parent_task_id)

        task = self.repo.create(
            family_id,
            title=title,
            description=description,
            assigned_member_id=assigned_member_id,
            due_at=schedule_at,
            created_by=user.id,
        )
        self.repo.update(
            task.id,
            task_type=task_type,
            priority=priority,
            schedule_at=schedule_at,
            recurrence_rule=rule,
            agent_id=agent_id,
            parent_task_id=parent_task_id,
            max_attempts=max_attempts,
            status=TASK_STATUS_SCHEDULED if (schedule_at is not None or rule) else TASK_STATUS_TODO,
        )
        for dependency in depends_on or []:
            self.add_dependency(family_id, task.id, dependency, user)
        refreshed = self.repo.get(task.id)
        assert refreshed is not None
        return refreshed

    def schedule(
        self, family_id: str, task_id: str, user: User, *, schedule_at: int | None
    ) -> FamilyTaskRow:
        self.family.require_access(family_id, user)
        task = self._assert_task(family_id, task_id)
        if task.status in {TASK_STATUS_DONE, TASK_STATUS_CANCELLED}:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "a finished task cannot be rescheduled"
            )
        self.repo.reschedule(task.id, schedule_at=schedule_at)
        refreshed = self.repo.transition(
            task.id, to_status=TASK_STATUS_SCHEDULED, from_status=task.status
        )
        if refreshed is None:
            raise HomeMindError(HomeMindErrorCode.FAMILY_CONFLICT, "task changed while scheduling")
        return refreshed

    def cancel(self, family_id: str, task_id: str, user: User) -> FamilyTaskRow:
        """Cancel a task.

        Any non-terminal state may be cancelled. After this the task is
        never claimable again — the cancelled status is what the
        scheduler's query filters on, not a best-effort check at claim
        time.
        """
        self.family.require_access(family_id, user)
        task = self._assert_task(family_id, task_id)
        updated = self.repo.transition(
            task.id, to_status=TASK_STATUS_CANCELLED, from_status=task.status
        )
        if updated is None:
            raise HomeMindError(HomeMindErrorCode.FAMILY_CONFLICT, "task changed while cancelling")
        return updated

    def retry(self, family_id: str, task_id: str, user: User) -> FamilyTaskRow:
        """Put a failed task back in the queue."""
        self.family.require_access(family_id, user)
        task = self._assert_task(family_id, task_id)
        if task.status not in {TASK_STATUS_FAILED, TASK_STATUS_BLOCKED}:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "only a failed or blocked task can be retried"
            )
        updated = self.repo.transition(task.id, to_status=TASK_STATUS_TODO, from_status=task.status)
        if updated is None:
            raise HomeMindError(HomeMindErrorCode.FAMILY_CONFLICT, "task changed while retrying")
        return updated

    # ----------------------------------------------------------- dependencies

    def add_dependency(
        self, family_id: str, task_id: str, depends_on_task_id: str, user: User
    ) -> None:
        """Declare that ``task_id`` waits on ``depends_on_task_id``.

        Refuses a cycle at write time. Detecting it later would mean a
        task sitting in ``BLOCKED`` with no explanation, which is the
        worst possible failure mode for a scheduler.
        """
        self.family.require_access(family_id, user)
        self._assert_task(family_id, task_id)
        self._assert_task(family_id, depends_on_task_id)
        if task_id == depends_on_task_id:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "a task cannot depend on itself")
        if self._reaches(depends_on_task_id, task_id):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "that dependency would create a cycle",
            )
        self.repo.add_dependency(task_id, depends_on_task_id)

    def remove_dependency(
        self, family_id: str, task_id: str, depends_on_task_id: str, user: User
    ) -> bool:
        self.family.require_access(family_id, user)
        self._assert_task(family_id, task_id)
        return self.repo.remove_dependency(task_id, depends_on_task_id)

    def _reaches(self, start: str, target: str) -> bool:
        """True when ``target`` is already downstream of ``start``."""
        seen: set[str] = set()
        stack = [start]
        while stack:
            current = stack.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            stack.extend(edge.depends_on_task_id for edge in self.repo.list_dependencies(current))
        return False

    # -------------------------------------------------------------- claiming

    def claim_due(
        self, *, owner: str, now: int | None = None, limit: int = 20
    ) -> list[FamilyTaskRow]:
        """Claim runnable tasks whose dependencies are satisfied.

        Concurrency is capped per family and per agent: without that, a
        family with three identical recurring tasks would fire three
        copies of the same agent job at the same instant.
        """
        claimed: list[FamilyTaskRow] = []
        busy_families: set[str] = set()
        busy_agents: set[str] = set()
        for candidate in self.repo.list_claimable(now=now, limit=limit * 3):
            if len(claimed) >= limit:
                break
            if candidate.family_id in busy_families:
                continue
            if candidate.agent_id and candidate.agent_id in busy_agents:
                continue
            if not self.dependencies_satisfied(candidate):
                continue
            task = self.repo.claim_for_run(candidate.id, owner=owner, now=now)
            if task is None:
                # Another scheduler won the CAS; skip and try the next.
                continue
            claimed.append(task)
            busy_families.add(task.family_id)
            if task.agent_id:
                busy_agents.add(task.agent_id)
        return claimed

    def dependencies_satisfied(self, task: FamilyTaskRow) -> bool:
        """True when every predecessor reached ``DONE``.

        A predecessor that ended ``FAILED`` or ``CANCELLED`` never
        satisfies the dependency: the downstream work is meaningless
        without it, and pretending otherwise would run a task whose
        input never existed.
        """
        for edge in self.repo.list_dependencies(task.id):
            predecessor = self.repo.get(edge.depends_on_task_id)
            if predecessor is None:
                # The predecessor is gone; the edge cascades away with
                # it, so treat it as no longer blocking.
                continue
            if predecessor.status != TASK_STATUS_DONE:
                return False
        return True

    def blocked_reason(self, task: FamilyTaskRow) -> str | None:
        """Human-readable reason a task cannot run, or ``None``."""
        if task.status == TASK_STATUS_BLOCKED:
            return task.last_error or "a dependency did not finish"
        if self.dependencies_satisfied(task):
            return None
        names = []
        for edge in self.repo.list_dependencies(task.id):
            predecessor = self.repo.get(edge.depends_on_task_id)
            if predecessor is not None and predecessor.status != TASK_STATUS_DONE:
                names.append(predecessor.title)
        return "waiting on: " + ", ".join(names) if names else "waiting on an upstream task"

    def recover_stale(self, *, now: int | None = None) -> int:
        return self.repo.recover_stale_leases(now=now)

    # ------------------------------------------------------------ completion

    def finish(
        self, task_id: str, *, owner: str, result: TaskExecutionResult
    ) -> FamilyTaskRow | None:
        """Close out a claimed task.

        The lease is checked on every write: a worker whose lease was
        stolen while it was running must not be able to report success
        over the new owner's work.
        """
        task = self.repo.get(task_id)
        if task is None or task.lease_owner != owner:
            return None
        if result.status == TASK_STATUS_WAITING_APPROVAL and result.transaction_id:
            return self.repo.transition(
                task_id,
                to_status=TASK_STATUS_WAITING_APPROVAL,
                from_status=TASK_STATUS_IN_PROGRESS,
                error=result.error,
                result_summary=result.summary,
            )
        if result.status == TASK_STATUS_FAILED:
            next_status = (
                TASK_STATUS_TODO if task.attempt_count < task.max_attempts else TASK_STATUS_FAILED
            )
            return self.repo.transition(
                task_id,
                to_status=next_status,
                from_status=TASK_STATUS_IN_PROGRESS,
                error=result.error,
                result_summary=result.summary,
            )
        updated = self.repo.transition(
            task_id,
            to_status=TASK_STATUS_DONE,
            from_status=TASK_STATUS_IN_PROGRESS,
            result_summary=result.summary,
        )
        if updated is not None:
            self._spawn_next_occurrence(updated)
        return updated

    def _spawn_next_occurrence(self, task: FamilyTaskRow) -> None:
        """Create the next run of a recurring task, if any.

        Only one occurrence is ever generated: a daily task that ran
        today produces tomorrow's row, and tomorrow's run produces the
        day after. Pre-generating a year of rows would make "pause this
        recurring task" impossible.
        """
        if not task.recurrence_rule:
            return
        try:
            probe = _RecurrenceProbe(
                title=task.title,
                starts_at=task.schedule_at or task.due_at or task.created_at,
                timezone="UTC",
                recurrence_rule=task.recurrence_rule,
            )
            upcoming = expand_occurrences(
                probe.as_event(task.family_id, task.id),
                window_start=task.completed_at or task.updated_at,
                window_end=(task.completed_at or task.updated_at) + 366 * 86400,
                remaining=2,
            )
        except Exception:  # noqa: BLE001 — a bad stored rule must not crash the scheduler
            logger.exception("TaskScheduler: task %s has an unusable recurrence rule", task.id)
            return
        if not upcoming:
            return
        nxt = upcoming[0]
        child = self.repo.create(
            task.family_id,
            title=task.title,
            description=task.description,
            assigned_member_id=task.assigned_member_id,
            due_at=nxt.starts_at,
            created_by=task.created_by,
        )
        self.repo.update(
            child.id,
            task_type=task.task_type,
            priority=task.priority,
            schedule_at=nxt.starts_at,
            recurrence_rule=task.recurrence_rule,
            agent_id=task.agent_id,
            parent_task_id=task.id,
            max_attempts=task.max_attempts,
            status=TASK_STATUS_SCHEDULED,
        )
        logger.info(
            "TaskScheduler: recurring task %s produced next run %s at %s",
            task.id,
            child.id,
            nxt.starts_at,
        )

    def resume_after_approval(
        self, family_id: str, task_id: str, *, transaction_id: str, user: User
    ) -> FamilyTaskRow:
        """Return an approved task to the queue with its new transaction."""
        self.family.require_access(family_id, user)
        task = self._assert_task(family_id, task_id)
        self.repo.update(task.id, transaction_id=transaction_id)
        updated = self.repo.transition(
            task.id, to_status=TASK_STATUS_TODO, from_status=TASK_STATUS_WAITING_APPROVAL
        )
        if updated is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT, "task is no longer waiting for approval"
            )
        return updated

    # --------------------------------------------------------------- helpers

    def _assert_task(self, family_id: str, task_id: str) -> FamilyTaskRow:
        task = self.repo.get(task_id)
        if task is None or task.family_id != family_id:
            raise HomeMindError(HomeMindErrorCode.FAMILY_NOT_FOUND, "family task not found")
        return task


@dataclass(frozen=True)
class _RecurrenceProbe:
    """Minimal event-shaped stand-in for recurrence expansion.

    ``expand_occurrences`` works off a calendar event row; a recurring
    *task* has no calendar. This carries just the fields the expander
    reads, so the two features cannot drift apart in how they expand a
    rule.
    """

    title: str
    starts_at: int
    timezone: str
    recurrence_rule: str

    def as_event(self, family_id: str, event_id: str) -> FamilyCalendarEventRow:
        """Build the row shape ``expand_occurrences`` expects.

        Constructed field by field rather than from a dict so the
        compiler can check every value against its declared type.
        """
        from homemind.infra.db.repos.family_calendars import FamilyCalendarEventRow

        return FamilyCalendarEventRow(
            id=event_id,
            pk=0,
            calendar_id="",
            family_id=family_id,
            title=self.title,
            description="",
            location=None,
            starts_at=self.starts_at,
            ends_at=self.starts_at,
            all_day=False,
            timezone=self.timezone,
            recurrence_rule=self.recurrence_rule,
            recurrence_until=None,
            source_type="TASK",
            source_id=None,
            status="CONFIRMED",
            version=1,
            created_by=0,
            created_at=self.starts_at,
            updated_at=self.starts_at,
        )


class TaskSchedulerRunner:
    """Poll for claimable tasks and run them through an executor."""

    def __init__(
        self,
        *,
        scheduler: FamilyTaskScheduler,
        executor: Callable[[FamilyTaskRow], Any] | None = None,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        worker_id: str | None = None,
    ) -> None:
        self._scheduler = scheduler
        self._executor = executor
        self._poll_interval = poll_interval_seconds
        self._worker_id = worker_id or _worker_id()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def start(self) -> None:
        if self._task is not None:
            return
        recovered = self._scheduler.recover_stale()
        if recovered:
            logger.info("TaskSchedulerRunner: recovered %d stale leases", recovered)
        self._task = asyncio.create_task(self._loop(), name="homemind-task-scheduler")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.drain_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — one bad task must not kill the loop
                logger.exception("TaskSchedulerRunner: drain failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval)

    async def drain_once(self) -> int:
        """Claim and run whatever is due. Returns tasks finished."""
        # Claiming is synchronous SQL; running an agent is a long await.
        claimed = await asyncio.to_thread(self._scheduler.claim_due, owner=self._worker_id)
        finished = 0
        for task in claimed:
            result = await self._run(task)
            updated = await asyncio.to_thread(
                self._scheduler.finish, task.id, owner=self._worker_id, result=result
            )
            if updated is not None:
                finished += 1
                _hm_inc("family_task_finished_total")
        return finished

    async def _run(self, task: FamilyTaskRow) -> TaskExecutionResult:
        attempt = await asyncio.to_thread(
            self._scheduler.repo.start_attempt,
            task.id,
            family_id=task.family_id,
            attempt_number=task.attempt_count,
            lease_owner=self._worker_id,
        )
        if self._executor is None:
            outcome = TaskExecutionResult(
                status=TASK_STATUS_FAILED,
                error="no executor is wired for this task type",
            )
        else:
            try:
                outcome = await self._executor(task)
            except Exception as exc:  # noqa: BLE001 — a failing run is an outcome, not a crash
                logger.exception("TaskSchedulerRunner: task %s raised", task.id)
                outcome = TaskExecutionResult(
                    status=TASK_STATUS_FAILED, error=f"{type(exc).__name__}: {exc}"[:500]
                )
        await asyncio.to_thread(
            self._scheduler.repo.finish_attempt,
            attempt.id,
            status=outcome.status,
            summary=outcome.summary,
            error=outcome.error,
        )
        if outcome.status == TASK_STATUS_FAILED:
            _hm_inc("family_task_failed_total")
        return outcome


__all__ = [
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "MAX_CONCURRENT_PER_AGENT",
    "MAX_CONCURRENT_PER_FAMILY",
    "FamilyTaskScheduler",
    "TaskExecutionResult",
    "TaskSchedulerRunner",
]
