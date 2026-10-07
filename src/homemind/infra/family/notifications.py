"""The family notification centre (Stage 2).

Every "the family should know something" path in HomeMind ends here.
That centralisation is the point: a router that INSERTs its own row is a
router whose dedupe, quiet-hours and expiry rules quietly diverge from
everyone else's.

**The database is the truth; the event bus is the accelerant.** A member
whose dashboard was closed still finds the notification in their inbox
after a restart, and the unread badge can always be recomputed from
storage rather than trusted to a socket that may have dropped.

Text is stored as ``title_key`` / ``body_key`` plus parameters, never as
a rendered sentence. The same row has to read correctly for a Chinese
and an English member; freezing one language into the table makes the
other permanently wrong.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from homemind.infra.cursor import Page, build_page
from homemind.infra.db.repos.family_notifications import (
    NOTIFICATION_SEVERITIES,
    NOTIFICATION_TYPES,
    SEVERITY_CRITICAL,
    SEVERITY_INFO,
    SEVERITY_WARNING,
    TYPE_APPROVAL_DECIDED,
    TYPE_APPROVAL_WAITING,
    TYPE_ASSET_JOB_DONE,
    TYPE_ASSET_JOB_FAILED,
    TYPE_CALENDAR_REMINDER,
    TYPE_DEVICE_OFFLINE,
    TYPE_KNOWLEDGE_INDEX_FAILED,
    TYPE_TASK_DONE,
    TYPE_TASK_DUE,
    FamilyNotificationRepo,
    FamilyNotificationRow,
    NotificationPrefsRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

# How long a notification stays in the inbox by default. Long enough to
# catch a week away, short enough that the table does not become an
# archive nobody prunes.
DEFAULT_TTL_DAYS = 30

# Notification copy keys. The dashboard resolves these through i18n;
# the backend owns the keys so both locales stay in step.
TITLE_KEYS: dict[str, str] = {
    TYPE_TASK_DUE: "notifications.taskDue.title",
    TYPE_APPROVAL_WAITING: "notifications.approvalWaiting.title",
    TYPE_APPROVAL_DECIDED: "notifications.approvalDecided.title",
    TYPE_DEVICE_OFFLINE: "notifications.deviceOffline.title",
    TYPE_ASSET_JOB_DONE: "notifications.assetJobDone.title",
    TYPE_ASSET_JOB_FAILED: "notifications.assetJobFailed.title",
    TYPE_KNOWLEDGE_INDEX_FAILED: "notifications.knowledgeIndexFailed.title",
    TYPE_CALENDAR_REMINDER: "notifications.calendarReminder.title",
    TYPE_TASK_DONE: "notifications.taskDone.title",
}

BODY_KEYS: dict[str, str] = {
    TYPE_TASK_DUE: "notifications.taskDue.body",
    TYPE_APPROVAL_WAITING: "notifications.approvalWaiting.body",
    TYPE_APPROVAL_DECIDED: "notifications.approvalDecided.body",
    TYPE_DEVICE_OFFLINE: "notifications.deviceOffline.body",
    TYPE_ASSET_JOB_DONE: "notifications.assetJobDone.body",
    TYPE_ASSET_JOB_FAILED: "notifications.assetJobFailed.body",
    TYPE_KNOWLEDGE_INDEX_FAILED: "notifications.knowledgeIndexFailed.body",
    TYPE_CALENDAR_REMINDER: "notifications.calendarReminder.body",
    TYPE_TASK_DONE: "notifications.taskDone.body",
}


@dataclass(frozen=True)
class NotificationRequest:
    """One notification, addressed to whoever should receive it.

    ``dedupe_suffix`` distinguishes two logically different notices that
    share a target (two approvals on the same transaction, say). Leave
    it ``None`` when one notice per target is the right rule.
    """

    type: str
    params: dict[str, object]
    target_type: str | None = None
    target_id: str | None = None
    severity: str = SEVERITY_INFO
    dedupe_suffix: str | None = None
    ttl_days: int = DEFAULT_TTL_DAYS


def build_dedupe_key(
    *,
    family_id: str,
    user_id: int,
    type: str,
    target_type: str | None,
    target_id: str | None,
    dedupe_suffix: str | None = None,
) -> str:
    """Stable identity of "this notice for this user about this thing"."""
    blob = "|".join(
        (
            family_id,
            str(user_id),
            type,
            target_type or "-",
            target_id or "-",
            dedupe_suffix or "-",
        )
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:48]


class NotificationManager:
    """Create, list and deliver family notifications."""

    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyNotificationRepo,
        *,
        server_timezone: str = "UTC",
    ) -> None:
        self.family = family
        self.repo = repo
        self._server_timezone = server_timezone

    # ----------------------------------------------------------------- emit

    def emit(
        self,
        family_id: str,
        request: NotificationRequest,
        *,
        recipients: Iterable[int] | None = None,
        now: int | None = None,
    ) -> list[FamilyNotificationRow]:
        """Persist one notification per eligible recipient.

        Idempotent per ``(user, type, target)``: calling this twice for
        the same event returns the same rows rather than stacking two
        unread bells.

        ``recipients`` defaults to every member of the family who has a
        linked user account. A member with no account has nobody to
        notify, and a platform admin with no membership has no business
        in this family's inbox.
        """
        if request.type not in NOTIFICATION_TYPES:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "unknown notification type")
        if request.severity not in NOTIFICATION_SEVERITIES:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "unknown notification severity")

        timestamp = now if now is not None else int(datetime.now(UTC).timestamp())
        user_ids = list(recipients) if recipients is not None else self._family_user_ids(family_id)
        created: list[FamilyNotificationRow] = []
        for user_id in user_ids:
            if not self._deliverable(family_id, user_id, request, timestamp):
                continue
            row = self.repo.create(
                family_id,
                user_id=user_id,
                type=request.type,
                title_key=TITLE_KEYS[request.type],
                body_key=BODY_KEYS[request.type],
                params=request.params,
                target_type=request.target_type,
                target_id=request.target_id,
                severity=request.severity,
                dedupe_key=build_dedupe_key(
                    family_id=family_id,
                    user_id=user_id,
                    type=request.type,
                    target_type=request.target_type,
                    target_id=request.target_id,
                    dedupe_suffix=request.dedupe_suffix,
                ),
                expires_at=timestamp + request.ttl_days * 86400,
                now=timestamp,
            )
            if row is not None:
                created.append(row)
        return created

    def emit_for_member(
        self,
        family_id: str,
        member_id: str,
        request: NotificationRequest,
        *,
        now: int | None = None,
    ) -> list[FamilyNotificationRow]:
        """Address one member. A member without a user account gets none."""
        member = self.family.repo.get_member(member_id)
        if member is None or member.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        if member.user_id is None:
            return []
        return self.emit(family_id, request, recipients=[member.user_id], now=now)

    # ----------------------------------------------------------------- read

    def list_inbox(
        self,
        family_id: str,
        user: User,
        *,
        type: str | None = None,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FamilyNotificationRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_inbox(
            user.id,
            family_id=family_id,
            type=type,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )

    def list_inbox_page(
        self,
        family_id: str,
        user: User,
        *,
        limit: int = 20,
        cursor: str | None = None,
        unread_only: bool = False,
        type: str | None = None,
    ) -> Page:
        """One cursor-paginated page of the caller's inbox.

        The mobile contract: a phone pulls twenty at a time and follows
        ``next_cursor`` until it is ``None``. ``limit + 1`` rows are
        fetched so ``has_more`` costs no extra query.
        """
        self.family.require_access(family_id, user)
        rows = self.repo.list_inbox(
            user.id,
            family_id=family_id,
            type=type,
            unread_only=unread_only,
            limit=limit + 1,
            cursor=cursor,
        )
        return build_page(rows, to_item=lambda row: row, limit=limit)

    def unread_count(self, family_id: str, user: User) -> int:
        """Unread badge.

        Access is checked first even though the query is scoped by
        ``user_id``: an outsider must get a 403, not a confident 0 that
        reads like "you have no notifications".
        """
        self.family.require_access(family_id, user)
        return self.repo.count_unread(user.id, family_id=family_id)

    def mark_read(self, family_id: str, notification_id: str, user: User) -> FamilyNotificationRow:
        """Mark one notification read and return it.

        Idempotent: a row that was already read keeps its original
        ``read_at`` rather than being re-stamped, so "when did you see
        this" stays truthful.
        """
        self.family.require_access(family_id, user)
        row = self.repo.get(notification_id)
        # Family *and* owner must match: without the user check, a
        # family member could mark somebody else's notification read by
        # guessing its id, and the victim's badge would silently drop.
        if row is None or row.family_id != family_id or row.user_id != user.id:
            raise OctopError(ErrorCode.NOT_FOUND, "notification not found")
        self.repo.mark_read(notification_id, user_id=user.id)
        refreshed = self.repo.get(notification_id)
        return refreshed if refreshed is not None else row

    def mark_all_read(self, family_id: str, user: User) -> int:
        self.family.require_access(family_id, user)
        return self.repo.mark_all_read(user.id, family_id=family_id)

    def purge_expired(self, family_id: str, user: User) -> int:
        """Drop the caller's lapsed notifications from their inbox."""
        self.family.require_access(family_id, user)
        timestamp = int(datetime.now(UTC).timestamp())
        return self._purge_expired_for_user(user.id, timestamp)

    def purge_expired_globally(self, *, now: int | None = None) -> int:
        """Sweep lapsed rows for every user.

        Manager-level (platform) operation: the table is shared, so one
        household must not be able to delete another household's
        housekeeping.
        """
        timestamp = now if now is not None else int(datetime.now(UTC).timestamp())
        return self.repo.delete_expired(now=timestamp)

    # ---------------------------------------------------------- preferences

    def get_prefs(self, family_id: str, user: User) -> NotificationPrefsRow:
        """Preferences, defaulted when the user has never set any.

        Returning defaults rather than 404 means a client never has to
        special-case "first visit".
        """
        self.family.require_access(family_id, user)
        row = self.repo.get_prefs(family_id, user.id)
        if row is not None:
            return row
        return NotificationPrefsRow(
            family_id=family_id,
            user_id=user.id,
            disabled_types=[],
            quiet_hours_start=None,
            quiet_hours_end=None,
            timezone=self._server_timezone,
            created_at=0,
            updated_at=0,
        )

    def save_prefs(
        self,
        family_id: str,
        user: User,
        *,
        disabled_types: Sequence[str] | None = None,
        quiet_hours_start: int | None = None,
        quiet_hours_end: int | None = None,
        timezone: str | None = None,
    ) -> NotificationPrefsRow:
        self.family.require_access(family_id, user)
        if disabled_types is not None:
            unknown = sorted(set(disabled_types) - NOTIFICATION_TYPES)
            if unknown:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID, f"unknown notification types: {unknown}"
                )
        if (quiet_hours_start is None) != (quiet_hours_end is None):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "quiet hours need both a start and an end"
            )
        for field, value in (
            ("quiet_hours_start", quiet_hours_start),
            ("quiet_hours_end", quiet_hours_end),
        ):
            if value is not None and not 0 <= value <= 1439:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    f"{field} must be minutes from local midnight (0-1439)",
                )
        return self.repo.save_prefs(
            family_id,
            user_id=user.id,
            disabled_types=list(disabled_types or []),
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            timezone=timezone or self._server_timezone,
        )

    # ------------------------------------------------------------- triggers

    def notify_task_due(
        self, family_id: str, *, task_id: str, title: str, due_at: int, **kwargs: object
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_TASK_DUE,
                params={"task_id": task_id, "title": title, "due_at": due_at},
                target_type="TASK",
                target_id=task_id,
                severity=SEVERITY_INFO,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_task_done(
        self, family_id: str, *, task_id: str, title: str, **kwargs: object
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_TASK_DONE,
                params={"task_id": task_id, "title": title},
                target_type="TASK",
                target_id=task_id,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_approval_waiting(
        self,
        family_id: str,
        *,
        approval_id: str,
        transaction_id: str,
        action: str,
        **kwargs: object,
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_APPROVAL_WAITING,
                params={
                    "approval_id": approval_id,
                    "transaction_id": transaction_id,
                    "action": action,
                },
                target_type="APPROVAL",
                target_id=approval_id,
                severity=SEVERITY_WARNING,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_approval_decided(
        self,
        family_id: str,
        *,
        approval_id: str,
        transaction_id: str,
        action: str,
        approved: bool,
        **kwargs: object,
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_APPROVAL_DECIDED,
                params={
                    "approval_id": approval_id,
                    "transaction_id": transaction_id,
                    "action": action,
                    "approved": approved,
                },
                target_type="APPROVAL",
                target_id=approval_id,
                severity=SEVERITY_INFO if approved else SEVERITY_WARNING,
                # A decision can flip while the same approval is being
                # re-decided after an expiry, so the outcome belongs in
                # the identity.
                dedupe_suffix="approved" if approved else "rejected",
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_device_offline(
        self, family_id: str, *, device_id: str, name: str, **kwargs: object
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_DEVICE_OFFLINE,
                params={"device_id": device_id, "name": name},
                target_type="DEVICE",
                target_id=device_id,
                severity=SEVERITY_WARNING,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_asset_job_finished(
        self,
        family_id: str,
        *,
        job_id: str,
        job_type: str,
        succeeded: bool,
        failed_items: int = 0,
        **kwargs: object,
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_ASSET_JOB_DONE if succeeded else TYPE_ASSET_JOB_FAILED,
                params={
                    "job_id": job_id,
                    "job_type": job_type,
                    "failed_items": failed_items,
                },
                target_type="ASSET_JOB",
                target_id=job_id,
                severity=SEVERITY_INFO if succeeded else SEVERITY_WARNING,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_knowledge_index_failed(
        self,
        family_id: str,
        *,
        document_id: str,
        asset_id: str,
        error: str,
        **kwargs: object,
    ) -> list[FamilyNotificationRow]:
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_KNOWLEDGE_INDEX_FAILED,
                # The parser's message can contain a path; keep only the
                # first line so a notification never leaks a filesystem
                # layout into an inbox.
                params={
                    "document_id": document_id,
                    "asset_id": asset_id,
                    "reason": error.splitlines()[0][:200] if error else "",
                },
                target_type="KNOWLEDGE_DOCUMENT",
                target_id=document_id,
                severity=SEVERITY_WARNING,
            ),
            **kwargs,  # type: ignore[arg-type]
        )

    def notify_calendar_reminder(
        self,
        family_id: str,
        *,
        reminder_id: str,
        target_id: str,
        title: str,
        starts_at: int,
        recipients: Iterable[int],
        **kwargs: object,
    ) -> list[FamilyNotificationRow]:
        """Turn a delivered reminder into an inbox entry.

        ``dedupe_suffix`` is the occurrence key, so a weekly event
        produces one notification per week rather than one ever.
        """
        return self.emit(
            family_id,
            NotificationRequest(
                type=TYPE_CALENDAR_REMINDER,
                params={
                    "reminder_id": reminder_id,
                    "target_id": target_id,
                    "title": title,
                    "starts_at": starts_at,
                },
                target_type="CALENDAR_EVENT",
                target_id=target_id,
                dedupe_suffix=str(starts_at),
            ),
            recipients=recipients,
            **kwargs,  # type: ignore[arg-type]
        )

    # --------------------------------------------------------------- helpers

    def is_suppressed(self, family_id: str, user_id: int, type: str, *, now: int) -> bool:
        """True when this user has muted ``type`` or is in quiet hours."""
        prefs = self.repo.get_prefs(family_id, user_id)
        if prefs is None:
            return False
        if type in prefs.disabled_types:
            return True
        return self._in_quiet_hours(prefs, now)

    def _deliverable(
        self, family_id: str, user_id: int, request: NotificationRequest, now: int
    ) -> bool:
        if not self.is_suppressed(family_id, user_id, request.type, now=now):
            return True
        # A critical notice breaks through quiet hours. Muting is a
        # preference, not a safety mechanism.
        return request.severity == SEVERITY_CRITICAL

    @staticmethod
    def _in_quiet_hours(prefs: NotificationPrefsRow, now: int) -> bool:
        """True when ``now`` falls inside the user's quiet-hours window.

        The window is stored in **minutes from local midnight** (0-1439),
        which is what the API contract documents; the instant is
        converted back into the same minute-of-day before comparing. Both
        sides must use the same unit — mixing minutes with seconds
        silently produces a window nobody is ever inside.
        """
        start, end = prefs.quiet_hours_start, prefs.quiet_hours_end
        if start is None or end is None:
            return False
        if start == end:
            return False
        moment = datetime.fromtimestamp(now, tz=UTC)
        current = moment.hour * 60 + moment.minute
        if start < end:
            return start <= current < end
        # A window that wraps past midnight, e.g. 22:00 → 07:00.
        return current >= start or current < end

    def _family_user_ids(self, family_id: str) -> list[int]:
        return [
            int(member.user_id)
            for member in self.family.repo.list_members(family_id)
            if member.user_id is not None and member.status == "ACTIVE"
        ]

    def _purge_expired_for_user(self, user_id: int, now: int) -> int:
        """Delete one user's lapsed rows.

        Scoped by ``user_id`` rather than reusing the global sweep, so
        a family can tidy its own inbox without a platform-wide delete.
        """
        with self.repo.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_notifications "
                "WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at <= ?",
                (user_id, now),
            )
        return int(cursor.rowcount or 0)


__all__ = [
    "BODY_KEYS",
    "DEFAULT_TTL_DAYS",
    "TITLE_KEYS",
    "NotificationManager",
    "NotificationRequest",
    "build_dedupe_key",
]
