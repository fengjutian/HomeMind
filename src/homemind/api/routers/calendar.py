"""HTTP surface for family calendars, events and occurrences.

Thin on purpose: validate the request, delegate to
:class:`FamilyCalendarManager`, map the result. Every timestamp in the
contract is UTC epoch seconds; the timezone a human reads in travels
alongside as an IANA name so the dashboard renders "09:00" without
guessing whose clock that is.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.calendar import (
    MAX_OCCURRENCE_WINDOW_DAYS,
    FamilyCalendarManager,
)
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.reminders import FamilyReminderManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]

_TIMESTAMP = Field(description="UTC epoch seconds.")


class CalendarCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    color: str | None = Field(default=None, max_length=32)
    timezone: str | None = Field(
        default=None,
        description="IANA name. Omit to inherit the server timezone.",
    )
    visibility: str = Field(default="FAMILY", description="FAMILY | PRIVATE | PUBLIC")
    space_id: str | None = None


class CalendarUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(default=None, max_length=32)
    timezone: str | None = None
    visibility: str | None = None
    space_id: str | None = None


class CalendarResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    family_id: str
    name: str
    description: str
    color: str | None
    timezone: str | None = Field(
        default=None, description="Null means the server timezone applies."
    )
    visibility: str
    space_id: str | None
    created_by: int
    created_at: int
    updated_at: int


class CalendarEventCreateBody(BaseModel):
    calendar_id: str
    title: str = Field(min_length=1, max_length=200)
    starts_at: int = _TIMESTAMP
    ends_at: int = _TIMESTAMP
    description: str = Field(default="", max_length=5000)
    location: str | None = Field(default=None, max_length=300)
    all_day: bool = False
    timezone: str | None = Field(
        default=None, description="IANA name; defaults to the calendar's, then the server's."
    )
    recurrence_rule: str | None = Field(
        default=None,
        description=(
            "RFC 5545 RRULE, e.g. 'FREQ=WEEKLY;BYDAY=MO'. Natural language is "
            "rejected; the stored value must stay machine-readable."
        ),
    )
    recurrence_until: int | None = _TIMESTAMP
    source_type: str = Field(default="MANUAL", description="MANUAL | TASK | EXTERNAL")
    source_id: str | None = None


class CalendarEventUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    starts_at: int | None = None
    ends_at: int | None = None
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=300)
    all_day: bool | None = None
    timezone: str | None = None
    recurrence_rule: str | None = None
    recurrence_until: int | None = None
    status: str | None = Field(default=None, description="CONFIRMED | CANCELLED")
    expected_version: int | None = Field(
        default=None,
        description="Optimistic lock; a mismatch is answered with 409 instead of overwriting.",
    )


class CalendarEventResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    calendar_id: str
    family_id: str
    title: str
    description: str
    location: str | None
    starts_at: int
    ends_at: int
    all_day: bool
    timezone: str
    recurrence_rule: str | None
    recurrence_until: int | None
    source_type: str
    source_id: str | None
    status: str
    version: int
    created_by: int
    created_at: int
    updated_at: int


class OccurrenceResponse(BaseModel):
    model_config = {"from_attributes": True}

    event_id: str
    calendar_id: str
    family_id: str
    title: str
    description: str
    location: str | None
    starts_at: int
    ends_at: int
    all_day: bool
    timezone: str
    status: str
    source_type: str
    source_id: str | None
    occurrence_key: str
    is_recurring: bool


def _manager(server: OctopServer) -> FamilyCalendarManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    families = FamilyManager(services.family_repo)
    return FamilyCalendarManager(
        families,
        services.family_calendar_repo,
        server_timezone=server_timezone(server),
        # Wired so editing an event re-derives its reminders; without it
        # a moved meeting would still remind the family about the old time.
        reminders=FamilyReminderManager(families, services.family_reminder_repo),
    )


def server_timezone(server: OctopServer) -> str:
    """The server's configured display/scheduling timezone.

    Read per request rather than cached: ``config.json`` can be edited
    while the process runs, and a stale value would show the family the
    wrong local time on every calendar render.
    """
    from octop.config import load_config  # noqa: PLC0415 — keeps config out of module import

    try:
        return load_config(server.paths.config).default_timezone
    except Exception:  # noqa: BLE001 — a missing config must not break the calendar
        return "UTC"


# ----------------------------------------------------------------- calendars


@router.post(
    "/{family_id}/calendars",
    response_model=CalendarResponse,
    status_code=201,
    summary="Create a family calendar",
    description=(
        "A named container for events. Omit `timezone` to inherit the "
        "server timezone; set it when a family keeps its own clock."
    ),
)
async def create_calendar(
    family_id: str, body: CalendarCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_calendar(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/calendars",
    response_model=list[CalendarResponse],
    summary="List family calendars",
)
async def list_calendars(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).list_calendars(family_id, user)


@router.get(
    "/{family_id}/calendars/{calendar_id}",
    response_model=CalendarResponse,
    summary="Read one family calendar",
)
async def get_calendar(
    family_id: str, calendar_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).get_calendar(family_id, calendar_id, user)


@router.patch(
    "/{family_id}/calendars/{calendar_id}",
    response_model=CalendarResponse,
    summary="Update a family calendar",
)
async def update_calendar(
    family_id: str,
    calendar_id: str,
    body: CalendarUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update_calendar(
        family_id, calendar_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{family_id}/calendars/{calendar_id}",
    status_code=204,
    summary="Delete a family calendar",
    description="Its events are removed with it.",
)
async def delete_calendar(
    family_id: str, calendar_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_calendar(family_id, calendar_id, user)
    return Response(status_code=204)


# -------------------------------------------------------------------- events


@router.post(
    "/{family_id}/calendar-events",
    response_model=CalendarEventResponse,
    status_code=201,
    summary="Create a calendar event",
    description=(
        "Store one row per event, recurring or not. A repeating event is "
        "never expanded on write; its occurrences are computed for the "
        "window a caller asks about."
    ),
)
async def create_event(
    family_id: str, body: CalendarEventCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_event(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/calendar-events",
    response_model=list[CalendarEventResponse],
    summary="List calendar events",
    description="Stored rows, not occurrences. Use `calendar-occurrences` for a window.",
)
async def list_events(
    family_id: str,
    server: Server,
    user: CurrentUser,
    calendar_id: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query(description="CONFIRMED | CANCELLED")] = None,
) -> object:
    return _manager(server).list_events(
        family_id, user, calendar_id=calendar_id, status=status
    )


@router.get(
    "/{family_id}/calendar-events/{event_id}",
    response_model=CalendarEventResponse,
    summary="Read one calendar event",
)
async def get_event(
    family_id: str, event_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).get_event(family_id, event_id, user)


@router.patch(
    "/{family_id}/calendar-events/{event_id}",
    response_model=CalendarEventResponse,
    summary="Update a calendar event",
    description=(
        "Pass `expected_version` for optimistic concurrency: a stale "
        "writer gets 409 rather than silently overwriting a sibling's edit."
    ),
)
async def update_event(
    family_id: str,
    event_id: str,
    body: CalendarEventUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    values = body.model_dump(exclude_unset=True)
    expected_version = values.pop("expected_version", None)
    return _manager(server).update_event(
        family_id, event_id, user, values, expected_version=expected_version
    )


@router.post(
    "/{family_id}/calendar-events/{event_id}/cancel",
    response_model=CalendarEventResponse,
    summary="Cancel a calendar event",
    description=(
        "Keeps the row and marks it CANCELLED, so the family's history "
        "still shows that something was planned."
    ),
)
async def cancel_event(
    family_id: str,
    event_id: str,
    server: Server,
    user: CurrentUser,
    expected_version: Annotated[int | None, Query()] = None,
) -> object:
    return _manager(server).cancel_event(
        family_id, event_id, user, expected_version=expected_version
    )


@router.delete(
    "/{family_id}/calendar-events/{event_id}",
    status_code=204,
    summary="Delete a calendar event",
)
async def delete_event(
    family_id: str, event_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_event(family_id, event_id, user)
    return Response(status_code=204)


# --------------------------------------------------------------- occurrences


@router.get(
    "/{family_id}/calendar-occurrences",
    response_model=list[OccurrenceResponse],
    summary="List occurrences overlapping a window",
    description=(
        "Expands recurrence rules for the requested range and returns "
        f"instances ordered by start time. The window may not exceed "
        f"{MAX_OCCURRENCE_WINDOW_DAYS} days and the result is capped, so a "
        "daily rule over a decade cannot be asked for in one call. "
        "Timestamps are UTC epoch seconds; render them in the returned "
        "`timezone` (or the server timezone) for display."
    ),
)
async def list_occurrences(
    family_id: str,
    server: Server,
    user: CurrentUser,
    window_start: Annotated[int, Query(alias="from", description="UTC epoch seconds.")],
    window_end: Annotated[int, Query(alias="to", description="UTC epoch seconds.")],
    calendar_id: Annotated[str | None, Query()] = None,
    include_cancelled: Annotated[bool, Query()] = False,
) -> object:
    return _manager(server).occurrences(
        family_id,
        user,
        window_start=window_start,
        window_end=window_end,
        calendar_id=calendar_id,
        include_cancelled=include_cancelled,
    )


# ------------------------------------------------------------------ exports


__all__ = [
    "router",
    "CalendarCreateBody",
    "CalendarEventCreateBody",
    "CalendarEventResponse",
    "CalendarEventUpdateBody",
    "CalendarResponse",
    "CalendarUpdateBody",
    "OccurrenceResponse",
    "server_timezone",
]