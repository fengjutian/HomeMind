"""Calendar, occurrence and reminder behaviour (Stage 1).

The interesting cases here are the ones a naive implementation gets
wrong: a weekly meeting that crosses a DST boundary, an all-day event,
a window that asks for too much, and a reminder that must fire exactly
once even when two runners race.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.calendar import (
    MAX_OCCURRENCES_PER_QUERY,
    FamilyCalendarManager,
    expand_occurrences,
    normalize_recurrence_rule,
)
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.reminder_runner import ReminderRunner
from homemind.infra.family.reminders import FamilyReminderManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _epoch(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=UTC).timestamp())


def _soon(*, hours: int) -> int:
    """A timestamp ``hours`` from now, rounded to the hour.

    Reminder tests need a date inside the rolling 30-day horizon, which
    a hard-coded calendar date would silently drift out of.
    """
    from datetime import timedelta

    moment = datetime.now(UTC) + timedelta(hours=hours)
    return int(moment.replace(minute=0, second=0, microsecond=0).timestamp())


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — pytest fixture returning a small namespace
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse"), (3, "outsider")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Calendar Family", timezone="Asia/Shanghai", locale="zh"
    )
    spouse_member = families.create_member(
        family.id, owner, display_name="Spouse", role=MemberRole.MEMBER, user_id=2
    )
    calendars = FamilyCalendarManager(
        families, services.family_calendar_repo, server_timezone="Asia/Shanghai"
    )
    reminders = FamilyReminderManager(families, services.family_reminder_repo)
    return {
        "pool": pool,
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
        "spouse": User(2, "spouse", Role.USER, "Spouse"),
        "outsider": User(3, "outsider", Role.USER, "Outsider"),
        "member": spouse_member,
        "calendars": calendars,
        "reminders": reminders,
    }


# ----------------------------------------------------------------- migrations


def test_calendar_tables_exist_after_migration(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        names = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    assert {
        "homemind_family_calendars",
        "homemind_family_calendar_events",
        "homemind_family_reminders",
    } <= names


def test_schema_version_reaches_18(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        row = conn.execute("SELECT version FROM _homemind_schema_version").fetchone()
    assert int(row[0]) == 18


# ------------------------------------------------------------------- rrules


def test_normalize_recurrence_rule_canonicalizes_prefix_and_case() -> None:
    assert normalize_recurrence_rule("rrule:freq=weekly;byday=mo") == "FREQ=WEEKLY;BYDAY=MO"
    assert normalize_recurrence_rule("  FREQ=DAILY  ") == "FREQ=DAILY"
    assert normalize_recurrence_rule(None) is None
    assert normalize_recurrence_rule("   ") is None


def test_normalize_recurrence_rule_rejects_natural_language() -> None:
    with pytest.raises(HomeMindError) as excinfo:
        normalize_recurrence_rule("every other tuesday")
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


# ---------------------------------------------------------------- occurrences


def test_weekly_event_produces_occurrences_in_the_server_timezone(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Swim class",
        starts_at=_epoch(2026, 3, 2, 1, 0),
        ends_at=_epoch(2026, 3, 2, 2, 0),
        timezone="Asia/Shanghai",
        recurrence_rule="FREQ=WEEKLY;BYDAY=MO",
    )

    occurrences = calendars.occurrences(
        family.id,
        owner,
        window_start=_epoch(2026, 3, 1),
        window_end=_epoch(2026, 3, 30),
    )
    assert [o.starts_at for o in occurrences] == [
        _epoch(2026, 3, 2, 1, 0),
        _epoch(2026, 3, 9, 1, 0),
        _epoch(2026, 3, 16, 1, 0),
        _epoch(2026, 3, 23, 1, 0),
    ]
    # Each occurrence keeps the event's own duration.
    assert all(o.ends_at - o.starts_at == 3600 for o in occurrences)
    assert all(o.occurrence_key == str(o.starts_at) for o in occurrences)
    assert all(o.is_recurring for o in occurrences)
    assert event.recurrence_rule == "FREQ=WEEKLY;BYDAY=MO"


def test_weekly_event_keeps_local_time_across_a_dst_boundary(env) -> None:  # noqa: ANN001
    """09:00 in New York stays 09:00 even though its UTC instant shifts.

    US DST starts 2026-03-08. A rule evaluated in UTC would drift to
    10:00 local; evaluating it in the event's own zone is the whole
    point of persisting the timezone next to the instant.
    """
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(
        family.id, owner, name="Trips", timezone="America/New_York"
    )
    new_york = ZoneInfo("America/New_York")
    calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Dentist",
        starts_at=int(datetime(2026, 3, 2, 9, 0, tzinfo=new_york).timestamp()),
        ends_at=int(datetime(2026, 3, 2, 10, 0, tzinfo=new_york).timestamp()),
        recurrence_rule="FREQ=WEEKLY",
    )

    occurrences = calendars.occurrences(
        family.id,
        owner,
        window_start=_epoch(2026, 3, 1),
        window_end=_epoch(2026, 3, 20),
    )
    assert len(occurrences) == 3
    local_hours = [
        datetime.fromtimestamp(o.starts_at, tz=UTC).astimezone(new_york).hour
        for o in occurrences
    ]
    assert local_hours == [9, 9, 9]
    # And the UTC instants genuinely differ, proving the rule was not
    # simply replayed at a fixed offset.
    offsets = {
        datetime.fromtimestamp(o.starts_at, tz=UTC).astimezone(new_york).utcoffset()
        for o in occurrences
    }
    assert len(offsets) == 2


def test_all_day_event_spans_a_whole_local_day(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="House")
    calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="School holiday",
        starts_at=_epoch(2026, 5, 1),
        ends_at=_epoch(2026, 5, 2),
        all_day=True,
    )
    occurrences = calendars.occurrences(
        family.id, owner, window_start=_epoch(2026, 4, 30), window_end=_epoch(2026, 5, 3)
    )
    assert len(occurrences) == 1
    assert occurrences[0].all_day is True


def test_event_occurring_across_midnight_is_returned_for_both_days(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Work")
    calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Night shift",
        starts_at=_epoch(2026, 6, 10, 22, 0),
        ends_at=_epoch(2026, 6, 11, 6, 0),
    )
    first = calendars.occurrences(
        family.id, owner, window_start=_epoch(2026, 6, 10), window_end=_epoch(2026, 6, 10, 23, 59)
    )
    second = calendars.occurrences(
        family.id, owner, window_start=_epoch(2026, 6, 11), window_end=_epoch(2026, 6, 11, 23, 59)
    )
    assert len(first) == 1
    assert len(second) == 1
    assert first[0].starts_at == second[0].starts_at


def test_recurrence_until_stops_the_series(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Course")
    calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Course",
        starts_at=_epoch(2026, 1, 5, 9, 0),
        ends_at=_epoch(2026, 1, 5, 10, 0),
        recurrence_rule="FREQ=DAILY;UNTIL=20260107T235959Z",
    )
    occurrences = calendars.occurrences(
        family.id, owner, window_start=_epoch(2026, 1, 1), window_end=_epoch(2026, 1, 31)
    )
    assert [o.starts_at for o in occurrences] == [
        _epoch(2026, 1, 5, 9, 0),
        _epoch(2026, 1, 6, 9, 0),
        _epoch(2026, 1, 7, 9, 0),
    ]


def test_occurrence_query_is_capped(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Daily")
    # Two daily series over a year is 730 occurrences; the cap has to
    # hold, or one wide request becomes an unbounded expansion.
    for hour, title in ((8, "Pills"), (20, "Vitamin D")):
        calendars.create_event(
            family.id,
            owner,
            calendar_id=calendar.id,
            title=title,
            starts_at=_epoch(2026, 1, 1, hour, 0),
            ends_at=_epoch(2026, 1, 1, hour, 5),
            recurrence_rule="FREQ=DAILY",
        )
    occurrences = calendars.occurrences(
        family.id, owner, window_start=_epoch(2026, 1, 1), window_end=_epoch(2027, 1, 1)
    )
    assert len(occurrences) == MAX_OCCURRENCES_PER_QUERY
    # The cap truncates rather than reorders: still sorted by start time.
    assert [o.starts_at for o in occurrences] == sorted(o.starts_at for o in occurrences)


def test_occurrence_window_wider_than_a_year_is_rejected(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    with pytest.raises(HomeMindError) as excinfo:
        env["calendars"].occurrences(
            family.id, owner, window_start=_epoch(2026, 1, 1), window_end=_epoch(2036, 1, 1)
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_cancelled_event_disappears_from_occurrences(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Cancelled thing",
        starts_at=_epoch(2026, 4, 1, 10, 0),
        ends_at=_epoch(2026, 4, 1, 11, 0),
    )
    calendars.cancel_event(family.id, event.id, owner)
    assert (
        calendars.occurrences(
            family.id, owner, window_start=_epoch(2026, 4, 1), window_end=_epoch(2026, 4, 2)
        )
        == []
    )
    # The row survives so history can still show it.
    assert calendars.get_event(family.id, event.id, owner).status == "CANCELLED"


def test_occurrence_helpers_handle_a_short_window(env) -> None:  # noqa: ANN001
    """A window that fits inside one occurrence still returns it."""

    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Long",
        starts_at=_epoch(2026, 7, 1, 0, 0),
        ends_at=_epoch(2026, 7, 1, 23, 0),
    )
    occurrences = expand_occurrences(
        calendars.get_event(family.id, event.id, owner),
        window_start=_epoch(2026, 7, 1, 12, 0),
        window_end=_epoch(2026, 7, 1, 13, 0),
    )
    assert len(occurrences) == 1


# ----------------------------------------------------------------- versioning


def test_expected_version_guards_a_concurrent_edit(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Original",
        starts_at=_epoch(2026, 8, 1, 10, 0),
        ends_at=_epoch(2026, 8, 1, 11, 0),
    )
    calendars.update_event(
        family.id, event.id, owner, {"title": "First edit"}, expected_version=event.version
    )
    with pytest.raises(HomeMindError) as excinfo:
        calendars.update_event(
            family.id, event.id, owner, {"title": "Stale edit"}, expected_version=event.version
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_CONFLICT
    assert calendars.get_event(family.id, event.id, owner).title == "First edit"


def test_event_bumps_its_version_on_every_update(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="v1",
        starts_at=_epoch(2026, 8, 1, 10, 0),
        ends_at=_epoch(2026, 8, 1, 11, 0),
    )
    assert event.version == 1
    assert calendars.update_event(family.id, event.id, owner, {"title": "v2"}).version == 2


def test_conflicting_recurrence_until_is_rejected(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    with pytest.raises(HomeMindError) as excinfo:
        calendars.create_event(
            family.id,
            owner,
            calendar_id=calendar.id,
            title="Conflicting",
            starts_at=_epoch(2026, 9, 1, 10, 0),
            ends_at=_epoch(2026, 9, 1, 11, 0),
            recurrence_rule="FREQ=DAILY;UNTIL=20260905T235959Z",
            recurrence_until=_epoch(2026, 12, 31),
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_event_must_end_after_it_starts(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    with pytest.raises(HomeMindError) as excinfo:
        calendars.create_event(
            family.id,
            owner,
            calendar_id=calendar.id,
            title="Backwards",
            starts_at=_epoch(2026, 9, 1, 11, 0),
            ends_at=_epoch(2026, 9, 1, 10, 0),
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


# ---------------------------------------------------------------- permissions


def test_outsider_cannot_read_a_family_calendar(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    outsider = env["outsider"]
    calendars = env["calendars"]
    calendars.create_calendar(family.id, owner, name="Private plans")
    with pytest.raises(OctopError) as excinfo:
        calendars.list_calendars(family.id, outsider)
    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_calendar_of_another_family_is_not_found(env) -> None:  # noqa: ANN001
    owner = env["owner"]
    spouse = env["spouse"]
    families = env["families"]
    other = families.create_family(
        spouse, name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    calendar = env["calendars"].create_calendar(family_id := other.id, user=spouse, name="X")
    with pytest.raises(OctopError) as excinfo:
        env["calendars"].get_calendar(family_id, calendar.id, owner)
    assert excinfo.value.code in {ErrorCode.NOT_FOUND, ErrorCode.FORBIDDEN}


# ------------------------------------------------------------------ reminders


def test_reminder_is_created_once_even_when_scheduled_twice(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    reminders = env["reminders"]
    task = env["services"].family_task_repo.create(
        family.id, title="Buy formula", description="", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    first = reminders.create(
        family.id, owner, target_type="TASK", target_id=task.id, remind_at=_epoch(2026, 5, 1)
    )
    second = reminders.create(
        family.id, owner, target_type="TASK", target_id=task.id, remind_at=_epoch(2026, 5, 1)
    )
    assert first.id == second.id
    assert len(reminders.list_for_family(family.id, owner)) == 1


def test_reminder_for_a_removed_member_is_not_created(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    reminders = env["reminders"]
    with pytest.raises(OctopError) as excinfo:
        reminders.create(
            family.id,
            owner,
            target_type="TASK",
            target_id="whatever",
            remind_at=_epoch(2026, 5, 1),
            recipient_member_id="member-that-does-not-exist",
        )
    assert excinfo.value.code is ErrorCode.NOT_FOUND


def test_moving_an_event_cancels_the_old_reminder_and_creates_a_new_one(env) -> None:  # noqa: ANN001
    """The plan's core promise: edit the time, the old reminder dies."""

    family = env["family"]
    owner = env["owner"]
    member = env["member"]
    calendars = env["calendars"]
    reminders = env["reminders"]
    calendars.reminders = reminders

    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    # Inside the rolling horizon: reminders are only materialised for
    # the next 30 days, so a date a year out legitimately has none.
    starts = _soon(hours=24 * 7)
    owner_member = env["families"].repo.find_member_by_user(family.id, owner.id)
    owner_member_id = owner_member.id if owner_member else ""
    spouse_id = member.id
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Grandma's appointment",
        starts_at=starts,
        ends_at=starts + 3600,
        timezone="Asia/Shanghai",
    )
    before = reminders.list_for_target("CALENDAR_EVENT", event.id)
    # One reminder per active member, each addressed individually.
    assert len(before) == 2
    assert {r.recipient_member_id for r in before} == {owner_member_id, spouse_id}
    assert all(r.status == "PENDING" for r in before)
    assert all(r.remind_at == starts - 3600 for r in before)

    calendars.update_event(family.id, event.id, owner, {"starts_at": starts + 86400})
    after = reminders.list_for_target("CALENDAR_EVENT", event.id)
    live = [r for r in after if r.status == "PENDING"]
    assert len(live) == 2
    assert all(r.remind_at == starts + 86400 - 3600 for r in live)
    # And the two old rows are retired rather than left to fire.
    assert len([r for r in after if r.status == "CANCELLED"]) == 2


def test_a_reminder_is_only_created_for_a_member_the_family_still_has(env) -> None:  # noqa: ANN001
    """A removed member must not be told about what happens next."""

    family = env["family"]
    owner = env["owner"]
    reminders = env["reminders"]
    calendars = env["calendars"]
    calendars.reminders = reminders
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    starts = _soon(hours=24 * 3)
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Piano lesson",
        starts_at=starts,
        ends_at=starts + 1800,
    )
    # One reminder per active member, addressed to each by member id.
    recipients = {
        r.recipient_member_id for r in reminders.list_for_target("CALENDAR_EVENT", event.id)
    }
    assert env["member"].id in recipients

    env["families"].delete_member(family.id, env["member"].id, owner)
    calendars.update_event(family.id, event.id, owner, {"title": "Piano lesson (moved)"})
    live = [
        r
        for r in reminders.list_for_target("CALENDAR_EVENT", event.id)
        if r.status == "PENDING"
    ]
    assert env["member"].id not in {r.recipient_member_id for r in live}


def test_cancelling_an_event_leaves_no_live_reminder(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    calendars = env["calendars"]
    reminders = env["reminders"]
    calendars.reminders = reminders
    calendar = calendars.create_calendar(family.id, owner, name="Shared")
    starts = _soon(hours=24 * 5)
    event = calendars.create_event(
        family.id,
        owner,
        calendar_id=calendar.id,
        title="Something",
        starts_at=starts,
        ends_at=starts + 3600,
    )
    assert len(reminders.list_for_target("CALENDAR_EVENT", event.id)) == 2
    calendars.cancel_event(family.id, event.id, owner)
    live = [
        r
        for r in reminders.list_for_target("CALENDAR_EVENT", event.id)
        if r.status in {"PENDING", "CLAIMED"}
    ]
    assert live == []


def test_task_due_at_change_resyncs_the_task_reminder(env) -> None:  # noqa: ANN001
    from homemind.infra.family.tasks import FamilyTaskManager

    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]
    tasks = FamilyTaskManager(env["families"], services.family_task_repo, reminders=reminders)

    task = tasks.create(family.id, owner, title="Pay tuition", due_at=_epoch(2026, 4, 1, 8, 0))
    reminders.sync_task_reminder(
        family_id=family.id,
        task_id=task.id,
        due_at=task.due_at,
        recipient_member_id=None,
    )
    original = reminders.list_for_target("TASK", task.id)
    assert len(original) == 1

    moved = tasks.update(family.id, task.id, owner, {"due_at": _epoch(2026, 4, 8, 8, 0)})
    assert moved.due_at == _epoch(2026, 4, 8, 8, 0)
    live = [r for r in reminders.list_for_target("TASK", task.id) if r.status == "PENDING"]
    assert len(live) == 1
    assert live[0].remind_at == _epoch(2026, 4, 8, 8, 0) - 3600


def test_clearing_a_task_due_date_removes_its_reminder(env) -> None:  # noqa: ANN001
    from homemind.infra.family.tasks import FamilyTaskManager

    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]
    tasks = FamilyTaskManager(env["families"], services.family_task_repo, reminders=reminders)
    task = tasks.create(family.id, owner, title="Renew passport", due_at=_epoch(2026, 4, 1))
    reminders.sync_task_reminder(
        family_id=family.id, task_id=task.id, due_at=task.due_at, recipient_member_id=None
    )
    tasks.update(family.id, task.id, owner, {"due_at": None})
    live = [
        r for r in reminders.list_for_target("TASK", task.id) if r.status in {"PENDING", "CLAIMED"}
    ]
    assert live == []


def test_deleting_a_task_removes_its_reminders(env) -> None:  # noqa: ANN001
    from homemind.infra.family.tasks import FamilyTaskManager

    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]
    tasks = FamilyTaskManager(env["families"], services.family_task_repo, reminders=reminders)
    task = tasks.create(family.id, owner, title="Book dentist", due_at=_epoch(2026, 4, 1))
    reminders.sync_task_reminder(
        family_id=family.id, task_id=task.id, due_at=task.due_at, recipient_member_id=None
    )
    tasks.delete(family.id, task.id, owner)
    assert reminders.list_for_target("TASK", task.id) == []


# ------------------------------------------------------------- claim / lease


def test_claiming_a_reminder_takes_it_out_of_reach_of_a_second_runner(env) -> None:  # noqa: ANN001
    repo = env["services"].family_reminder_repo
    repo.create(
        env["family"].id,
        target_type="TASK",
        target_id="t1",
        remind_at=1000,
        dedupe_key="k1",
    )
    first = repo.claim_due(owner="worker-a", ttl_seconds=60, now=1000)
    second = repo.claim_due(owner="worker-b", ttl_seconds=60, now=1000)
    assert len(first) == 1
    assert second == []


def test_expired_lease_is_recovered_on_boot(env) -> None:  # noqa: ANN001
    repo = env["services"].family_reminder_repo
    repo.create(
        env["family"].id,
        target_type="TASK",
        target_id="t1",
        remind_at=1000,
        dedupe_key="k1",
    )
    claimed = repo.claim_due(owner="worker-a", ttl_seconds=10, now=1000)
    assert claimed[0].status == "CLAIMED"
    # Nothing to recover while the lease is still live.
    assert repo.recover_stale_leases(now=1005) == 0
    assert repo.recover_stale_leases(now=1100) == 1
    assert repo.get(claimed[0].id).status == "PENDING"
    assert repo.get(claimed[0].id).lease_owner is None


def test_delivery_resolves_the_target_title(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    reminders = env["reminders"]
    services = env["services"]
    task = services.family_task_repo.create(
        family.id, title="Pick up the cake", description="chocolate", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=1000,
        dedupe_key="cake",
    )
    dispatches = reminders.claim_due(owner="worker", ttl_seconds=60, now=1000)
    assert len(dispatches) == 1
    assert dispatches[0].target_title == "Pick up the cake"
    assert dispatches[0].target_description == "chocolate"


def test_delivery_of_a_deleted_target_fails_permanently(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    reminders = env["reminders"]
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id="task-that-never-existed",
        remind_at=1000,
        dedupe_key="ghost",
    )
    dispatches = reminders.claim_due(owner="worker", ttl_seconds=60, now=1000)
    assert dispatches == []
    rows = reminders.repo.list_for_family(family.id)
    assert rows[0].status == "FAILED"
    assert "no longer exists" in (rows[0].last_error or "")


def test_backoff_grows_and_is_capped(env) -> None:  # noqa: ANN001
    reminders = env["reminders"]
    assert reminders.backoff_seconds(0) == 30
    assert reminders.backoff_seconds(1) == 30
    assert reminders.backoff_seconds(2) == 60
    assert reminders.backoff_seconds(3) == 120
    assert reminders.backoff_seconds(50) == 3600


# -------------------------------------------------------------------- runner


@pytest.mark.asyncio
async def test_runner_delivers_a_due_reminder_and_marks_it_sent(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]

    class _Bus:
        def __init__(self) -> None:
            self.events: list[tuple[str, str, dict]] = []

        async def emit(self, event) -> int:  # noqa: ANN001 - minimal bus stand-in
            self.events.append((event.event_type, event.family_id, event.payload))
            return 1

    bus = _Bus()
    task = services.family_task_repo.create(
        family.id, title="Call the plumber", description="", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=1000,
        dedupe_key="plumber",
    )
    runner = ReminderRunner(
        manager=reminders,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        worker_id="test-runner",
        event_bus=lambda: bus,
    )
    delivered = await runner.drain_once()
    assert delivered == 1
    assert len(bus.events) == 1
    event_type, family_id, payload = bus.events[0]
    assert event_type == "family.reminder.due"
    assert family_id == family.id
    assert payload["target_title"] == "Call the plumber"
    stored = reminders.repo.list_for_family(family.id)[0]
    assert stored.status == "SENT"


@pytest.mark.asyncio
async def test_runner_does_not_redeliver_a_sent_reminder(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]
    task = services.family_task_repo.create(
        family.id, title="Feed the cat", description="", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=1000,
        dedupe_key="cat",
    )
    runner = ReminderRunner(
        manager=reminders,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        worker_id="test-runner",
    )
    assert await runner.drain_once() == 1
    assert await runner.drain_once() == 0


@pytest.mark.asyncio
async def test_runner_retries_a_failed_delivery_then_retires_it(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]

    class _BrokenBus:
        async def emit(self, event) -> int:  # noqa: ANN001
            raise RuntimeError("gateway down")

    task = services.family_task_repo.create(
        family.id, title="Sign the form", description="", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=1000,
        dedupe_key="form",
    )
    runner = ReminderRunner(
        manager=reminders,
        poll_interval_seconds=0.01,
        lease_seconds=60,
        max_attempts=2,
        worker_id="test-runner",
        event_bus=lambda: _BrokenBus(),
    )
    assert await runner.drain_once() == 0
    assert reminders.repo.list_for_family(family.id)[0].status == "PENDING"
    # Second failure crosses max_attempts and retires the reminder.
    assert await runner.drain_once() == 0
    assert reminders.repo.list_for_family(family.id)[0].status == "FAILED"


@pytest.mark.asyncio
async def test_runner_start_recovers_a_lease_left_by_a_dead_worker(env) -> None:  # noqa: ANN001
    family = env["family"]
    owner = env["owner"]
    services = env["services"]
    reminders = env["reminders"]
    task = services.family_task_repo.create(
        family.id, title="Return the library book", description="", assigned_member_id=None,
        due_at=None, created_by=owner.id,
    )
    reminders.repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=int(datetime.now(UTC).timestamp()) - 10,
        dedupe_key="library",
    )
    # A worker that died mid-delivery leaves the row CLAIMED with an
    # expired lease; booting must hand it back.
    reminders.repo.claim_due(owner="dead-worker", ttl_seconds=1, now=0)
    reminders.repo.claim_due(owner="dead-worker", ttl_seconds=1, now=1)

    runner = ReminderRunner(
        manager=reminders, poll_interval_seconds=3600, worker_id="new-worker"
    )
    await runner.start()
    try:
        assert await runner.drain_once() == 1
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_runner_stop_is_idempotent(env) -> None:  # noqa: ANN001
    runner = ReminderRunner(
        manager=env["reminders"], poll_interval_seconds=0.01, worker_id="idle"
    )
    await runner.start()
    await runner.stop()
    await runner.stop()
