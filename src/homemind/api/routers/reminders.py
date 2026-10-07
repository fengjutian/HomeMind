"""HTTP surface for family reminders.

A reminder is a scheduled intent; this router only creates, lists and
cancels them. Delivery is the runner's job and never happens inside an
HTTP request, so creating a reminder here cannot block on a push.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.reminders import FamilyReminderManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class ReminderCreateBody(BaseModel):
    target_type: str = Field(description="TASK | CALENDAR_EVENT | APPROVAL | DEVICE")
    target_id: str
    remind_at: int = Field(description="UTC epoch seconds the reminder should fire.")
    recipient_member_id: str | None = Field(
        default=None,
        description="Omit to address every member of the family.",
    )
    occurrence_key: str | None = Field(
        default=None,
        description="Distinguishes one occurrence of a repeating target.",
    )
    lead_seconds: int = Field(
        default=0,
        ge=0,
        description="Fire this many seconds before `remind_at` instead of at it.",
    )


class ReminderResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    family_id: str
    target_type: str
    target_id: str
    recipient_member_id: str | None
    recipient_user_id: int | None
    remind_at: int
    channel: str
    status: str
    attempt_count: int
    last_error: str | None
    occurrence_key: str | None
    created_at: int
    updated_at: int


class ReminderSummaryResponse(BaseModel):
    counts: dict[str, int]


def _manager(server: OctopServer) -> FamilyReminderManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return FamilyReminderManager(
        FamilyManager(services.family_repo), services.family_reminder_repo
    )


@router.post(
    "/{family_id}/reminders",
    response_model=ReminderResponse,
    status_code=201,
    summary="Schedule a family reminder",
    description=(
        "Idempotent: scheduling the same reminder for the same target, "
        "occurrence and recipient returns the existing row rather than a "
        "second notification."
    ),
)
async def create_reminder(
    family_id: str, body: ReminderCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/reminders",
    response_model=list[ReminderResponse],
    summary="List family reminders",
)
async def list_reminders(
    family_id: str,
    server: Server,
    user: CurrentUser,
    status: Annotated[
        str | None, Query(description="PENDING | CLAIMED | SENT | FAILED | CANCELLED")
    ] = None,
    target_type: Annotated[str | None, Query()] = None,
) -> object:
    return _manager(server).list_for_family(
        family_id, user, status=status, target_type=target_type
    )


@router.get(
    "/{family_id}/reminders/summary",
    response_model=ReminderSummaryResponse,
    summary="Count family reminders by status",
)
async def reminder_summary(
    family_id: str, server: Server, user: CurrentUser
) -> object:
    return ReminderSummaryResponse(counts=_manager(server).summary(family_id, user))


@router.post(
    "/{family_id}/reminders/{reminder_id}/cancel",
    response_model=ReminderResponse,
    summary="Cancel one reminder",
    description=(
        "Cancels only the named reminder. A repeating event's other "
        "occurrences keep their own reminders. An already-sent reminder "
        "is returned unchanged."
    ),
)
async def cancel_reminder(
    family_id: str, reminder_id: str, server: Server, user: CurrentUser
) -> object:
    reminder = _manager(server).cancel(family_id, reminder_id, user)
    if reminder is None:  # pragma: no cover — cancel_one always returns the row
        from octop.infra.errors import ErrorCode, OctopError

        raise OctopError(ErrorCode.NOT_FOUND, "family reminder not found")
    return reminder


__all__ = ["router", "ReminderCreateBody", "ReminderResponse", "ReminderSummaryResponse"]