"""``memory.create`` action handler.

The lifecycle manager owns approval for memory candidates; this handler
delegates the create path so the transaction machinery doesn't need to
special-case ``FamilyContextManager.create_memory`` directly.
"""

from __future__ import annotations

from typing import Any

from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.transaction_actions.base import (
    ActionContext,
    FamilyActionHandler,
)
from octop.infra.errors import ErrorCode, OctopError


class MemoryCreateHandler:
    action = "memory.create"

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("content"), str) or not payload["content"].strip():
            raise OctopError(ErrorCode.BAD_REQUEST, "memory content is required")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "subject_type": payload.get("subject_type"),
            "subject_id": payload.get("subject_id"),
            "memory_type": payload.get("memory_type"),
            "importance": payload.get("importance"),
            "confidence": payload.get("confidence"),
            "visibility": payload.get("visibility", "FAMILY"),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        memory_type = payload.get("memory_type", MemoryType.FACT.value)
        memory = self.context.create_memory(
            context.family_id,
            context.requester,
            subject_type=payload.get("subject_type", "FAMILY"),
            subject_id=payload.get("subject_id"),
            content=payload["content"],
            memory_type=MemoryType(memory_type),
            importance=float(payload.get("importance", 0.5)),
            confidence=float(payload.get("confidence", 0.5)),
            visibility=payload.get("visibility", "FAMILY"),
            source_type=payload.get("source_type", "AGENT"),
            source_id=payload.get("source_id"),
            expires_at=payload.get("expires_at"),
        )
        return {"memory_id": memory.id}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        memory = self.context.repo.get_memory(result["memory_id"])
        return {"found": bool(memory)}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"compensated": False}


__all__ = ["MemoryCreateHandler"]