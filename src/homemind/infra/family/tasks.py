"""Family task domain service."""

from __future__ import annotations

from enum import StrEnum

from homemind.infra.db.repos.family_tasks import FamilyTaskRepo, FamilyTaskRow
from homemind.infra.family.manager import FamilyManager
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    WAITING_APPROVAL = "WAITING_APPROVAL"
    DONE = "DONE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class FamilyTaskManager:
    def __init__(self, family: FamilyManager, repo: FamilyTaskRepo) -> None:
        self.family = family
        self.repo = repo

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
        return self.repo.list(family_id, status=status.value if status else None)
