"""Notification centre behaviour (Stage 2).

The properties worth pinning here are the ones that make the inbox
trustworthy: a trigger fired twice produces one row, a muted user gets
nothing, a member removed from the family is never notified, and the
unread count survives a process restart because it is derived from
storage rather than from a socket.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_notifications import (
    SEVERITY_CRITICAL,
    TYPE_APPROVAL_WAITING,
    TYPE_CALENDAR_REMINDER,
    TYPE_DEVICE_OFFLINE,
    TYPE_TASK_DUE,
    FamilyNotificationRepo,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.notifications import (
    NotificationManager,
    NotificationRequest,
    build_dedupe_key,
)
from homemind.infra.family.reminder_runner import ReminderRunner
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _epoch(year: int, month: int, day: int, hour: int = 12) -> int:
    return int(datetime(year, month, day, hour, tzinfo=UTC).timestamp())


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
    spouse_user = User(2, "spouse", Role.USER, "Spouse")
    family = families.create_family(
        owner, name="Notify Family", timezone="Asia/Shanghai", locale="zh"
    )
    spouse = families.create_member(
        family.id, owner, display_name="Spouse", role=MemberRole.MEMBER, user_id=2
    )
    notifications = NotificationManager(
        families, services.family_notification_repo, server_timezone="Asia/Shanghai"
    )
    return {
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
        "spouse": spouse_user,
        "member": spouse,
        "notifications": notifications,
    }


def _due_request(**overrides: object) -> NotificationRequest:
    payload: dict[str, object] = {
        "type": TYPE_TASK_DUE,
        "params": {"task_id": "task-1", "title": "Pay tuition", "due_at": 1893456000},
        "target_type": "TASK",
        "target_id": "task-1",
    }
    payload.update(overrides)
    return NotificationRequest(**payload)  # type: ignore[arg-type]


# --------------------------------------------------------------------- emit


def test_emit_reaches_every_active_member(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    rows = notifications.emit(family.id, _due_request())
    assert len(rows) == 2
    assert {row.user_id for row in rows} == {owner.id, env["spouse"].id}
    assert all(row.severity == "INFO" for row in rows)
    # Copy is stored as keys, never as rendered text.
    assert rows[0].title_key == "notifications.taskDue.title"
    assert rows[0].params["title"] == "Pay tuition"


def test_emitting_the_same_thing_twice_creates_one_row(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    first = notifications.emit(family.id, _due_request(), recipients=[owner.id])
    second = notifications.emit(family.id, _due_request(), recipients=[owner.id])
    assert len(first) == 1
    assert second == []
    assert notifications.unread_count(family.id, owner) == 1


def test_dedupe_suffix_separates_two_notices_about_one_target(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    notifications.emit(family.id, _due_request(dedupe_suffix="first"), recipients=[owner.id])
    notifications.emit(family.id, _due_request(dedupe_suffix="second"), recipients=[owner.id])
    assert notifications.unread_count(family.id, owner) == 2


def test_emit_skips_members_without_a_linked_account(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    # A child profile with no login has nobody to notify.
    env["families"].create_member(
        family.id, owner, display_name="Baby", role=MemberRole.CHILD, user_id=None
    )
    rows = notifications.emit(family.id, _due_request())
    assert {row.user_id for row in rows} == {owner.id, env["spouse"].id}


def test_a_removed_member_is_never_notified(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    env["families"].delete_member(family.id, env["member"].id, owner)
    rows = notifications.emit(family.id, _due_request())
    assert env["spouse"].id not in {row.user_id for row in rows}


def test_emit_for_member_targets_exactly_that_member(env) -> None:  # noqa: ANN001
    family, notifications = env["family"], env["notifications"]
    rows = notifications.emit_for_member(family.id, env["member"].id, _due_request())
    assert [row.user_id for row in rows] == [env["spouse"].id]


def test_emit_for_a_member_without_account_writes_nothing(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    child = env["families"].create_member(
        family.id, owner, display_name="Baby", role=MemberRole.CHILD, user_id=None
    )
    assert notifications.emit_for_member(family.id, child.id, _due_request()) == []


def test_unknown_notification_type_is_rejected(env) -> None:  # noqa: ANN001
    family, notifications = env["family"], env["notifications"]
    with pytest.raises(HomeMindError) as excinfo:
        notifications.emit(family.id, _due_request(type="NOT_A_TYPE"))
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


# ------------------------------------------------------------ preferences


def test_preferences_default_when_never_set(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    prefs = notifications.get_prefs(family.id, owner)
    assert prefs.disabled_types == []
    assert prefs.quiet_hours_start is None
    assert prefs.timezone == "Asia/Shanghai"


def test_a_muted_type_is_never_written(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    notifications.save_prefs(family.id, owner, disabled_types=[TYPE_TASK_DUE])
    rows = notifications.emit(family.id, _due_request())
    # The spouse still gets it; only the muting user is spared.
    assert [row.user_id for row in rows] == [env["spouse"].id]
    assert notifications.unread_count(family.id, owner) == 0


def test_quiet_hours_suppress_delivery(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    # 00:00–23:59 covers every hour of the day.
    notifications.save_prefs(family.id, owner, quiet_hours_start=0, quiet_hours_end=1439)
    rows = notifications.emit(family.id, _due_request())
    assert owner.id not in {row.user_id for row in rows}


def test_quiet_hours_that_wrap_past_midnight(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    # 22:00 → 07:00, expressed in minutes from local midnight as the
    # API documents.
    notifications.save_prefs(family.id, owner, quiet_hours_start=22 * 60, quiet_hours_end=7 * 60)
    inside = notifications.is_suppressed(
        family.id, owner.id, TYPE_TASK_DUE, now=_epoch(2026, 5, 1, 23)
    )
    outside = notifications.is_suppressed(
        family.id, owner.id, TYPE_TASK_DUE, now=_epoch(2026, 5, 1, 12)
    )
    assert inside is True
    assert outside is False


def test_quiet_hours_outside_minutes_of_day_are_rejected(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    with pytest.raises(HomeMindError) as excinfo:
        notifications.save_prefs(
            family.id, owner, quiet_hours_start=22 * 3600, quiet_hours_end=7 * 60
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_a_critical_notification_breaks_through_quiet_hours(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    notifications.save_prefs(family.id, owner, quiet_hours_start=0, quiet_hours_end=1439)
    rows = notifications.emit(family.id, _due_request(severity=SEVERITY_CRITICAL))
    assert owner.id in {row.user_id for row in rows}


def test_partial_quiet_hours_are_rejected(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    with pytest.raises(HomeMindError):
        notifications.save_prefs(family.id, owner, quiet_hours_start=3600)


def test_unknown_disabled_type_is_rejected(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    with pytest.raises(HomeMindError):
        notifications.save_prefs(family.id, owner, disabled_types=["NOPE"])


# ------------------------------------------------------------------- reads


def test_unread_count_drops_as_notifications_are_read(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    rows = notifications.emit(family.id, _due_request())
    mine = [row for row in rows if row.user_id == owner.id]
    assert notifications.unread_count(family.id, owner) == 1
    read = notifications.mark_read(family.id, mine[0].id, owner)
    assert read.read_at is not None
    assert notifications.unread_count(family.id, owner) == 0


def test_mark_read_is_idempotent(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    rows = notifications.emit(family.id, _due_request(), recipients=[owner.id])
    first = notifications.mark_read(family.id, rows[0].id, owner)
    second = notifications.mark_read(family.id, rows[0].id, owner)
    # Re-stamping would destroy "when did you actually see this".
    assert first.read_at == second.read_at


def test_mark_all_read_clears_the_badge(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    notifications.emit(family.id, _due_request())
    notifications.notify_device_offline(family.id, device_id="d1", name="Hub")
    assert notifications.unread_count(family.id, owner) == 2
    assert notifications.mark_all_read(family.id, owner) == 2
    assert notifications.unread_count(family.id, owner) == 0


def test_one_user_cannot_read_another_users_notification(env) -> None:  # noqa: ANN001
    family, notifications = env["family"], env["notifications"]
    rows = notifications.emit(family.id, _due_request(), recipients=[env["owner"].id])
    with pytest.raises(OctopError):
        notifications.mark_read(family.id, rows[0].id, env["spouse"])


def test_a_notification_never_appears_in_another_users_inbox(env) -> None:  # noqa: ANN001
    family, notifications = env["family"], env["notifications"]
    notifications.emit(family.id, _due_request(), recipients=[env["owner"].id])
    assert notifications.list_inbox(family.id, env["spouse"]) == []


def test_outside_family_gets_forbidden_not_zero(env) -> None:  # noqa: ANN001
    family, notifications = env["family"], env["notifications"]
    stranger = User(99, "stranger", Role.USER, "Stranger")
    with pytest.raises(OctopError) as excinfo:
        notifications.unread_count(family.id, stranger)
    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_inbox_is_ordered_newest_first(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    now = int(datetime.now(UTC).timestamp())
    notifications.emit(
        family.id,
        NotificationRequest(
            type=TYPE_APPROVAL_WAITING,
            params={"action": "filesystem.move"},
            target_type="APPROVAL",
            target_id="a1",
        ),
        recipients=[owner.id],
        now=now - 3600,
    )
    notifications.emit(
        family.id,
        NotificationRequest(
            type=TYPE_DEVICE_OFFLINE,
            params={"name": "Hub"},
            target_type="DEVICE",
            target_id="d1",
        ),
        recipients=[owner.id],
        now=now,
    )
    inbox = notifications.list_inbox(family.id, owner)
    assert [row.type for row in inbox] == [TYPE_DEVICE_OFFLINE, TYPE_APPROVAL_WAITING]


def test_inbox_can_be_filtered_by_type_and_unread(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    notifications.emit(
        family.id,
        NotificationRequest(
            type=TYPE_APPROVAL_WAITING,
            params={"action": "x"},
            target_type="APPROVAL",
            target_id="a1",
        ),
        recipients=[owner.id],
    )
    notifications.notify_device_offline(family.id, device_id="d1", name="Hub")
    assert len(notifications.list_inbox(family.id, owner, type=TYPE_DEVICE_OFFLINE)) == 1
    notifications.mark_all_read(family.id, owner)
    assert notifications.list_inbox(family.id, owner, unread_only=True) == []


def test_expired_notifications_disappear_from_the_inbox(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    now = int(datetime.now(UTC).timestamp())
    notifications.emit(
        family.id,
        NotificationRequest(
            type=TYPE_TASK_DUE,
            params={"title": "Old"},
            target_type="TASK",
            target_id="task-old",
            ttl_days=1,
        ),
        recipients=[owner.id],
        now=now - 86400 * 3,
    )
    assert notifications.list_inbox(family.id, owner) == []
    assert notifications.purge_expired(family.id, owner) == 1


def test_global_purge_only_removes_lapsed_rows(env) -> None:  # noqa: ANN001
    family, owner, notifications = env["family"], env["owner"], env["notifications"]
    now = int(datetime.now(UTC).timestamp())
    notifications.emit(
        family.id,
        NotificationRequest(
            type=TYPE_TASK_DUE,
            params={"title": "Old"},
            target_type="TASK",
            target_id="old",
            ttl_days=1,
        ),
        recipients=[owner.id],
        now=now - 86400 * 5,
    )
    notifications.emit(family.id, _due_request(), recipients=[owner.id])
    assert notifications.purge_expired_globally() == 1
    assert notifications.unread_count(family.id, owner) == 1


def test_dedupe_key_is_stable_and_scoped_per_user() -> None:
    base = {
        "family_id": "F",
        "type": TYPE_TASK_DUE,
        "target_type": "TASK",
        "target_id": "1",
        "dedupe_suffix": None,
    }
    assert build_dedupe_key(**base, user_id=1) == build_dedupe_key(**base, user_id=1)
    assert build_dedupe_key(**base, user_id=1) != build_dedupe_key(**base, user_id=2)


# ------------------------------------------------------- reminder -> inbox


@pytest.mark.asyncio
async def test_a_delivered_reminder_lands_in_the_recipient_inbox(env) -> None:  # noqa: ANN001
    family, services = env["family"], env["services"]
    notifications = env["notifications"]
    task = services.family_task_repo.create(
        family.id,
        title="Call the plumber",
        description="",
        assigned_member_id=None,
        due_at=None,
        created_by=env["owner"].id,
    )
    now = int(datetime.now(UTC).timestamp())
    services.family_reminder_repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=now - 60,
        dedupe_key="plumber",
        recipient_member_id=env["member"].id,
        recipient_user_id=env["spouse"].id,
    )

    from homemind.infra.family.reminders import FamilyReminderManager

    runner = ReminderRunner(
        manager=FamilyReminderManager(env["families"], services.family_reminder_repo),
        notifications=notifications,
        poll_interval_seconds=0.01,
        worker_id="test-runner",
    )
    assert await runner.drain_once() == 1

    inbox = notifications.list_inbox(family.id, env["spouse"])
    assert len(inbox) == 1
    assert inbox[0].type == TYPE_CALENDAR_REMINDER
    assert inbox[0].params["title"] == "Call the plumber"
    # Only the addressed member was told.
    assert notifications.unread_count(family.id, env["owner"]) == 0


@pytest.mark.asyncio
async def test_reminder_delivery_writes_the_inbox_only_once(env) -> None:  # noqa: ANN001
    """A replayed delivery must not stack a second bell."""
    family, services = env["family"], env["services"]
    notifications = env["notifications"]
    task = services.family_task_repo.create(
        family.id,
        title="Feed the cat",
        description="",
        assigned_member_id=None,
        due_at=None,
        created_by=env["owner"].id,
    )
    now = int(datetime.now(UTC).timestamp())
    services.family_reminder_repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=now - 60,
        dedupe_key="cat",
        recipient_member_id=env["member"].id,
        recipient_user_id=env["spouse"].id,
    )
    from homemind.infra.family.reminders import FamilyReminderManager

    runner = ReminderRunner(
        manager=FamilyReminderManager(env["families"], services.family_reminder_repo),
        notifications=notifications,
        poll_interval_seconds=0.01,
        worker_id="test-runner",
    )
    assert await runner.drain_once() == 1

    # Force the row back into the queue and deliver it a second time,
    # exactly as a crash-and-recover cycle would.
    sent = services.family_reminder_repo.list_for_family(family.id)[0]
    services.family_reminder_repo.mark_failed(sent.id, error="replay", now=now - 60)
    assert await runner.drain_once() == 1
    assert len(notifications.list_inbox(family.id, env["spouse"])) == 1


@pytest.mark.asyncio
async def test_runner_still_works_without_a_notification_manager(env) -> None:  # noqa: ANN001
    family, services = env["family"], env["services"]
    task = services.family_task_repo.create(
        family.id,
        title="No inbox needed",
        description="",
        assigned_member_id=None,
        due_at=None,
        created_by=env["owner"].id,
    )

    services.family_reminder_repo.create(
        family.id,
        target_type="TASK",
        target_id=task.id,
        remind_at=1000,
        dedupe_key="nobox",
    )
    from homemind.infra.family.reminders import FamilyReminderManager

    runner = ReminderRunner(
        manager=FamilyReminderManager(env["families"], services.family_reminder_repo),
        poll_interval_seconds=0.01,
        worker_id="test-runner",
    )
    assert await runner.drain_once() == 1


def test_notifications_survive_a_new_repo_instance(tmp_path: Path, env) -> None:  # noqa: ANN001
    """The badge is derived from storage, so a restart loses nothing."""
    family, owner = env["family"], env["owner"]
    env["notifications"].emit(family.id, _due_request(), recipients=[owner.id])

    fresh = NotificationManager(
        FamilyManager(env["services"].family_repo),
        FamilyNotificationRepo(env["services"].db),
    )
    assert fresh.unread_count(family.id, owner) == 1
    assert len(fresh.list_inbox(family.id, owner)) == 1
