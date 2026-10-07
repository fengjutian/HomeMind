"""SQL access for HomeMind family reminders.

A reminder is a *scheduled intent*, not a notification: it says "at
``remind_at``, tell this member about this target". Turning it into
something a user sees is the runner's job, which is why the row keeps a
lease, an attempt counter and a last error instead of a delivered flag.

Two invariants live in this file rather than in the runner:

* **One row per (target, occurrence, recipient).** ``dedupe_key`` is
  ``UNIQUE``, so a runner restart or a second runner cannot create a
  second in-app notification for the same thing.
* **One owner at a time.** Claiming is a compare-and-swap on
  ``status``/``lease_expires_at``; the loser of the race gets ``None``
  and moves on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

# What a reminder is about. Kept as a string union rather than a
# foreign key so a reminder can point at a task, a calendar occurrence,
# an approval or a device without four nullable columns.
TARGET_TYPE_TASK = "TASK"
TARGET_TYPE_CALENDAR_EVENT = "CALENDAR_EVENT"
TARGET_TYPE_APPROVAL = "APPROVAL"
TARGET_TYPE_DEVICE = "DEVICE"
TARGET_TYPES: frozenset[str] = frozenset(
    {TARGET_TYPE_TASK, TARGET_TYPE_CALENDAR_EVENT, TARGET_TYPE_APPROVAL, TARGET_TYPE_DEVICE}
)

# Delivery channels. Only IN_APP exists; an enum keeps "IM channel"
# from being smuggled in as a free-text string that nothing fulfils.
CHANNEL_IN_APP = "IN_APP"
REMINDER_CHANNELS: frozenset[str] = frozenset({CHANNEL_IN_APP})

# Lifecycle.
STATUS_PENDING = "PENDING"
STATUS_CLAIMED = "CLAIMED"
STATUS_SENT = "SENT"
STATUS_FAILED = "FAILED"
STATUS_CANCELLED = "CANCELLED"
REMINDER_STATUSES: frozenset[str] = frozenset(
    {STATUS_PENDING, STATUS_CLAIMED, STATUS_SENT, STATUS_FAILED, STATUS_CANCELLED}
)

# Statuses a user-facing query may still return.
VISIBLE_STATUSES: frozenset[str] = frozenset({STATUS_PENDING, STATUS_CLAIMED, STATUS_SENT})


def _row_data(row: DbRow) -> dict[str, Any]:
    return (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )


@dataclass(frozen=True)
class FamilyReminderRow:
    id: str
    pk: int
    family_id: str
    target_type: str
    target_id: str
    recipient_member_id: str | None
    recipient_user_id: int | None
    remind_at: int
    channel: str
    status: str
    lease_owner: str | None
    lease_expires_at: int | None
    attempt_count: int
    last_error: str | None
    dedupe_key: str
    occurrence_key: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyReminderRow:
        data = _row_data(row)
        return cls(
            id=str(data["reminder_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            target_type=str(data["target_type"]),
            target_id=str(data["target_id"]),
            recipient_member_id=data["recipient_member_id"],
            recipient_user_id=(
                int(data["recipient_user_id"]) if data["recipient_user_id"] is not None else None
            ),
            remind_at=int(data["remind_at"]),
            channel=str(data["channel"]),
            status=str(data["status"]),
            lease_owner=data["lease_owner"],
            lease_expires_at=(
                int(data["lease_expires_at"]) if data["lease_expires_at"] is not None else None
            ),
            attempt_count=int(data["attempt_count"]),
            last_error=data["last_error"],
            dedupe_key=str(data["dedupe_key"]),
            occurrence_key=data["occurrence_key"],
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class FamilyReminderRepo:
    """SQL access for ``homemind_family_reminders``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @property
    def db(self) -> DatabasePool:
        """The pool this repo runs on.

        Exposed so a domain service that must read a *different* table
        (to resolve a reminder's target, say) can build that repo
        explicitly instead of reaching into ``_db``.
        """
        return self._db

    def create(
        self,
        family_id: str,
        *,
        target_type: str,
        target_id: str,
        remind_at: int,
        dedupe_key: str,
        recipient_member_id: str | None = None,
        recipient_user_id: int | None = None,
        channel: str = CHANNEL_IN_APP,
        occurrence_key: str | None = None,
    ) -> FamilyReminderRow | None:
        """Insert a reminder, or return ``None`` when it already exists.

        The ``ON CONFLICT DO NOTHING`` form makes "create this reminder"
        idempotent by construction: the caller does not have to check
        first and then race, and the unique index is the single source
        of truth for "already scheduled".
        """
        reminder_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO homemind_family_reminders(reminder_id, family_id, target_type, "
                "target_id, recipient_member_id, recipient_user_id, remind_at, channel, status, "
                "attempt_count, dedupe_key, occurrence_key, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?, ?, ?) "
                "ON CONFLICT (dedupe_key) DO NOTHING",
                (
                    reminder_id,
                    family_id,
                    target_type,
                    target_id,
                    recipient_member_id,
                    recipient_user_id,
                    remind_at,
                    channel,
                    dedupe_key,
                    occurrence_key,
                    timestamp,
                    timestamp,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(reminder_id)

    def get(self, reminder_id: str) -> FamilyReminderRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_reminders WHERE reminder_id = ?", (reminder_id,)
            ).fetchone()
        return FamilyReminderRow.from_row(row) if row else None

    def list_for_family(
        self,
        family_id: str,
        *,
        status: str | None = None,
        target_type: str | None = None,
        limit: int = 200,
    ) -> list[FamilyReminderRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if target_type is not None:
            clauses.append("target_type = ?")
            params.append(target_type)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_reminders WHERE "
                + " AND ".join(clauses)
                + " ORDER BY remind_at ASC, id ASC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyReminderRow)

    def list_for_target(self, target_type: str, target_id: str) -> list[FamilyReminderRow]:
        """Every reminder attached to one target.

        Used when an event is moved or a task's due date changes: the
        old reminders must be invalidated, and they can only be found
        through the target.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_reminders "
                "WHERE target_type = ? AND target_id = ? ORDER BY remind_at ASC, id ASC",
                (target_type, target_id),
            ).fetchall()
        return map_rows(rows, FamilyReminderRow)

    def list_for_member(
        self, family_id: str, member_id: str, *, limit: int = 200
    ) -> list[FamilyReminderRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_reminders WHERE family_id = ? "
                "AND (recipient_member_id = ? OR recipient_member_id IS NULL) "
                "AND status IN ('PENDING', 'CLAIMED', 'SENT') "
                "ORDER BY remind_at ASC, id ASC LIMIT ?",
                (family_id, member_id, limit),
            ).fetchall()
        return map_rows(rows, FamilyReminderRow)

    def claim_due(
        self,
        *,
        owner: str,
        ttl_seconds: int,
        now: int | None = None,
        limit: int = 25,
    ) -> list[FamilyReminderRow]:
        """Atomically take every due reminder into a lease.

        Two steps on purpose: read the candidate ids, then CAS each one.
        Reading inside the claim transaction without a lock would let two
        runners pick the same id; the ``status``/``lease_expires_at``
        guard is what makes the second one lose.
        """
        timestamp = now_ts() if now is None else now
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT reminder_id FROM homemind_family_reminders "
                "WHERE status = 'PENDING' AND remind_at <= ? "
                "AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?) "
                "ORDER BY remind_at ASC, id ASC LIMIT ?",
                (timestamp, timestamp, limit),
            ).fetchall()
        claimed: list[FamilyReminderRow] = []
        for row in rows:
            data = _row_data(row)
            reminder = self._claim_one(
                str(data["reminder_id"]), owner=owner, ttl_seconds=ttl_seconds, now=timestamp
            )
            if reminder is not None:
                claimed.append(reminder)
        return claimed

    def _claim_one(
        self, reminder_id: str, *, owner: str, ttl_seconds: int, now: int
    ) -> FamilyReminderRow | None:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_reminders SET status = 'CLAIMED', lease_owner = ?, "
                "lease_expires_at = ?, attempt_count = attempt_count + 1, updated_at = ? "
                "WHERE reminder_id = ? AND status = 'PENDING' "
                "AND (lease_owner IS NULL OR lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (owner, now + ttl_seconds, now, reminder_id, now),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(reminder_id)

    def recover_stale_leases(self, *, now: int | None = None) -> int:
        """Return ``CLAIMED`` reminders whose runner died back to ``PENDING``.

        Called on boot. Without it a crash mid-delivery would strand a
        reminder in ``CLAIMED`` forever -- the user never hears about it
        and nothing retries.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_reminders SET status = 'PENDING', "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE status = 'CLAIMED' "
                "AND (lease_expires_at IS NULL OR lease_expires_at <= ?)",
                (timestamp, timestamp),
            )
        return int(cursor.rowcount or 0)

    def mark_sent(self, reminder_id: str, *, now: int | None = None) -> FamilyReminderRow | None:
        return self._finish(reminder_id, STATUS_SENT, error=None, now=now)

    def mark_failed(
        self,
        reminder_id: str,
        *,
        error: str,
        permanent: bool = False,
        now: int | None = None,
    ) -> FamilyReminderRow | None:
        """Record a delivery failure.

        ``permanent`` retires the reminder (``FAILED``). Otherwise it
        goes back to ``PENDING`` so the next sweep retries it, keeping
        the lease clear; the runner applies the backoff, not this layer.
        """
        return self._finish(
            reminder_id,
            STATUS_FAILED if permanent else STATUS_PENDING,
            error=error[:500],
            now=now,
        )

    def _finish(
        self, reminder_id: str, status: str, *, error: str | None, now: int | None
    ) -> FamilyReminderRow | None:
        timestamp = now_ts() if now is None else now
        # The lease always goes: a finished reminder owns nothing, and a
        # requeued one must be claimable by whoever sweeps next.
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_reminders SET status = ?, last_error = ?, "
                "updated_at = ?, lease_owner = NULL, lease_expires_at = NULL "
                "WHERE reminder_id = ?",
                [status, error, timestamp, reminder_id],
            )
        return self.get(reminder_id)

    def cancel_one(self, reminder_id: str, *, now: int | None = None) -> FamilyReminderRow | None:
        """Cancel a single reminder.

        Distinct from :meth:`cancel_for_target`: an event may have one
        reminder per occurrence, and the user cancelling Tuesday's must
        not silently drop every other reminder for that event.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_reminders SET status = 'CANCELLED', "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                "WHERE reminder_id = ? AND status NOT IN ('SENT', 'CANCELLED')",
                (timestamp, reminder_id),
            )
        return self.get(reminder_id)

    def cancel_for_target(
        self,
        target_type: str,
        target_id: str,
        *,
        statuses: tuple[str, ...] = (STATUS_PENDING, STATUS_CLAIMED),
        now: int | None = None,
    ) -> int:
        """Invalidate the outstanding reminders of one target.

        Already-``SENT`` rows are left alone: the user was told, and
        rewriting history would hide that.
        """
        if not statuses:
            return 0
        timestamp = now_ts() if now is None else now
        placeholders = ", ".join(f"'{status}'" for status in statuses)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_reminders SET status = 'CANCELLED', "
                "lease_owner = NULL, lease_expires_at = NULL, updated_at = ? "
                f"WHERE target_type = ? AND target_id = ? AND status IN ({placeholders})",
                (timestamp, target_type, target_id),
            )
        return int(cursor.rowcount or 0)

    def delete_for_target(self, target_type: str, target_id: str) -> int:
        """Hard-delete reminders of a target that no longer exists."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_reminders WHERE target_type = ? AND target_id = ?",
                (target_type, target_id),
            )
        return int(cursor.rowcount or 0)

    def count_by_status(self, family_id: str) -> dict[str, int]:
        """Status histogram for the dashboard's reminder summary."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS total FROM homemind_family_reminders "
                "WHERE family_id = ? GROUP BY status",
                (family_id,),
            ).fetchall()
        return {str(_row_data(row)["status"]): int(_row_data(row)["total"]) for row in rows}


__all__ = [
    "CHANNEL_IN_APP",
    "REMINDER_CHANNELS",
    "REMINDER_STATUSES",
    "STATUS_CANCELLED",
    "STATUS_CLAIMED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_SENT",
    "TARGET_TYPES",
    "TARGET_TYPE_APPROVAL",
    "TARGET_TYPE_CALENDAR_EVENT",
    "TARGET_TYPE_DEVICE",
    "TARGET_TYPE_TASK",
    "VISIBLE_STATUSES",
    "FamilyReminderRepo",
    "FamilyReminderRow",
]
