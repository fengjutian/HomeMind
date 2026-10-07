"""Family task domain service."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from homemind.infra.db.repos.family_tasks import FamilyTaskRepo, FamilyTaskRow
from homemind.infra.family.manager import FamilyManager
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User

if TYPE_CHECKING:
    from homemind.infra.family.reminders import FamilyReminderManager


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# How long before a task's due date its reminder fires.
TASK_REMINDER_LEAD_SECONDS = 3600


class FamilyTaskManager:
    def __init__(
        self,
        family: FamilyManager,
        repo: FamilyTaskRepo,
        *,
        reminders: FamilyReminderManager | None = None,
    ) -> None:
        self.family = family
        self.repo = repo
        # Optional so every existing construction site keeps working; when
        # present, a due-date change re-derives the task's reminder
        # instead of leaving a stale one pointing at the old time.
        self.reminders = reminders

    def create(
        self,
        family_id: str,
        user: User,
        *,
        title: str,
        description: str = "",
        assigned_member_id: str | None = None,
        due_at: int | None = None,
    ) -> FamilyTaskRow:
        self.family.require_access(family_id, user)
        if assigned_member_id is not None:
            member = self.family.repo.get_member(assigned_member_id)
            if member is None or member.family_id != family_id:
                raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        return self.repo.create(
            family_id,
            title=title.strip(),
            description=description.strip(),
            assigned_member_id=assigned_member_id,
            due_at=due_at,
            created_by=user.id,
        )

    def list(
        self, family_id: str, user: User, *, status: TaskStatus | None = None
    ) -> list[FamilyTaskRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_for_family(family_id, status=status.value if status else None)

    def update(
        self,
        family_id: str,
        task_id: str,
        user: User,
        values: dict[str, object],
    ) -> FamilyTaskRow:
        self.family.require_access(family_id, user)
        task = self._task(family_id, task_id)
        if "assigned_member_id" in values and values["assigned_member_id"] is not None:
            member = self.family.repo.get_member(str(values["assigned_member_id"]))
            if member is None or member.family_id != family_id:
                raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        if "status" in values and values["status"] is not None:
            values["status"] = TaskStatus(str(values["status"])).value
        if "title" in values and values["title"] is not None:
            values["title"] = str(values["title"]).strip()
        if "description" in values and values["description"] is not None:
            values["description"] = str(values["description"]).strip()
        updated = self.repo.update(task.id, **values)
        assert updated is not None
        if self.reminders is not None and "due_at" in values:
            self._sync_reminder(updated)
        return updated

    def delete(self, family_id: str, task_id: str, user: User) -> None:
        self.family.require_access(family_id, user)
        task = self._task(family_id, task_id)
        self.repo.delete(task.id)
        if self.reminders is not None:
            # The task is gone; a reminder pointing at it would resolve to
            # nothing at delivery time.
            self.reminders.forget_target("TASK", task.id)

    def _sync_reminder(self, task: FamilyTaskRow) -> None:
        assert self.reminders is not None
        self.reminders.sync_task_reminder(
            family_id=task.family_id,
            task_id=task.id,
            due_at=task.due_at,
            recipient_member_id=task.assigned_member_id,
            lead_seconds=TASK_REMINDER_LEAD_SECONDS,
        )

    def _task(self, family_id: str, task_id: str) -> FamilyTaskRow:
        task = self.repo.get(task_id)
        if task is None or task.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family task not found")
        return task
