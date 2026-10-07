"""HTTP surface for the family notification centre.

Thin by design: validate, delegate to :class:`NotificationManager`,
shape the response. Every route is scoped to the caller's own inbox —
there is deliberately no "list another member's notifications" route,
because a notification is addressed to a person, not to a family.

Times are UTC epoch seconds; ``read_at`` and ``expires_at`` are
``None`` while unread and unexpired respectively.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.notifications import NotificationManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class NotificationResponse(BaseModel):
    """One inbox entry, resolved but still language-neutral.

    ``title_key`` / ``body_key`` plus ``params`` are what the server
    stored; the dashboard turns them into text in the reader's locale.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    user_id: int
    type: str
    title_key: str
    body_key: str
    params: dict[str, object] = Field(default_factory=dict)
    target_type: str | None
    target_id: str | None
    severity: str
    read_at: int | None
    created_at: int
    expires_at: int | None


class UnreadCountResponse(BaseModel):
    unread: int


class NotificationPageResponse(BaseModel):
    """One page of the mobile inbox.

    ``next_cursor`` is ``None`` on the last page — not an empty string —
    so a client testing it for truthiness cannot loop forever.
    """

    items: list[NotificationResponse]
    next_cursor: str | None
    has_more: bool


class MarkAllReadResponse(BaseModel):
    marked: int


class PurgeExpiredResponse(BaseModel):
    removed: int


class NotificationPrefsBody(BaseModel):
    disabled_types: list[str] = Field(
        default_factory=list,
        description="Notification types to mute. Omit to change nothing else.",
    )
    quiet_hours_start: int | None = Field(
        default=None,
        description="Local minutes from midnight, 0-1439. Both quiet-hour "
        "fields must be set together; null clears them.",
    )
    quiet_hours_end: int | None = Field(
        default=None,
        description="Local minutes from midnight, 0-1439. A window that ends "
        "before it starts wraps past midnight.",
    )
    timezone: str | None = Field(
        default=None, description="IANA name used to evaluate quiet hours."
    )


class NotificationPrefsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    family_id: str
    user_id: int
    disabled_types: list[str]
    quiet_hours_start: int | None
    quiet_hours_end: int | None
    timezone: str | None


def _manager(server: OctopServer) -> NotificationManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    from octop.config import load_config  # noqa: PLC0415 — config stays out of import time

    try:
        timezone = load_config(server.paths.config).default_timezone
    except Exception:  # noqa: BLE001 — a missing config must not break the inbox
        timezone = "UTC"
    return NotificationManager(
        FamilyManager(services.family_repo),
        services.family_notification_repo,
        server_timezone=timezone,
    )


@router.get(
    "/{family_id}/notifications",
    response_model=list[NotificationResponse],
    summary="List the caller's notifications, newest first",
    description=(
        "Returns the caller's own inbox for one family. Expired entries "
        "are filtered at read time, so a lapsed notification disappears "
        "even before the periodic sweep removes the row."
    ),
)
async def list_notifications(
    family_id: str,
    server: Server,
    user: CurrentUser,
    type: Annotated[str | None, Query(description="Filter by notification type.")] = None,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> object:
    return _manager(server).list_inbox(
        family_id, user, type=type, unread_only=unread_only, limit=limit, offset=offset
    )


@router.get(
    "/{family_id}/notifications/page",
    response_model=NotificationPageResponse,
    summary="One cursor-paginated page of notifications",
    description=(
        "The mobile contract. Pass `cursor` from the previous page's "
        "`next_cursor` until it comes back `None`. Paging is keyset-based, "
        "so a notification arriving mid-scroll shifts nothing — no item is "
        "skipped or repeated."
    ),
)
async def list_notifications_page(
    family_id: str,
    server: Server,
    user: CurrentUser,
    cursor: Annotated[str | None, Query(description="Opaque token from the previous page.")] = None,
    type: Annotated[str | None, Query()] = None,
    unread_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100, description="Page size.")] = 20,
) -> object:
    from homemind.infra.cursor import InvalidCursor  # noqa: PLC0415
    from homemind.infra.errors import HomeMindError, HomeMindErrorCode  # noqa: PLC0415

    try:
        page = _manager(server).list_inbox_page(
            family_id,
            user,
            limit=limit,
            cursor=cursor,
            unread_only=unread_only,
            type=type,
        )
    except InvalidCursor as exc:
        # A bad cursor sends the client back to page one rather than
        # returning a page from an unexpected position.
        raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, str(exc)) from exc
    return NotificationPageResponse(
        items=[NotificationResponse.model_validate(row) for row in page.items],
        next_cursor=page.next_cursor,
        has_more=page.has_more,
    )


@router.get(
    "/{family_id}/notifications/unread-count",
    response_model=UnreadCountResponse,
    summary="Count the caller's unread notifications",
    description=(
        "Derived from storage, not from a pushed socket, so the badge is "
        "correct after a reconnect, a restart, or a week offline."
    ),
)
async def unread_count(family_id: str, server: Server, user: CurrentUser) -> object:
    return UnreadCountResponse(unread=_manager(server).unread_count(family_id, user))


@router.post(
    "/{family_id}/notifications/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one notification read",
    description=(
        "Scoped to the caller: a notification belonging to another user "
        "answers 404 rather than being marked."
    ),
)
async def mark_read(
    family_id: str, notification_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).mark_read(family_id, notification_id, user)


@router.post(
    "/{family_id}/notifications/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark every notification read",
)
async def mark_all_read(family_id: str, server: Server, user: CurrentUser) -> object:
    return MarkAllReadResponse(marked=_manager(server).mark_all_read(family_id, user))


@router.delete(
    "/{family_id}/notifications/expired",
    response_model=PurgeExpiredResponse,
    status_code=200,
    summary="Drop the caller's lapsed notifications",
    description="Scoped to the caller's own inbox; no family-wide delete exists.",
)
async def purge_expired(family_id: str, server: Server, user: CurrentUser) -> object:
    return PurgeExpiredResponse(removed=_manager(server).purge_expired(family_id, user))


@router.get(
    "/{family_id}/notifications/preferences",
    response_model=NotificationPrefsResponse,
    summary="Read the caller's notification preferences",
    description=(
        "Returns sensible defaults when the user has never set any, so "
        "a first-time client needs no special case."
    ),
)
async def get_preferences(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).get_prefs(family_id, user)


@router.put(
    "/{family_id}/notifications/preferences",
    response_model=NotificationPrefsResponse,
    summary="Replace the caller's notification preferences",
)
async def put_preferences(
    family_id: str, body: NotificationPrefsBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).save_prefs(
        family_id,
        user,
        disabled_types=body.disabled_types,
        quiet_hours_start=body.quiet_hours_start,
        quiet_hours_end=body.quiet_hours_end,
        timezone=body.timezone,
    )


__all__ = [
    "router",
    "MarkAllReadResponse",
    "NotificationPrefsBody",
    "NotificationPrefsResponse",
    "NotificationResponse",
    "PurgeExpiredResponse",
    "UnreadCountResponse",
]
