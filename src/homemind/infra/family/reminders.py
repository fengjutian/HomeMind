"""Family reminder domain service.

Reminders are *derived* state. A calendar event that moves, a task whose
due date changes, a device that goes offline -- each of those must
invalidate the reminder it produced, or the family gets told about a time
that no longer exists. That derivation lives here; the delivery loop
lives in :mod:`homemind.infra.family.reminder_runner`.

The dedupe key is the contract that makes "exactly one reminder per
occurrence per recipient" hold: it is derived from the target, the
occurrence, the recipient and the channel, so two calls to
:func:`schedule` -- or a restart halfway through -- converge on one row.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass

from homemind.infra.db.repos.family_reminders import (
    CHANNEL_IN_APP,
    STATUS_CANCELLED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_SENT,
    TARGET_TYPES,
    TARGET_TYPE_APPROVAL,
    TARGET_TYPE_CALENDAR_EVENT,
    TARGET_TYPE_DEVICE,
    TARGET_TYPE_TASK,
    FamilyReminderRepo,
    FamilyReminderRow,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from octop.infra.db.pool import DatabasePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

# How far ahead a rolling materialiser looks when topping up reminders
# for a recurring event. Long enough that a laptop asleep for a week
# still wakes up with the next week covered, short enough that a daily
# event does not create a year of rows.
DEFAULT_ROLLING_HORIZON_DAYS = 30


@dataclass(frozen=True)
class ReminderDispatch:
    """What the runner should do with one claimed reminder.

    ``target_title`` is carried as data, not rendered text: the caller
    formats it in the recipient's locale. Storing a translated string
    would freeze one language into the database.
    """

    reminder: FamilyReminderRow
    family_id: str
    recipient_user_id: int | None
    recipient_member_id: str | None
    target_title: str
    target_description: str


def build_dedupe_key(
    *,
    target_type: str,
    target_id: str,
    recipient_member_id: str | None,
    occurrence_key: str | None,
    remind_at: int,
    channel: str = CHANNEL_IN_APP,
) -> str:
    """Stable identity of "this reminder for this occurrence".

    Hashed rather than concatenated so the stored value stays a fixed
    size no matter how long a target id or a title-derived key gets.
    """
    blob = "|".join(
        (
            target_type,
            target_id,
            recipient_member_id or "-",
            occurrence_key or "-",
            channel,
            str(remind_at),
        )
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:40]


class FamilyReminderManager:
    """Create, cancel and roll reminders for one family."""

    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyReminderRepo,
        *,
        db: DatabasePool | None = None,
    ) -> None:
        self.family = family
        self.repo = repo
        # Resolving a reminder's target means reading whichever table the
        # target lives in. Those repos are built here, from the pool the
        # reminder repo already sits on, so the manager never has to
        # reach into another object's private state.
        self._db = db if db is not None else repo.db

    # ----------------------------------------------------------------- CRUD

    def create(
        self,
        family_id: str,
        user: User,
        *,
        target_type: str,
        target_id: str,
        remind_at: int,
        recipient_member_id: str | None = None,
        occurrence_key: str | None = None,
        lead_seconds: int = 0,
    ) -> FamilyReminderRow:
        """Schedule one reminder for a target.

        Returns the existing row when an identical reminder already
        exists -- the caller sees the same reminder, not an error, which
        makes "add a reminder to this event" idempotent in the UI.
        """
        self.family.require_access(family_id, user)
        if target_type not in TARGET_TYPES:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "invalid reminder target type")
        member = self._resolve_recipient(family_id, recipient_member_id)
        fire_at = remind_at - lead_seconds
        dedupe = build_dedupe_key(
            target_type=target_type,
            target_id=target_id,
            recipient_member_id=member.id if member else None,
            occurrence_key=occurrence_key,
            remind_at=fire_at,
        )
        created = self.repo.create(
            family_id,
            target_type=target_type,
            target_id=target_id,
            remind_at=fire_at,
            dedupe_key=dedupe,
            recipient_member_id=member.id if member else None,
            recipient_user_id=member.user_id if member else None,
            occurrence_key=occurrence_key,
        )
        if created is not None:
            return created
        existing = self.repo.list_for_target(target_type, target_id)
        for row in existing:
            if row.dedupe_key == dedupe:
                return row
        raise HomeMindError(HomeMindErrorCode.FAMILY_CONFLICT, "reminder already exists")

    def list_for_family(
        self,
        family_id: str,
        user: User,
        *,
        status: str | None = None,
        target_type: str | None = None,
    ) -> list[FamilyReminderRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_for_family(
            family_id, status=status, target_type=target_type
        )

    def list_for_target(self, target_type: str, target_id: str) -> list[FamilyReminderRow]:
        return self.repo.list_for_target(target_type, target_id)

    def cancel(self, family_id: str, reminder_id: str, user: User) -> FamilyReminderRow | None:
        self.family.require_access(family_id, user)
        reminder = self.repo.get(reminder_id)
        if reminder is None or reminder.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family reminder not found")
        if reminder.status in {STATUS_SENT, STATUS_CANCELLED}:
            return reminder
        return self.repo.cancel_one(reminder.id)

    def cancel_for_target(self, target_type: str, target_id: str) -> int:
        """Invalidate every outstanding reminder of a target.

        Called when a calendar event moves or is cancelled and when a
        task's due date changes. Rows already ``SENT`` survive: the user
        was told, and pretending otherwise would erase a real moment.
        """
        return self.repo.cancel_for_target(target_type, target_id)

    def forget_target(self, target_type: str, target_id: str) -> int:
        """Hard-delete reminders of a target that no longer exists."""
        return self.repo.delete_for_target(target_type, target_id)

    def summary(self, family_id: str, user: User) -> dict[str, int]:
        self.family.require_access(family_id, user)
        return self.repo.count_by_status(family_id)

    # ------------------------------------------------------------ derivation

    def sync_calendar_event_reminders(
        self,
        event: object,
        *,
        recipient_member_ids: list[str],
        lead_seconds: int = 3600,
        horizon_days: int = DEFAULT_ROLLING_HORIZON_DAYS,
        now: int | None = None,
    ) -> list[FamilyReminderRow]:
        """Re-derive the reminders of one calendar event.

        Any occurrence whose time moved, or that no longer exists, has
        its reminder cancelled; new occurrences inside the rolling
        horizon get fresh ones. Nothing is pre-generated beyond the
        horizon, so a weekly event does not seed five years of rows.
        """
        from datetime import UTC, datetime, timedelta

        from homemind.infra.family.calendar import expand_occurrences

        timestamp = now if now is not None else int(datetime.now(UTC).timestamp())
        horizon_end = timestamp + horizon_days * 86400
        event_id = str(getattr(event, "id"))
        family_id = str(getattr(event, "family_id"))

        # Cancel first: an occurrence that moved keeps the same
        # ``target_id`` but a different ``remind_at``, so its old row has
        # to go before the new dedupe key can be minted.
        self.repo.cancel_for_target(TARGET_TYPE_CALENDAR_EVENT, event_id)

        if str(getattr(event, "status", "CONFIRMED")) != "CONFIRMED":
            return []

        occurrences = expand_occurrences(
            event,  # type: ignore[arg-type]
            window_start=timestamp,
            window_end=horizon_end,
            remaining=200,
        )
        created: list[FamilyReminderRow] = []
        for occurrence in occurrences:
            for member_id in recipient_member_ids:
                reminder = self._schedule_for_occurrence(
                    family_id=family_id,
                    event_id=event_id,
                    occurrence_key=occurrence.occurrence_key,
                    starts_at=occurrence.starts_at,
                    member_id=member_id,
                    lead_seconds=lead_seconds,
                )
                if reminder is not None:
                    created.append(reminder)
        return created

    def _schedule_for_occurrence(
        self,
        *,
        family_id: str,
        event_id: str,
        occurrence_key: str,
        starts_at: int,
        member_id: str,
        lead_seconds: int,
    ) -> FamilyReminderRow | None:
        fire_at = starts_at - lead_seconds
        dedupe = build_dedupe_key(
            target_type=TARGET_TYPE_CALENDAR_EVENT,
            target_id=event_id,
            recipient_member_id=member_id,
            occurrence_key=occurrence_key,
            remind_at=fire_at,
        )
        return self.repo.create(
            family_id,
            target_type=TARGET_TYPE_CALENDAR_EVENT,
            target_id=event_id,
            remind_at=fire_at,
            dedupe_key=dedupe,
            recipient_member_id=member_id,
            occurrence_key=occurrence_key,
        )

    def sync_task_reminder(
        self,
        *,
        family_id: str,
        task_id: str,
        due_at: int | None,
        recipient_member_id: str | None,
        lead_seconds: int = 3600,
    ) -> FamilyReminderRow | None:
        """Mirror a task's due date into a reminder.

        A task whose due date is cleared or moved loses its old reminder
        first; only then is a new one created.
        """
        self.repo.cancel_for_target(TARGET_TYPE_TASK, task_id)
        if due_at is None:
            return None
        fire_at = due_at - lead_seconds
        dedupe = build_dedupe_key(
            target_type=TARGET_TYPE_TASK,
            target_id=task_id,
            recipient_member_id=recipient_member_id,
            occurrence_key=None,
            remind_at=fire_at,
        )
        return self.repo.create(
            family_id,
            target_type=TARGET_TYPE_TASK,
            target_id=task_id,
            remind_at=fire_at,
            dedupe_key=dedupe,
            recipient_member_id=recipient_member_id,
        )

    # ------------------------------------------------------------- delivery

    def claim_due(
        self, *, owner: str, ttl_seconds: int, now: int | None = None, limit: int = 25
    ) -> list[ReminderDispatch]:
        """Claim due reminders and resolve what they are about.

        The join is done here rather than in SQL because the target title
        lives in four different tables; doing it per claimed row also
        means a deleted target costs one lookup instead of a join that
        would have to span every target table.
        """
        claimed = self.repo.claim_due(owner=owner, ttl_seconds=ttl_seconds, now=now, limit=limit)
        dispatches: list[ReminderDispatch] = []
        for reminder in claimed:
            title, description = self._resolve_target(reminder)
            if title is None:
                # The target is gone. Cancelling is the honest outcome:
                # delivering "your reminder" with no subject is worse.
                self.repo.mark_failed(
                    reminder.id, error="target no longer exists", permanent=True
                )
                continue
            dispatches.append(
                ReminderDispatch(
                    reminder=reminder,
                    family_id=reminder.family_id,
                    recipient_user_id=reminder.recipient_user_id,
                    recipient_member_id=reminder.recipient_member_id,
                    target_title=title,
                    target_description=description,
                )
            )
        return dispatches

    def mark_sent(self, reminder_id: str) -> None:
        self.repo.mark_sent(reminder_id)

    def mark_failed(self, reminder_id: str, *, error: str, permanent: bool = False) -> None:
        self.repo.mark_failed(reminder_id, error=error, permanent=permanent)

    def recover_stale_leases(self, *, now: int | None = None) -> int:
        return self.repo.recover_stale_leases(now=now)

    def backoff_seconds(self, attempt_count: int, *, base: int = 30, cap: int = 3600) -> int:
        """Exponential backoff for a failed delivery.

        Doubling from ``base`` and capped, so a permanently broken target
        settles at one attempt every hour instead of hammering the bus
        forever.
        """
        if attempt_count <= 0:
            return base
        return min(cap, base * (2 ** min(attempt_count - 1, 10)))

    # --------------------------------------------------------------- helpers

    def _resolve_recipient(
        self, family_id: str, recipient_member_id: str | None
    ) -> object | None:
        if recipient_member_id is None:
            return None
        member = self.family.repo.get_member(recipient_member_id)
        if member is None or member.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        return member

    def _resolve_target(self, reminder: FamilyReminderRow) -> tuple[str | None, str]:
        """Fetch the target's title, or ``None`` when it no longer exists."""
        if reminder.target_type == TARGET_TYPE_CALENDAR_EVENT:
            from homemind.infra.db.repos.family_calendars import FamilyCalendarRepo

            event = FamilyCalendarRepo(self._db).get_event(reminder.target_id)
            if event is None:
                return None, ""
            return event.title, event.description
        if reminder.target_type == TARGET_TYPE_TASK:
            from homemind.infra.db.repos.family_tasks import FamilyTaskRepo

            task = FamilyTaskRepo(self._db).get(reminder.target_id)
            if task is None:
                return None, ""
            return task.title, task.description
        if reminder.target_type == TARGET_TYPE_DEVICE:
            from homemind.infra.db.repos.family_devices import FamilyDeviceRepo

            device = FamilyDeviceRepo(self._db).get(reminder.target_id)
            if device is None:
                return None, ""
            return device.name, ""
        if reminder.target_type == TARGET_TYPE_APPROVAL:
            from homemind.infra.db.repos.family_transactions import FamilyTransactionRepo

            transactions = FamilyTransactionRepo(self._db)
            approval = transactions.get_approval(reminder.target_id)
            if approval is None:
                return None, ""
            # The approval row has no subject of its own; the action name
            # lives on the transaction it guards.
            transaction = transactions.get_transaction(approval.transaction_id)
            if transaction is None:
                return None, ""
            return transaction.action, ""
        logger.warning("FamilyReminder: unknown target type %r", reminder.target_type)
        return None, ""


__all__ = [
    "DEFAULT_ROLLING_HORIZON_DAYS",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_SENT",
    "TARGET_TYPE_APPROVAL",
    "TARGET_TYPE_CALENDAR_EVENT",
    "TARGET_TYPE_DEVICE",
    "TARGET_TYPE_TASK",
    "FamilyReminderManager",
    "ReminderDispatch",
    "build_dedupe_key",
]