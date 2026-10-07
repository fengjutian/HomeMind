"""SQL access for HomeMind family calendars and calendar events.

Both tables follow the repository convention used elsewhere in this
package: a public ULID (``calendar_id`` / ``event_id``) is what the API
and every foreign key use, while ``id`` stays an internal surrogate key
exposed as ``pk``.

Events are never expanded into occurrences here. A recurring event is
one row; the calendar manager computes the occurrences for a requested
window on the fly so a ten-year daily event costs one row, not 3650.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid

# Event status.
EVENT_STATUS_CONFIRMED = "CONFIRMED"
EVENT_STATUS_CANCELLED = "CANCELLED"
EVENT_STATUSES: frozenset[str] = frozenset({EVENT_STATUS_CONFIRMED, EVENT_STATUS_CANCELLED})

# What created the event. ``TASK`` links a due date to the task board;
# ``EXTERNAL`` reserves the id space for a future calendar sync.
SOURCE_TYPE_MANUAL = "MANUAL"
SOURCE_TYPE_TASK = "TASK"
SOURCE_TYPE_EXTERNAL = "EXTERNAL"
SOURCE_TYPES: frozenset[str] = frozenset(
    {SOURCE_TYPE_MANUAL, SOURCE_TYPE_TASK, SOURCE_TYPE_EXTERNAL}
)

# Calendar visibility, mirroring the space/asset vocabulary already in use.
VISIBILITY_PRIVATE = "PRIVATE"
VISIBILITY_FAMILY = "FAMILY"
VISIBILITY_PUBLIC = "PUBLIC"
CALENDAR_VISIBILITIES: frozenset[str] = frozenset(
    {VISIBILITY_PRIVATE, VISIBILITY_FAMILY, VISIBILITY_PUBLIC}
)


def _row_data(row: DbRow) -> dict[str, Any]:
    # ``sqlite3.Row`` iterates values, not column names, so the mapping
    # has to go through ``.keys()`` (see the note in ``asset_jobs.py``).
    return (
        {key: row[key] for key in row.keys()}  # noqa: SIM118
        if hasattr(row, "keys")
        else dict(row)
    )


@dataclass(frozen=True)
class FamilyCalendarRow:
    id: str
    pk: int
    family_id: str
    name: str
    description: str
    color: str | None
    timezone: str | None
    visibility: str
    space_id: str | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyCalendarRow:
        data = _row_data(row)
        return cls(
            id=str(data["calendar_id"]),
            pk=int(data["id"]),
            family_id=str(data["family_id"]),
            name=str(data["name"]),
            description=str(data["description"]),
            color=data["color"],
            timezone=data["timezone"],
            visibility=str(data["visibility"]),
            space_id=data["space_id"],
            created_by=int(data["created_by"]),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


@dataclass(frozen=True)
class FamilyCalendarEventRow:
    id: str
    pk: int
    calendar_id: str
    family_id: str
    title: str
    description: str
    location: str | None
    starts_at: int
    ends_at: int
    all_day: bool
    timezone: str
    recurrence_rule: str | None
    recurrence_until: int | None
    source_type: str
    source_id: str | None
    status: str
    version: int
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyCalendarEventRow:
        data = _row_data(row)
        return cls(
            id=str(data["event_id"]),
            pk=int(data["id"]),
            calendar_id=str(data["calendar_id"]),
            family_id=str(data["family_id"]),
            title=str(data["title"]),
            description=str(data["description"]),
            location=data["location"],
            starts_at=int(data["starts_at"]),
            ends_at=int(data["ends_at"]),
            all_day=bool(data["all_day"]),
            timezone=str(data["timezone"]),
            recurrence_rule=data["recurrence_rule"],
            recurrence_until=(
                int(data["recurrence_until"]) if data["recurrence_until"] is not None else None
            ),
            source_type=str(data["source_type"]),
            source_id=data["source_id"],
            status=str(data["status"]),
            version=int(data["version"]),
            created_by=int(data["created_by"]),
            created_at=int(data["created_at"]),
            updated_at=int(data["updated_at"]),
        )


class FamilyCalendarRepo:
    """SQL access for ``homemind_family_calendars`` and its events."""

    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    # ------------------------------------------------------------- calendars

    def create_calendar(
        self,
        family_id: str,
        *,
        name: str,
        description: str = "",
        color: str | None = None,
        timezone: str | None = None,
        visibility: str = VISIBILITY_FAMILY,
        space_id: str | None = None,
        created_by: int,
    ) -> FamilyCalendarRow:
        calendar_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_calendars(calendar_id, family_id, name, "
                "description, color, timezone, visibility, space_id, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    calendar_id,
                    family_id,
                    name,
                    description,
                    color,
                    timezone,
                    visibility,
                    space_id,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_calendar(calendar_id)  # type: ignore[return-value]

    def get_calendar(self, calendar_id: str) -> FamilyCalendarRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_calendars WHERE calendar_id = ?", (calendar_id,)
            ).fetchone()
        return FamilyCalendarRow.from_row(row) if row else None

    def list_calendars(self, family_id: str) -> list[FamilyCalendarRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_calendars WHERE family_id = ? "
                "ORDER BY name ASC, created_at ASC",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyCalendarRow)

    def update_calendar(self, calendar_id: str, **values: object) -> FamilyCalendarRow | None:
        allowed = {
            "name",
            "description",
            "color",
            "timezone",
            "visibility",
            "space_id",
        }
        fields = [key for key in values if key in allowed]
        if fields:
            params = [values[key] for key in fields]
            params.extend((now_ts(), calendar_id))
            with self._db.transaction() as conn:
                conn.execute(
                    "UPDATE homemind_family_calendars SET "
                    f"{', '.join(f'{key} = ?' for key in fields)}, updated_at = ? "
                    "WHERE calendar_id = ?",
                    params,
                )
        return self.get_calendar(calendar_id)

    def delete_calendar(self, calendar_id: str) -> bool:
        """Drop a calendar. Its events cascade with it."""
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_calendars WHERE calendar_id = ?", (calendar_id,)
            )
        return bool(cursor.rowcount > 0)

    # ---------------------------------------------------------------- events

    def create_event(
        self,
        family_id: str,
        *,
        calendar_id: str,
        title: str,
        description: str = "",
        location: str | None = None,
        starts_at: int,
        ends_at: int,
        all_day: bool = False,
        timezone: str,
        recurrence_rule: str | None = None,
        recurrence_until: int | None = None,
        source_type: str = SOURCE_TYPE_MANUAL,
        source_id: str | None = None,
        status: str = EVENT_STATUS_CONFIRMED,
        created_by: int,
    ) -> FamilyCalendarEventRow:
        event_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_calendar_events(event_id, calendar_id, family_id, "
                "title, description, location, starts_at, ends_at, all_day, timezone, "
                "recurrence_rule, recurrence_until, source_type, source_id, status, version, "
                "created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    event_id,
                    calendar_id,
                    family_id,
                    title,
                    description,
                    location,
                    starts_at,
                    ends_at,
                    1 if all_day else 0,
                    timezone,
                    recurrence_rule,
                    recurrence_until,
                    source_type,
                    source_id,
                    status,
                    created_by,
                    timestamp,
                    timestamp,
                ),
            )
        return self.get_event(event_id)  # type: ignore[return-value]

    def get_event(self, event_id: str) -> FamilyCalendarEventRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_calendar_events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return FamilyCalendarEventRow.from_row(row) if row else None

    def list_events(
        self,
        family_id: str,
        *,
        calendar_id: str | None = None,
        status: str | None = None,
        window_start: int | None = None,
        window_end: int | None = None,
        limit: int = 200,
    ) -> list[FamilyCalendarEventRow]:
        """List events for a family, optionally clipped to a start-time window.

        The window is a *candidate* filter, not the answer: a weekly event
        whose first occurrence is years old still has occurrences inside
        the window, so the calendar manager re-checks every returned row
        against its rule. A recurring event is therefore admitted by its
        ``DTSTART``/``UNTIL`` span alone -- filtering it on ``ends_at``
        would drop every occurrence after the first.
        """
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if calendar_id is not None:
            clauses.append("calendar_id = ?")
            params.append(calendar_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if window_end is not None:
            clauses.append("starts_at <= ?")
            params.append(window_end)
        if window_start is not None:
            clauses.append(
                "(recurrence_rule IS NOT NULL OR ends_at >= ?) "
                "AND (recurrence_until IS NULL OR recurrence_until >= ?)"
            )
            params.extend((window_start, window_start))
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_calendar_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY starts_at ASC, id ASC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyCalendarEventRow)

    def list_events_for_source(
        self, family_id: str, source_type: str, source_id: str
    ) -> list[FamilyCalendarEventRow]:
        """Find the calendar events a source row (e.g. a task) produced."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_calendar_events "
                "WHERE family_id = ? AND source_type = ? AND source_id = ? "
                "ORDER BY starts_at ASC, id ASC",
                (family_id, source_type, source_id),
            ).fetchall()
        return map_rows(rows, FamilyCalendarEventRow)

    def update_event(
        self, event_id: str, *, expected_version: int | None = None, **values: object
    ) -> FamilyCalendarEventRow | None:
        """Update an event, optionally guarded by ``version``.

        The optimistic lock is enforced by the SQL ``WHERE`` clause, not
        by a prior read: two dashboards editing the same event must not
        both believe they won.
        """
        allowed = {
            "title",
            "description",
            "location",
            "starts_at",
            "ends_at",
            "all_day",
            "timezone",
            "recurrence_rule",
            "recurrence_until",
            "status",
        }
        fields = [key for key in values if key in allowed]
        if not fields:
            return self.get_event(event_id)
        sets = [f"{key} = ?" for key in fields]
        params: list[Any] = [1 if values[key] is True else values[key] for key in fields]
        sets.append("version = version + 1")
        sets.append("updated_at = ?")
        params.append(now_ts())
        where = "event_id = ?"
        params.append(event_id)
        if expected_version is not None:
            where += " AND version = ?"
            params.append(expected_version)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                f"UPDATE homemind_family_calendar_events SET {', '.join(sets)} WHERE {where}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_event(event_id)

    def set_status(
        self, event_id: str, *, status: str, from_status: str | None = None
    ) -> FamilyCalendarEventRow | None:
        """Compare-and-swap an event's status.

        Cancelling is idempotent from the caller's point of view: a
        second cancel of an already-cancelled event returns the row
        rather than failing, but a cancel racing an update cannot
        resurrect it.
        """
        where = "event_id = ?"
        params: list[Any] = [status, now_ts(), event_id]
        if from_status is not None:
            where += " AND status = ?"
            params.append(from_status)
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_calendar_events SET status = ?, updated_at = ? "
                f"WHERE {where}",
                params,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_event(event_id)

    def delete_event(self, event_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_calendar_events WHERE event_id = ?", (event_id,)
            )
        return bool(cursor.rowcount > 0)


__all__ = [
    "CALENDAR_VISIBILITIES",
    "EVENT_STATUSES",
    "EVENT_STATUS_CANCELLED",
    "EVENT_STATUS_CONFIRMED",
    "SOURCE_TYPES",
    "SOURCE_TYPE_EXTERNAL",
    "SOURCE_TYPE_MANUAL",
    "SOURCE_TYPE_TASK",
    "VISIBILITY_FAMILY",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_PUBLIC",
    "FamilyCalendarEventRow",
    "FamilyCalendarRepo",
    "FamilyCalendarRow",
]