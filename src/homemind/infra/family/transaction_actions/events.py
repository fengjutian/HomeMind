"""``event.create`` action handler."""

from __future__ import annotations

from typing import Any

from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.transaction_actions.base import (
    ActionContext,
)


class EventCreateHandler:
    action = "event.create"

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        if not isinstance(payload.get("title"), str):
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "event title is required")
        if not isinstance(payload.get("start_at"), int) or not isinstance(
            payload.get("end_at"), int
        ):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "event start_at / end_at must be integers",
            )
        if payload["end_at"] < payload["start_at"]:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "event end_at must not precede start_at",
            )

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": payload["title"],
            "event_type": payload.get("event_type"),
            "start_at": payload["start_at"],
            "end_at": payload["end_at"],
            "location": payload.get("location"),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.context.create_event(
            context.family_id,
            context.requester,
            event_type=payload.get("event_type", "GENERIC"),
            title=payload["title"],
            start_at=payload["start_at"],
            end_at=payload["end_at"],
            location=payload.get("location"),
            description=payload.get("description", ""),
            metadata=payload.get("metadata", {}),
        )
        return {"event_id": event.id}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event = self.context.repo.get_event(result["event_id"])
        return {"found": bool(event)}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"compensated": False}


__all__ = ["EventCreateHandler"]
