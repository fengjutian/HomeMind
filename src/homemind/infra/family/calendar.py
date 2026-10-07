"""Family calendar domain service.

Two problems live here that the repository deliberately does not solve:

**Time.** A calendar event has two representations -- the instant
(``starts_at``, UTC epoch seconds) and the wall clock a human typed
("Tuesday 09:00", ``timezone``). They are not interchangeable: a weekly
09:00 event must stay at 09:00 local across a DST change, which means
the recurrence rule has to be evaluated in the *local* zone and only
then converted to an instant. Storage keeps both; this module owns the
conversion.

**Recurrence.** An event with an RRULE is one row. Occurrences for a
requested window are computed on the fly and bounded by
``MAX_OCCURRENCES_PER_QUERY`` so a wide window over a daily rule cannot
turn into an unbounded expansion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from homemind.infra.db.repos.family_calendars import (
    CALENDAR_VISIBILITIES,
    EVENT_STATUSES,
    SOURCE_TYPES,
    VISIBILITY_FAMILY,
    FamilyCalendarEventRow,
    FamilyCalendarRepo,
    FamilyCalendarRow,
)
from homemind.infra.family.manager import FamilyManager
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

# A caller may ask for a wide window; the answer stays bounded so one
# request cannot expand a daily rule into a million rows.
MAX_OCCURRENCES_PER_QUERY = 500
MAX_OCCURRENCE_WINDOW_DAYS = 366

# RRULE strings are normalised to uppercase and stripped of the "RRULE:"
# prefix a client may or may not send, so ``FREQ=WEEKLY;BYDAY=MO`` and
# ``rrule:freq=weekly;byday=mo`` are the same stored value.
_RRULE_PREFIX = "RRULE:"


def normalize_recurrence_rule(rule: str | None) -> str | None:
    """Canonical form of an RFC 5545 RRULE, or ``None``.

    Rejects anything that is not a well-formed RRULE. Natural language
    ("every other Tuesday") is *not* accepted: the plan requires the
    stored value to be machine-readable, and accepting prose would mean
    re-parsing it differently on every read.
    """
    if rule is None:
        return None
    text = rule.strip()
    if not text:
        return None
    if text.upper().startswith(_RRULE_PREFIX):
        text = text[len(_RRULE_PREFIX) :]
    text = text.upper()
    try:
        # Reuse the same RFC 5545 parser that expands the rule, so what
        # validation accepts and what expansion supports cannot drift.
        _build_rrule(text, datetime(2000, 1, 1, tzinfo=UTC))
    except Exception as exc:  # noqa: BLE001 — any parse failure is a bad rule
        raise OctopError(ErrorCode.INVALID_INPUT, "invalid recurrence rule") from exc
    return text


def _build_rrule(rule: str, dtstart: datetime) -> object:
    from dateutil.rrule import rrulestr  # noqa: PLC0415 — optional-cost import

    return rrulestr(rule, dtstart=dtstart)


def validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise OctopError(ErrorCode.INVALID_INPUT, "invalid timezone") from exc
    return value


def resolve_timezone(name: str | None, *, fallback: str) -> ZoneInfo:
    """Resolve a stored timezone name, falling back to the server's.

    An unknown stored value must not break a calendar read; the server
    timezone is a better answer than an exception on the dashboard.
    """
    for candidate in (name, fallback):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("FamilyCalendar: unknown timezone %r; using %r", candidate, fallback)
    return ZoneInfo("UTC")


@dataclass(frozen=True)
class CalendarOccurrence:
    """One concrete instance of an event inside the requested window."""

    event_id: str
    calendar_id: str
    family_id: str
    title: str
    description: str
    location: str | None
    starts_at: int
    ends_at: int
    all_day: bool
    timezone: str
    status: str
    source_type: str
    source_id: str | None
    occurrence_key: str
    is_recurring: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "calendar_id": self.calendar_id,
            "family_id": self.family_id,
            "title": self.title,
            "description": self.description,
            "location": self.location,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "all_day": self.all_day,
            "timezone": self.timezone,
            "status": self.status,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "occurrence_key": self.occurrence_key,
            "is_recurring": self.is_recurring,
        }


class FamilyCalendarManager:
    """Calendars, events and their occurrences for one family."""

    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyCalendarRepo,
        *,
        server_timezone: str = "UTC",
    ) -> None:
        self.family = family
        self.repo = repo
        self._server_timezone = server_timezone

    @property
    def server_timezone(self) -> str:
        return self._server_timezone

    # ------------------------------------------------------------- calendars

    def create_calendar(
        self,
        family_id: str,
        user: User,
        *,
        name: str,
        description: str = "",
        color: str | None = None,
        timezone: str | None = None,
        visibility: str = VISIBILITY_FAMILY,
        space_id: str | None = None,
    ) -> FamilyCalendarRow:
        self.family.require_access(family_id, user)
        if visibility not in CALENDAR_VISIBILITIES:
            raise OctopError(ErrorCode.INVALID_INPUT, "invalid calendar visibility")
        if timezone is not None:
            validate_timezone(timezone)
        if space_id is not None:
            self._require_space(family_id, space_id)
        return self.repo.create_calendar(
            family_id,
            name=name.strip(),
            description=description.strip(),
            color=color,
            timezone=timezone,
            visibility=visibility,
            space_id=space_id,
            created_by=user.id,
        )

    def list_calendars(self, family_id: str, user: User) -> list[FamilyCalendarRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_calendars(family_id)

    def get_calendar(self, family_id: str, calendar_id: str, user: User) -> FamilyCalendarRow:
        self.family.require_access(family_id, user)
        return self._calendar(family_id, calendar_id)

    def update_calendar(
        self,
        family_id: str,
        calendar_id: str,
        user: User,
        values: dict[str, object],
    ) -> FamilyCalendarRow:
        calendar = self.get_calendar(family_id, calendar_id, user)
        if "timezone" in values and values["timezone"] is not None:
            values["timezone"] = validate_timezone(str(values["timezone"]))
        if "visibility" in values and values["visibility"] is not None:
            visibility = str(values["visibility"])
            if visibility not in CALENDAR_VISIBILITIES:
                raise OctopError(ErrorCode.INVALID_INPUT, "invalid calendar visibility")
            values["visibility"] = visibility
        if "name" in values and values["name"] is not None:
            values["name"] = str(values["name"]).strip()
        if "description" in values and values["description"] is not None:
            values["description"] = str(values["description"]).strip()
        if values.get("space_id") is not None:
            self._require_space(family_id, str(values["space_id"]))
        updated = self.repo.update_calendar(calendar.id, **values)
        if updated is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family calendar not found")
        return updated

    def delete_calendar(self, family_id: str, calendar_id: str, user: User) -> None:
        calendar = self.get_calendar(family_id, calendar_id, user)
        self.repo.delete_calendar(calendar.id)

    # ---------------------------------------------------------------- events

    def create_event(
        self,
        family_id: str,
        user: User,
        *,
        calendar_id: str,
        title: str,
        starts_at: int,
        ends_at: int,
        description: str = "",
        location: str | None = None,
        all_day: bool = False,
        timezone: str | None = None,
        recurrence_rule: str | None = None,
        recurrence_until: int | None = None,
        source_type: str = "MANUAL",
        source_id: str | None = None,
    ) -> FamilyCalendarEventRow:
        self.family.require_access(family_id, user)
        calendar = self._calendar(family_id, calendar_id)
        zone_name = self._event_timezone(calendar, timezone)
        self._validate_window(starts_at, ends_at)
        rule = normalize_recurrence_rule(recurrence_rule)
        if source_type not in SOURCE_TYPES:
            raise OctopError(ErrorCode.INVALID_INPUT, "invalid calendar event source")
        # A UNTIL in the rule and an explicit ``recurrence_until`` must
        # agree; disagreement would make the end of a series depend on
        # which one the reader happened to trust.
        if rule is not None and recurrence_until is not None:
            rule_until = _rule_until(rule)
            if rule_until is not None and abs(rule_until - recurrence_until) > 1:
                raise OctopError(
                    ErrorCode.INVALID_INPUT, "recurrence_until conflicts with the recurrence rule"
                )
        return self.repo.create_event(
            family_id,
            calendar_id=calendar.id,
            title=title.strip(),
            description=description.strip(),
            location=location.strip() if location else None,
            starts_at=starts_at,
            ends_at=ends_at,
            all_day=all_day,
            timezone=zone_name,
            recurrence_rule=rule,
            recurrence_until=recurrence_until,
            source_type=source_type,
            source_id=source_id,
            created_by=user.id,
        )

    def list_events(
        self,
        family_id: str,
        user: User,
        *,
        calendar_id: str | None = None,
        status: str | None = None,
    ) -> list[FamilyCalendarEventRow]:
        self.family.require_access(family_id, user)
        if calendar_id is not None:
            self._calendar(family_id, calendar_id)
        if status is not None and status not in EVENT_STATUSES:
            raise OctopError(ErrorCode.INVALID_INPUT, "invalid calendar event status")
        return self.repo.list_events(family_id, calendar_id=calendar_id, status=status)

    def get_event(
        self, family_id: str, event_id: str, user: User
    ) -> FamilyCalendarEventRow:
        self.family.require_access(family_id, user)
        return self._event(family_id, event_id)

    def update_event(
        self,
        family_id: str,
        event_id: str,
        user: User,
        values: dict[str, object],
        *,
        expected_version: int | None = None,
    ) -> FamilyCalendarEventRow:
        self.family.require_access(family_id, user)
        event = self._event(family_id, event_id)
        if "title" in values and values["title"] is not None:
            values["title"] = str(values["title"]).strip()
        if "description" in values and values["description"] is not None:
            values["description"] = str(values["description"]).strip()
        if "location" in values and values["location"] is not None:
            values["location"] = str(values["location"]).strip() or None
        if "timezone" in values and values["timezone"] is not None:
            values["timezone"] = validate_timezone(str(values["timezone"]))
        if "recurrence_rule" in values:
            values["recurrence_rule"] = normalize_recurrence_rule(
                None if values["recurrence_rule"] is None else str(values["recurrence_rule"])
            )
        if "starts_at" in values or "ends_at" in values:
            starts = int(values.get("starts_at", event.starts_at))  # type: ignore[arg-type]
            ends = int(values.get("ends_at", event.ends_at))  # type: ignore[arg-type]
            self._validate_window(starts, ends)
        updated = self.repo.update_event(event.id, expected_version=expected_version, **values)
        if updated is None:
            # Either the event vanished or another writer bumped the
            # version first. Both mean "your edit was not applied".
            raise OctopError(ErrorCode.CONFLICT, "calendar event was modified by someone else")
        return updated

    def cancel_event(
        self,
        family_id: str,
        event_id: str,
        user: User,
        *,
        expected_version: int | None = None,
    ) -> FamilyCalendarEventRow:
        """Cancel an event.

        Cancelling is not deleting: the row survives so the dashboard can
        show "cancelled" rather than pretending the event never existed,
        and the family's history keeps a record of it.
        """
        self.family.require_access(family_id, user)
        event = self._event(family_id, event_id)
        if event.status == "CANCELLED":
            return event
        updated = self.repo.update_event(
            event.id, expected_version=expected_version, status="CANCELLED"
        )
        if updated is None:
            raise OctopError(ErrorCode.CONFLICT, "calendar event was modified by someone else")
        return updated

    def delete_event(self, family_id: str, event_id: str, user: User) -> None:
        self.family.require_access(family_id, user)
        event = self._event(family_id, event_id)
        self.repo.delete_event(event.id)

    # ----------------------------------------------------------- occurrences

    def occurrences(
        self,
        family_id: str,
        user: User,
        *,
        window_start: int,
        window_end: int,
        calendar_id: str | None = None,
        include_cancelled: bool = False,
    ) -> list[CalendarOccurrence]:
        """Every occurrence overlapping ``[window_start, window_end]``.

        Non-recurring events contribute at most one occurrence;
        recurring ones are expanded from their rule in the event's own
        timezone, so a weekly 09:00 meeting stays at 09:00 across a DST
        boundary even though its UTC instant shifts.
        """
        self.family.require_access(family_id, user)
        if window_end <= window_start:
            raise OctopError(ErrorCode.INVALID_INPUT, "window end must be after window start")
        span_days = (window_end - window_start) / 86400
        if span_days > MAX_OCCURRENCE_WINDOW_DAYS:
            raise OctopError(
                ErrorCode.INVALID_INPUT,
                f"occurrence window must not exceed {MAX_OCCURRENCE_WINDOW_DAYS} days",
            )
        if calendar_id is not None:
            self._calendar(family_id, calendar_id)

        events = self.repo.list_events(
            family_id,
            calendar_id=calendar_id,
            status=None if include_cancelled else "CONFIRMED",
            window_start=window_start,
            window_end=window_end,
        )
        results: list[CalendarOccurrence] = []
        truncated = False
        for event in events:
            expanded = expand_occurrences(
                event,
                window_start=window_start,
                window_end=window_end,
                server_timezone=self._server_timezone,
                remaining=MAX_OCCURRENCES_PER_QUERY - len(results),
            )
            results.extend(expanded)
            if len(results) >= MAX_OCCURRENCES_PER_QUERY:
                truncated = True
                break
        if truncated:
            logger.warning(
                "FamilyCalendar: occurrence window %s..%s hit the %d result cap",
                window_start,
                window_end,
                MAX_OCCURRENCES_PER_QUERY,
            )
        results.sort(key=lambda occurrence: (occurrence.starts_at, occurrence.event_id))
        return results

    # --------------------------------------------------------------- helpers

    def _event_timezone(self, calendar: FamilyCalendarRow, override: str | None) -> str:
        """Calendar timezone, event override, else the server default."""
        if override:
            return validate_timezone(override)
        if calendar.timezone:
            return calendar.timezone
        return self._server_timezone

    @staticmethod
    def _validate_window(starts_at: int, ends_at: int) -> None:
        if ends_at <= starts_at:
            raise OctopError(ErrorCode.INVALID_INPUT, "event end must be after its start")

    def _calendar(self, family_id: str, calendar_id: str) -> FamilyCalendarRow:
        calendar = self.repo.get_calendar(calendar_id)
        if calendar is None or calendar.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family calendar not found")
        return calendar

    def _event(self, family_id: str, event_id: str) -> FamilyCalendarEventRow:
        event = self.repo.get_event(event_id)
        if event is None or event.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "calendar event not found")
        return event

    def _require_space(self, family_id: str, space_id: str) -> None:
        space = self.family.repo.get_space(space_id)
        if space is None or space.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family space not found")


def expand_occurrences(
    event: FamilyCalendarEventRow,
    *,
    window_start: int,
    window_end: int,
    server_timezone: str = "UTC",
    remaining: int = MAX_OCCURRENCES_PER_QUERY,
) -> list[CalendarOccurrence]:
    """Expand one event into the occurrences overlapping the window.

    ``remaining`` caps the expansion so a daily rule over a wide window
    cannot exhaust memory before the caller's own cap applies. Zero or
    negative means "no room left" and returns nothing.
    """
    if remaining <= 0:
        return []
    duration = max(0, event.ends_at - event.starts_at)

    # Overlap test first: a one-off event outside the window is skipped
    # without ever touching the recurrence parser.
    if event.recurrence_rule is None:
        if event.starts_at > window_end or event.ends_at < window_start:
            return []
        return [_occurrence(event, event.starts_at, duration, occurrence_key=None)]

    zone = resolve_timezone(event.timezone, fallback=server_timezone)
    local_start = datetime.fromtimestamp(event.starts_at, tz=UTC).astimezone(zone)
    # Convert the query window into the event's zone as well, otherwise a
    # rule whose "09:00" is interpreted in another offset would drift.
    local_window_start = datetime.fromtimestamp(window_start, tz=UTC).astimezone(zone)
    local_window_end = datetime.fromtimestamp(window_end, tz=UTC).astimezone(zone)

    try:
        rule = _build_rrule(event.recurrence_rule, local_start)
    except Exception:  # noqa: BLE001 — a stored rule that no longer parses
        logger.exception(
            "FamilyCalendar: event %s has an unparseable recurrence rule", event.id
        )
        return []

    out: list[CalendarOccurrence] = []
    # ``after`` is strict (the occurrence must be *greater* than the
    # cursor), which is what makes the cursor-then-advance loop
    # terminate. Widening the window by a second on each side keeps an
    # occurrence sitting exactly on the boundary included.
    cursor = local_window_start - timedelta(seconds=1)
    stop = local_window_end + timedelta(seconds=1)
    while len(out) < remaining:
        moment = rule.after(cursor)
        if moment is None or moment > stop:
            break
        cursor = moment
        starts_at = int(moment.timestamp())
        if starts_at > window_end:
            break
        if starts_at + duration < window_start:
            continue
        out.append(_occurrence(event, starts_at, duration, occurrence_key=str(starts_at)))
    return out


def _occurrence(
    event: FamilyCalendarEventRow,
    starts_at: int,
    duration: int,
    *,
    occurrence_key: str | None,
) -> CalendarOccurrence:
    return CalendarOccurrence(
        event_id=event.id,
        calendar_id=event.calendar_id,
        family_id=event.family_id,
        title=event.title,
        description=event.description,
        location=event.location,
        starts_at=starts_at,
        ends_at=starts_at + duration,
        all_day=event.all_day,
        timezone=event.timezone,
        status=event.status,
        source_type=event.source_type,
        source_id=event.source_id,
        occurrence_key=occurrence_key or str(event.starts_at),
        is_recurring=event.recurrence_rule is not None,
    )


def _rule_until(rule: str) -> int | None:
    """Epoch seconds of a rule's ``UNTIL``, if it has one."""
    for part in rule.split(";"):
        key, _, value = part.partition("=")
        if key.strip().upper() != "UNTIL":
            continue
        text = value.strip()
        for fmt in ("%Y%m%dT%H%M%SZ", "%Y%m%dT%H%M%S", "%Y%m%d"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return int(parsed.timestamp())
    return None


__all__ = [
    "MAX_OCCURRENCES_PER_QUERY",
    "MAX_OCCURRENCE_WINDOW_DAYS",
    "CalendarOccurrence",
    "FamilyCalendarManager",
    "expand_occurrences",
    "normalize_recurrence_rule",
    "resolve_timezone",
    "validate_timezone",
]