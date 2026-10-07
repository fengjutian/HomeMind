"""SQL access for HomeMind family tasks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

# Task lifecycle. The scheduler drives TODO → SCHEDULED → IN_PROGRESS →
# DONE, with FAILED and WAITING_APPROVAL as the two branches a real
# execution can take.
TASK_STATUS_TODO = "TODO"
TASK_STATUS_SCHEDULED = "SCHEDULED"
TASK_STATUS_IN_PROGRESS = "IN_PROGRESS"
TASK_STATUS_WAITING_APPROVAL = "WAITING_APPROVAL"
TASK_STATUS_DONE = "DONE"
TASK_STATUS_FAILED = "FAILED"
TASK_STATUS_CANCELLED = "CANCELLED"
TASK_STATUS_BLOCKED = "BLOCKED"

TASK_STATUSES: frozenset[str] = frozenset(
    {
        TASK_STATUS_TODO,
        TASK_STATUS_SCHEDULED,
        TASK_STATUS_IN_PROGRESS,
        TASK_STATUS_WAITING_APPROVAL,
        TASK_STATUS_DONE,
        TASK_STATUS_FAILED,
        TASK_STATUS_CANCELLED,
        TASK_STATUS_BLOCKED,
    }
)

#: Statuses from which no further transition is allowed.
TERMINAL_TASK_STATUSES: frozenset[str] = frozenset({TASK_STATUS_DONE, TASK_STATUS_CANCELLED})

#: Legal transitions. Anything not listed is refused by
#: :meth:`FamilyTaskRepo.transition`.
TASK_TRANSITIONS: dict[str, frozenset[str]] = {
    # A retry leaves ``FAILED``/``BLOCKED``/``WAITING_APPROVAL`` for
    # ``TODO``; the plan's diagram draws the FAILED → TODO edge but not
    # the approval-resume one, and both are the same motion: put the
    # task back in the queue.
    TASK_STATUS_TODO: frozenset(
        {
            TASK_STATUS_SCHEDULED,
            TASK_STATUS_IN_PROGRESS,
            TASK_STATUS_DONE,
            TASK_STATUS_CANCELLED,
            TASK_STATUS_BLOCKED,
        }
    ),
    TASK_STATUS_SCHEDULED: frozenset(
        {
            TASK_STATUS_IN_PROGRESS,
            TASK_STATUS_TODO,
            TASK_STATUS_CANCELLED,
            TASK_STATUS_BLOCKED,
        }
    ),
    TASK_STATUS_IN_PROGRESS: frozenset(
        {
            TASK_STATUS_DONE,
            TASK_STATUS_FAILED,
            TASK_STATUS_TODO,
            TASK_STATUS_WAITING_APPROVAL,
            TASK_STATUS_CANCELLED,
        }
    ),
    TASK_STATUS_WAITING_APPROVAL: frozenset(
        {
            TASK_STATUS_IN_PROGRESS,
            TASK_STATUS_TODO,
            TASK_STATUS_FAILED,
            TASK_STATUS_CANCELLED,
        }
    ),
    TASK_STATUS_FAILED: frozenset({TASK_STATUS_TODO, TASK_STATUS_CANCELLED}),
    TASK_STATUS_BLOCKED: frozenset({TASK_STATUS_TODO, TASK_STATUS_CANCELLED}),
    TASK_STATUS_DONE: frozenset(),
    TASK_STATUS_CANCELLED: frozenset(),
}

TASK_TYPE_MANUAL = "MANUAL"
TASK_TYPE_AGENT = "AGENT"
TASK_TYPE_DEVICE = "DEVICE"
TASK_TYPES: frozenset[str] = frozenset({TASK_TYPE_MANUAL, TASK_TYPE_AGENT, TASK_TYPE_DEVICE})

#: How long a claimed task stays claimed before another worker may take
#: it. Long enough for an agent turn, short enough that a dead worker
#: does not block a task for a whole afternoon.
TASK_LEASE_SECONDS = 900


@dataclass(frozen=True)
class FamilyTaskRow:
    id: str
    pk: int
    family_id: str
    title: str
    description: str
    status: str
    assigned_member_id: str | None
    due_at: int | None
    created_by: int
    created_at: int
    updated_at: int
    # Scheduling columns (migration 020). Absent on a task created
    # before it, which is exactly the point: a pre-existing to-do is a
    # MANUAL task with nothing scheduled.
    task_type: str = "MANUAL"
    priority: int = 0
    schedule_at: int | None = None
    recurrence_rule: str | None = None
    agent_id: str | None = None
    transaction_id: str | None = None
    parent_task_id: str | None = None
    attempt_count: int = 0
    max_attempts: int = 3
    lease_owner: str | None = None
    lease_expires_at: int | None = None
    started_at: int | None = None
    completed_at: int | None = None
    result_summary: str | None = None
    last_error: str | None = None
    version: int = 1

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTaskRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )

        def _opt(key: str) -> int | None:
            value = data.get(key)
            return int(value) if value is not None else None

        def _int(key: str, default: int) -> int:
            value = data.get(key)
            return int(value) if value is not None else default

        return cls(
            id=str(data["task_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            title=str(data["title"]),
            description=str(data["description"]),
            status=str(data["status"]),
            assigned_member_id=data["assigned_member_id"],
            due_at=_opt("due_at"),
            created_by=int(data["created_by"]),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
            task_type=str(data.get("task_type") or "MANUAL"),
            priority=_int("priority", 0),
            schedule_at=_opt("schedule_at"),
            recurrence_rule=data.get("recurrence_rule"),
            agent_id=data.get("agent_id"),
            transaction_id=data.get("transaction_id"),
            parent_task_id=data.get("parent_task_id"),
            attempt_count=_int("attempt_count", 0),
            max_attempts=_int("max_attempts", 3),
            lease_owner=data.get("lease_owner"),
            lease_expires_at=_opt("lease_expires_at"),
            started_at=_opt("started_at"),
            completed_at=_opt("completed_at"),
            result_summary=data.get("result_summary"),
            last_error=data.get("last_error"),
            version=_int("version", 1),
        )


@dataclass(frozen=True)
class FamilyTaskDependencyRow:
    id: str
    task_id: str
    depends_on_task_id: str
    created_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTaskDependencyRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["dependency_id"]),
            task_id=str(data["task_id"]),
            depends_on_task_id=str(data["depends_on_task_id"]),
            created_at=int(data["created_at"]),
        )


@dataclass(frozen=True)
class FamilyTaskAttemptRow:
    id: str
    task_id: str
    family_id: str
    attempt_number: int
    status: str
    summary: str | None
    error: str | None
    lease_owner: str | None
    started_at: int
    finished_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyTaskAttemptRow:
        data: dict[str, Any] = (
            {key: row[key] for key in row.keys()}  # noqa: SIM118
            if hasattr(row, "keys")
            else dict(row)
        )
        return cls(
            id=str(data["attempt_id"]),
            task_id=str(data["task_id"]),
            family_id=str(data["family_id"]),
            attempt_number=int(data["attempt_number"]),
            status=str(data["status"]),
            summary=data["summary"],
            error=data["error"],
            lease_owner=data["lease_owner"],
            started_at=int(data["started_at"]),
            finished_at=(int(data["finished_at"]) if data["finished_at"] is not None else None),
        )


class FamilyTaskRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        family_id: str,
        *,
        title: str,
        description: str,
        assigned_member_id: str | None,
        due_at: int | None,
        created_by: int,
    ) -> FamilyTaskRow:
        task_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_tasks(task_id, family_id, title, description, "
                "assigned_member_id, due_at, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    family_id,
                    title,
                    description,
                    assigned_member_id,
                    due_at,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        row = self.get(task_id)
        assert row is not None
        return row

    def get(self, task_id: str) -> FamilyTaskRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return FamilyTaskRow.from_row(row) if row else None

    def list_for_family(
        self, family_id: str, *, status: str | None = None, limit: int = 200
    ) -> list[FamilyTaskRow]:
        """Named ``list_for_family`` rather than ``list``.

        A method called ``list`` shadows the builtin *inside the class
        body*, so every ``-> list[X]`` annotation on this class then
        resolves to the method and mypy reads it as ``list?[X]``. The
        same rename was applied to the sibling repos for this reason.
        """
        sql = "SELECT * FROM homemind_family_tasks WHERE family_id = ?"
        params: list[object] = [family_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        params.append(limit)
        sql += " ORDER BY due_at IS NULL, due_at, created_at LIMIT ?"
        with self._db.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return map_rows(rows, FamilyTaskRow)

    def update(self, task_id: str, **values: object) -> FamilyTaskRow | None:
        allowed = {
            "title",
            "description",
            "status",
            "assigned_member_id",
            "due_at",
            # Scheduling columns (migration 020). Listed explicitly
            # because an unlisted key is silently dropped, and a
            # dropped ``schedule_at`` would leave a task that looks
            # scheduled but can never be claimed.
            "task_type",
            "priority",
            "schedule_at",
            "recurrence_rule",
            "agent_id",
            "transaction_id",
            "parent_task_id",
            "max_attempts",
            "result_summary",
            "last_error",
        }
        fields = [key for key in values if key in allowed]
        if fields:
            params = [values[key] for key in fields]
            params.extend((now_ts(), task_id))
            with self._db.transaction() as conn:
                conn.execute(
                    f"UPDATE homemind_family_tasks SET "
                    f"{', '.join(f'{key} = ?' for key in fields)}, updated_at = ? "
                    "WHERE task_id = ?",
                    params,
                )
        return self.get(task_id)

    def delete(self, task_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM homemind_family_tasks WHERE task_id = ?", (task_id,))
        return bool(cursor.rowcount > 0)

    # -------------------------------------------------------- state machine

    def transition(
        self,
        task_id: str,
        *,
        to_status: str,
        from_status: str | tuple[str, ...],
        error: str | None = None,
        result_summary: str | None = None,
        lease_owner: str | None = None,
        now: int | None = None,
    ) -> FamilyTaskRow | None:
        """Move a task between states with a compare-and-swap.

        The guard is in the SQL ``WHERE`` clause rather than a prior
        read: two schedulers (or a scheduler and a human clicking
        "cancel") both seeing ``IN_PROGRESS`` must not both believe they
        won. Exactly one observes the transition; the loser gets ``None``.

        The lease is cleared on any move out of a running state, so a
        crashed worker's claim cannot outlive its attempt.
        """
        timestamp = now_ts() if now is None else now
        if to_status not in TASK_STATUSES:
            raise ValueError(f"unknown task status {to_status!r}")
        allowed = TASK_TRANSITIONS.get(from_status if isinstance(from_status, str) else "")
        if isinstance(from_status, str) and allowed is not None and to_status not in allowed:
            # Refuse an illegal edge loudly rather than writing a state
            # the rest of the system has no meaning for.
            raise ValueError(f"illegal task transition {from_status} -> {to_status}")

        sets = ["status = ?", "updated_at = ?", "version = version + 1"]
        params: list[Any] = [to_status, timestamp]
        if to_status == TASK_STATUS_IN_PROGRESS:
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(timestamp)
        if to_status in TERMINAL_TASK_STATUSES:
            sets.append("completed_at = ?")
            params.append(timestamp)
        if (
            to_status in {TASK_STATUS_TODO, TASK_STATUS_SCHEDULED}
            or to_status in TERMINAL_TASK_STATUSES
        ):
            sets.extend(("lease_owner = NULL", "lease_expires_at = NULL"))
        if error is not None:
            sets.append("last_error = ?")
            params.append(error[:500])
        if result_summary is not None:
            sets.append("result_summary = ?")
            params.append(result_summary[:2000])
        if lease_owner is not None:
            sets.extend(("lease_owner = ?", "lease_expires_at = ?"))
            params.extend((lease_owner, timestamp + TASK_LEASE_SECONDS))

        from_clause = (
            f"status = '{from_status}'"
            if isinstance(from_status, str)
            else "status IN (" + ", ".join(f"'{s}'" for s in from_status) + ")"
        )
        params.append(task_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE homemind_family_tasks SET {', '.join(sets)} "
                f"WHERE task_id = ? AND {from_clause}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get(task_id)

    def claim_for_run(
        self,
        task_id: str,
        *,
        owner: str,
        lease_seconds: int = TASK_LEASE_SECONDS,
        now: int | None = None,
    ) -> FamilyTaskRow | None:
        """Take a runnable task into a lease, or take over a dead one.

        Two guards in one statement. The task must be claimable — a
        fresh ``TODO``/``SCHEDULED`` row, or an ``IN_PROGRESS`` one whose
        lease has already lapsed. A lease is what lets a second worker
        take over from a process that died mid-run; without the expiry
        clause a crashed agent would hold its task forever.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_tasks SET status = 'IN_PROGRESS', "
                "lease_owner = ?, lease_expires_at = ?, "
                "started_at = COALESCE(started_at, ?), "
                "attempt_count = attempt_count + 1, updated_at = ?, version = version + 1 "
                "WHERE task_id = ? "
                "AND (status IN ('TODO', 'SCHEDULED') "
                "     OR (status = 'IN_PROGRESS' "
                "         AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)) "
                "AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (
                    owner,
                    timestamp + lease_seconds,
                    timestamp,
                    timestamp,
                    task_id,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(task_id)

    def list_claimable(
        self,
        *,
        now: int | None = None,
        limit: int = 20,
    ) -> list[FamilyTaskRow]:
        """Tasks a scheduler may claim right now.

        Includes an ``IN_PROGRESS`` task whose lease has lapsed: that is
        a worker that died, and the task must not stay stuck forever.

        Ordered by priority then due time, because a family's "sort the
        hall before the school run" ordering matters more than insertion
        order when several tasks come due together.
        """
        timestamp = now_ts() if now is None else now
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_tasks "
                "WHERE task_type IN ('AGENT', 'DEVICE') "
                "  AND (schedule_at IS NULL OR schedule_at <= ?) "
                "  AND attempt_count < max_attempts "
                "  AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?) "
                "  AND (status IN ('TODO', 'SCHEDULED') "
                "       OR (status = 'IN_PROGRESS' AND lease_expires_at IS NOT NULL "
                "           AND lease_expires_at <= ?)) "
                "ORDER BY priority DESC, COALESCE(schedule_at, due_at, 0) ASC, created_at ASC "
                "LIMIT ?",
                (timestamp, timestamp, timestamp, limit),
            ).fetchall()
        return map_rows(rows, FamilyTaskRow)

    def recover_stale_leases(self, *, now: int | None = None) -> int:
        """Return leased-but-expired tasks to ``TODO``.

        Called on boot. Without it a crash mid-run would strand a task
        in ``IN_PROGRESS`` forever: nothing retries and nothing shows
        the family that it is stuck.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_tasks SET status = 'TODO', "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ?, "
                "version = version + 1 "
                "WHERE status = 'IN_PROGRESS' "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?",
                (timestamp, timestamp),
            )
        return int(cursor.rowcount or 0)

    def reschedule(self, task_id: str, *, schedule_at: int | None) -> FamilyTaskRow | None:
        """Move a task's execution time without touching its status."""
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_tasks SET schedule_at = ?, updated_at = ?, "
                "version = version + 1 WHERE task_id = ?",
                (schedule_at, now_ts(), task_id),
            )
        return self.get(task_id)

    def list_for_agent(self, agent_id: str, *, limit: int = 50) -> list[FamilyTaskRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_tasks WHERE agent_id = ? "
                "ORDER BY COALESCE(schedule_at, due_at, 0) ASC, created_at ASC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        return map_rows(rows, FamilyTaskRow)

    # ---------------------------------------------------------- dependencies

    def add_dependency(
        self, task_id: str, depends_on_task_id: str
    ) -> FamilyTaskDependencyRow | None:
        """Record ``task_id`` depends on ``depends_on_task_id``.

        Idempotent: a duplicate edge returns ``None`` rather than
        counting the predecessor twice.
        """
        dependency_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO homemind_family_task_dependencies(dependency_id, task_id, "
                "depends_on_task_id, created_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (task_id, depends_on_task_id) DO NOTHING",
                (dependency_id, task_id, depends_on_task_id, timestamp),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_dependency(dependency_id)

    def get_dependency(self, dependency_id: str) -> FamilyTaskDependencyRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_task_dependencies WHERE dependency_id = ?",
                (dependency_id,),
            ).fetchone()
        return FamilyTaskDependencyRow.from_row(row) if row else None

    def list_dependencies(self, task_id: str) -> list[FamilyTaskDependencyRow]:
        """Everything ``task_id`` waits on."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_task_dependencies WHERE task_id = ? "
                "ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return map_rows(rows, FamilyTaskDependencyRow)

    def list_dependents(self, task_id: str) -> list[FamilyTaskDependencyRow]:
        """Everything that waits on ``task_id``."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_task_dependencies WHERE depends_on_task_id = ? "
                "ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return map_rows(rows, FamilyTaskDependencyRow)

    def remove_dependency(self, task_id: str, depends_on_task_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_task_dependencies "
                "WHERE task_id = ? AND depends_on_task_id = ?",
                (task_id, depends_on_task_id),
            )
        return bool(cursor.rowcount > 0)

    # -------------------------------------------------------------- attempts

    def start_attempt(
        self,
        task_id: str,
        *,
        family_id: str,
        attempt_number: int,
        lease_owner: str,
        now: int | None = None,
    ) -> FamilyTaskAttemptRow:
        attempt_id, timestamp = new_ulid(), now_ts() if now is None else now
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_task_attempts(attempt_id, task_id, family_id, "
                "attempt_number, status, lease_owner, started_at) "
                "VALUES (?, ?, ?, ?, 'RUNNING', ?, ?)",
                (attempt_id, task_id, family_id, attempt_number, lease_owner, timestamp),
            )
        row = self.get_attempt(attempt_id)
        assert row is not None  # just inserted
        return row

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        summary: str | None = None,
        error: str | None = None,
        now: int | None = None,
    ) -> FamilyTaskAttemptRow | None:
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_task_attempts SET status = ?, summary = ?, error = ?, "
                "finished_at = ? WHERE attempt_id = ?",
                (
                    status,
                    summary[:2000] if summary else None,
                    error[:500] if error else None,
                    timestamp,
                    attempt_id,
                ),
            )
        return self.get_attempt(attempt_id)

    def get_attempt(self, attempt_id: str) -> FamilyTaskAttemptRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_task_attempts WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
        return FamilyTaskAttemptRow.from_row(row) if row else None

    def list_attempts(self, task_id: str, *, limit: int = 20) -> list[FamilyTaskAttemptRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_task_attempts WHERE task_id = ? "
                "ORDER BY attempt_number DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        return map_rows(rows, FamilyTaskAttemptRow)


__all__ = [
    "TASK_STATUS_BLOCKED",
    "TASK_STATUS_CANCELLED",
    "TASK_STATUS_DONE",
    "TASK_STATUS_FAILED",
    "TASK_STATUS_IN_PROGRESS",
    "TASK_STATUS_SCHEDULED",
    "TASK_STATUS_TODO",
    "TASK_STATUS_WAITING_APPROVAL",
    "TASK_STATUSES",
    "TASK_TRANSITIONS",
    "TASK_TYPES",
    "TASK_TYPE_AGENT",
    "TASK_TYPE_DEVICE",
    "TASK_TYPE_MANUAL",
    "TERMINAL_TASK_STATUSES",
    "FamilyTaskAttemptRow",
    "FamilyTaskDependencyRow",
    "FamilyTaskRepo",
    "FamilyTaskRow",
]
