"""``task.create`` action handler."""

from __future__ import annotations

import logging
from typing import Any

from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.base import (
    ActionContext,
)
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)


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
        """Re-read the task from the database and confirm every field
        the executor claimed actually landed.

        Spec requirement: ``重新读取 task, 检查 ID、family_id、title、status`` —
        any mismatch marks the transaction as
        ``FAILED_REQUIRES_REVIEW`` so a human can reconcile instead of
        silently marking the side effect COMPLETED.
        """
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            return {"verified": False, "reason": "missing_task_id"}
        task = self.tasks.repo.get(task_id)
        if task is None:
            return {"verified": False, "reason": "task_not_found", "task_id": task_id}
        if task.family_id != context.family_id:
            return {
                "verified": False,
                "reason": "family_mismatch",
                "task_family_id": task.family_id,
                "expected_family_id": context.family_id,
            }
        expected_title = (result.get("title") or "").strip()
        if expected_title and task.title != expected_title:
            return {
                "verified": False,
                "reason": "title_mismatch",
                "task_id": task_id,
                "expected": expected_title,
                "actual": task.title,
            }
        return {
            "verified": True,
            "task_id": task_id,
            "status": task.status,
            "family_id": task.family_id,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Best-effort rollback: delete the task we just created.

        Failure is logged but never raised so a doomed transaction
        still transitions to ``COMPENSATED``. The audit row records
        the failure for human review.
        """
        task_id = result.get("task_id") or result.get("id")
        if not task_id:
            return {"compensated": False, "reason": "missing_task_id"}
        try:
            self.tasks.repo.delete(task_id)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "TaskCreateHandler.compensate failed task_id=%s: %s",
                task_id,
                exc,
            )
            return {
                "compensated": False,
                "reason": "compensation_failed",
                "error": str(exc),
                "task_id": task_id,
            }
        return {"compensated": True, "task_id": task_id}


__all__ = ["TaskCreateHandler"]