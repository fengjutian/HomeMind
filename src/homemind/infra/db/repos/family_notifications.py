"""SQL access for family notifications and per-user preferences.

The notification table is the *truth* about what a user was told; the
event bus is only the fast path. Two consequences shape this file:

* **Idempotent delivery.** ``dedupe_key`` is ``UNIQUE``, so emitting the
  same logical notification twice (a retried job, a double-clicked
  approval) yields one row. That is the whole reason a trigger can be
  re-run safely.
* **Reads are per user, not per family.** A notification belongs to one
  ``user_id``; listing a family's notifications for someone who is not a
  member must return nothing, which the inbox index makes cheap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from homemind.infra.cursor import decode_cursor
from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

# Notification types. Stable strings: they are the contract the
# dashboard switches on and the key i18n lookups start from.
TYPE_TASK_DUE = "TASK_DUE"
TYPE_APPROVAL_WAITING = "APPROVAL_WAITING"
TYPE_APPROVAL_DECIDED = "APPROVAL_DECIDED"
TYPE_DEVICE_OFFLINE = "DEVICE_OFFLINE"
TYPE_ASSET_JOB_DONE = "ASSET_JOB_DONE"
TYPE_ASSET_JOB_FAILED = "ASSET_JOB_FAILED"
TYPE_KNOWLEDGE_INDEX_FAILED = "KNOWLEDGE_INDEX_FAILED"
TYPE_CALENDAR_REMINDER = "CALENDAR_REMINDER"
TYPE_TASK_DONE = "TASK_DONE"

NOTIFICATION_TYPES: frozenset[str] = frozenset(
    {
        TYPE_TASK_DUE,
        TYPE_APPROVAL_WAITING,
        TYPE_APPROVAL_DECIDED,
        TYPE_DEVICE_OFFLINE,
        TYPE_ASSET_JOB_DONE,
        TYPE_ASSET_JOB_FAILED,
        TYPE_KNOWLEDGE_INDEX_FAILED,
        TYPE_CALENDAR_REMINDER,
        TYPE_TASK_DONE,
    }
)

SEVERITY_INFO = "INFO"
SEVERITY_SUCCESS = "SUCCESS"
SEVERITY_WARNING = "WARNING"
SEVERITY_CRITICAL = "CRITICAL"
NOTIFICATION_SEVERITIES: frozenset[str] = frozenset(
    {SEVERITY_INFO, SEVERITY_SUCCESS, SEVERITY_WARNING, SEVERITY_CRITICAL}
)


def _row_data(row: DbRow) -> dict[str, Any]:
    return (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )


@dataclass(frozen=True)
class FamilyNotificationRow:
    id: str
    pk: int
    family_id: str
    user_id: int
    type: str
    title_key: str
    body_key: str
    params: dict[str, Any]
    target_type: str | None
    target_id: str | None
    severity: str
    dedupe_key: str
    read_at: int | None
    created_at: int
    expires_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyNotificationRow:
        data = _row_data(row)
        raw_params = data.get("params_json") or "{}"
        try:
            params = json.loads(raw_params)
        except (TypeError, ValueError):
            # A hand-edited or truncated row must not make the whole
            # inbox unreadable.
            params = {}
        return cls(
            id=str(data["notification_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            user_id=int(data["user_id"]),
            type=str(data["type"]),
            title_key=str(data["title_key"]),
            body_key=str(data["body_key"]),
            params=params if isinstance(params, dict) else {},
            target_type=data["target_type"],
            target_id=data["target_id"],
            severity=str(data["severity"]),
            dedupe_key=str(data["dedupe_key"]),
            read_at=int(data["read_at"]) if data["read_at"] is not None else None,
            created_at=int(data["created_at"]),
            expires_at=int(data["expires_at"]) if data["expires_at"] is not None else None,
        )


@dataclass(frozen=True)
class NotificationPrefsRow:
    family_id: str
    user_id: int
    disabled_types: list[str]
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    timezone: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> NotificationPrefsRow:
        data = _row_data(row)
        raw = data.get("disabled_types_json") or "[]"
        try:
            disabled = json.loads(raw)
        except (TypeError, ValueError):
            disabled = []
        return cls(
            family_id=str(data["family_id"]),
            user_id=int(data["user_id"]),
            disabled_types=[str(item) for item in disabled] if isinstance(disabled, list) else [],
            quiet_hours_start=(
                int(data["quiet_hours_start"]) if data["quiet_hours_start"] is not None else None
            ),
            quiet_hours_end=(
                int(data["quiet_hours_end"]) if data["quiet_hours_end"] is not None else None
            ),
            timezone=data["timezone"],
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class FamilyNotificationRepo:
    """SQL access for ``..._notifications`` and ``..._notification_prefs``."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    @property
    def db(self) -> DatabasePool:
        return self._db

    # ---------------------------------------------------------- notifications

    def create(
        self,
        family_id: str,
        *,
        user_id: int,
        type: str,
        title_key: str,
        body_key: str,
        dedupe_key: str,
        params: dict[str, Any] | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        severity: str = SEVERITY_INFO,
        expires_at: int | None = None,
        now: int | None = None,
    ) -> FamilyNotificationRow | None:
        """Insert a notification, or return ``None`` if it already exists.

        The conflict is *not* an error: a duplicate means the user has
        already been told, which is exactly the outcome the caller
        wanted.
        """
        notification_id, timestamp = new_ulid(), now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "INSERT INTO homemind_family_notifications(notification_id, family_id, user_id, "
                "type, title_key, body_key, params_json, target_type, target_id, severity, "
                "dedupe_key, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (dedupe_key) DO NOTHING",
                (
                    notification_id,
                    family_id,
                    user_id,
                    type,
                    title_key,
                    body_key,
                    json.dumps(params or {}, ensure_ascii=False, sort_keys=True),
                    target_type,
                    target_id,
                    severity,
                    dedupe_key,
                    timestamp,
                    expires_at,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(notification_id)

    def get(self, notification_id: str) -> FamilyNotificationRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_notifications WHERE notification_id = ?",
                (notification_id,),
            ).fetchone()
        return FamilyNotificationRow.from_row(row) if row else None

    def list_inbox(
        self,
        user_id: int,
        *,
        family_id: str | None = None,
        type: str | None = None,
        unread_only: bool = False,
        now: int | None = None,
        limit: int = 50,
        offset: int = 0,
        cursor: str | None = None,
    ) -> list[FamilyNotificationRow]:
        """Newest-first inbox for one user.

        Expired rows are filtered at *read* time as well as by the
        cleanup job: a row can lapse between sweeps and the user should
        not see it in the meantime.
        """
        timestamp = now_ts() if now is None else now
        clauses = ["user_id = ?", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [user_id, timestamp]
        if family_id is not None:
            clauses.append("family_id = ?")
            params.append(family_id)
        if type is not None:
            clauses.append("type = ?")
            params.append(type)
        if unread_only:
            clauses.append("read_at IS NULL")
        if cursor is not None:
            # Keyset paging: resume strictly *after* the cursor row rather
            # than counting from the start. Inserting a notification while
            # a phone scrolls therefore shifts nothing — no item is
            # skipped and none is shown twice.
            #
            # The tie-break is on ``notification_id``, not ``id``: in this
            # table ``id`` is the integer surrogate, so ordering by it
            # while the cursor compares ULIDs would order the same rows
            # two different ways and silently duplicate rows across a
            # page boundary.
            cursor_created_at, cursor_id = decode_cursor(cursor)
            clauses.append("(created_at < ? OR (created_at = ? AND notification_id < ?))")
            params.extend((cursor_created_at, cursor_created_at, cursor_id))
        params.extend((limit, offset))
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_notifications WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, notification_id DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyNotificationRow)

    def count_unread(
        self, user_id: int, *, family_id: str | None = None, now: int | None = None
    ) -> int:
        timestamp = now_ts() if now is None else now
        clauses = ["user_id = ?", "read_at IS NULL", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [user_id, timestamp]
        if family_id is not None:
            clauses.append("family_id = ?")
            params.append(family_id)
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total FROM homemind_family_notifications WHERE "
                + " AND ".join(clauses),
                params,
            ).fetchone()
        return int(_row_data(row)["total"]) if row else 0

    def mark_read(self, notification_id: str, *, user_id: int, now: int | None = None) -> bool:
        """Mark one notification read, but only for its owner.

        The ``user_id`` predicate is what keeps a caller from reading
        somebody else's inbox by guessing an id.
        """
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_notifications SET read_at = ? "
                "WHERE notification_id = ? AND user_id = ? AND read_at IS NULL",
                (timestamp, notification_id, user_id),
            )
        return bool(cursor.rowcount > 0)

    def mark_all_read(self, user_id: int, *, family_id: str | None = None) -> int:
        timestamp = now_ts()
        clauses = ["user_id = ?", "read_at IS NULL"]
        params: list[Any] = [user_id]
        if family_id is not None:
            clauses.append("family_id = ?")
            params.append(family_id)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_notifications SET read_at = ? WHERE "
                + " AND ".join(clauses),
                [timestamp, *params],
            )
        return int(cursor.rowcount or 0)

    def delete_expired(self, *, now: int | None = None) -> int:
        timestamp = now_ts() if now is None else now
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_notifications WHERE expires_at IS NOT NULL "
                "AND expires_at <= ?",
                (timestamp,),
            )
        return int(cursor.rowcount or 0)

    # --------------------------------------------------------- preferences

    def get_prefs(self, family_id: str, user_id: int) -> NotificationPrefsRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_notification_prefs "
                "WHERE family_id = ? AND user_id = ?",
                (family_id, user_id),
            ).fetchone()
        return NotificationPrefsRow.from_row(row) if row else None

    def save_prefs(
        self,
        family_id: str,
        *,
        user_id: int,
        disabled_types: list[str],
        quiet_hours_start: int | None = None,
        quiet_hours_end: int | None = None,
        timezone: str | None = None,
    ) -> NotificationPrefsRow:
        """Insert or update one user's preference row.

        Written as a delete-then-insert so the same statement works on
        SQLite and PostgreSQL without a dialect-specific upsert.
        """
        timestamp = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "DELETE FROM homemind_family_notification_prefs "
                "WHERE family_id = ? AND user_id = ?",
                (family_id, user_id),
            )
            conn.execute(
                "INSERT INTO homemind_family_notification_prefs(family_id, user_id, "
                "disabled_types_json, quiet_hours_start, quiet_hours_end, timezone, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    family_id,
                    user_id,
                    json.dumps(disabled_types, ensure_ascii=False),
                    quiet_hours_start,
                    quiet_hours_end,
                    timezone,
                    timestamp,
                    timestamp,
                ),
            )
        row = self.get_prefs(family_id, user_id)
        assert row is not None  # just inserted
        return row


__all__ = [
    "NOTIFICATION_SEVERITIES",
    "NOTIFICATION_TYPES",
    "SEVERITY_CRITICAL",
    "SEVERITY_INFO",
    "SEVERITY_SUCCESS",
    "SEVERITY_WARNING",
    "TYPE_APPROVAL_DECIDED",
    "TYPE_APPROVAL_WAITING",
    "TYPE_ASSET_JOB_DONE",
    "TYPE_ASSET_JOB_FAILED",
    "TYPE_CALENDAR_REMINDER",
    "TYPE_DEVICE_OFFLINE",
    "TYPE_KNOWLEDGE_INDEX_FAILED",
    "TYPE_TASK_DONE",
    "TYPE_TASK_DUE",
    "FamilyNotificationRepo",
    "FamilyNotificationRow",
    "NotificationPrefsRow",
]
