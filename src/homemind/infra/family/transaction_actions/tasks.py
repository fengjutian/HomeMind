"""``task.create`` action handler."""

from __future__ import annotations

from typing import Any

from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.base import (
    ActionContext,
)
from octop.infra.errors import ErrorCode, OctopError


class TaskCreateHandler:
    action = "task.create"

    def __init__(
        self,
        family: FamilyManager,
        tasks: FamilyTaskManager,
    ) -> None:
        self.family = family
        self.tasks = tasks

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise OctopError(ErrorCode.BAD_REQUEST, "task title is required")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": payload.get("title"),
            "description": payload.get("description", ""),
            "assigned_member_id": payload.get("assigned_member_id"),
            "due_at": payload.get("due_at"),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        task = self.tasks.create(
            context.family_id,
            context.requester,
            title=payload["title"],
            description=payload.get("description", ""),
            assigned_member_id=payload.get("assigned_member_id"),
            due_at=payload.get("due_at"),
        )
        return {
            "id": task.id,
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status,
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            return {"verified": False, "reason": "missing task_id"}
        task = self.tasks.repo.get(task_id)
        return {"verified": bool(task), "found": bool(task)}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"compensated": False}


__all__ = ["TaskCreateHandler"]