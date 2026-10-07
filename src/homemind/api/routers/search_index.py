"""Paginated family search over the unified index (Stage 7).

Replaces the previous unbounded result list with an opaque, signed
cursor. The response shape is stable:

    { items: [{ kind, id, title, snippet, score, matched_by, highlights,
                metadata }], next_cursor, has_more }

``cursor`` is a server-side key, so the client cannot edit it to skip or
replay rows.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.search_index import sanitize_visibility
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.search_index_manager import (
    CURSOR_TTL_SECONDS,
    MAX_LIMIT,
    FamilySearchManager,
    FamilySearchQuery,
)
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class SearchHit(BaseModel):
    kind: str
    id: str
    title: str
    snippet: str
    score: float
    matched_by: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    items: list[SearchHit]
    next_cursor: str | None
    has_more: bool
    total_candidates: int


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _manager(services: HomeMindServices) -> FamilySearchManager:
    return FamilySearchManager(
        FamilyManager(services.family_repo),
        services.search_index_repo,
        permission_evaluator=FamilyPermissionEvaluator(services.family_repo),
    )


@router.get(
    "/{family_id}/search/indexed",
    response_model=SearchResponse,
    summary="Paginated search over the unified index (Stage 7)",
    description=(
        "Permission filtering runs in SQL against the unified index, so "
        "documents the caller may not read are never fetched. Pagination "
        "uses an opaque signed cursor rather than an offset, so "
        "concurrent writes cannot make page 2 repeat or skip a row."
    ),
)
async def search_family_indexed(
    family_id: str,
    server: Server,
    user: CurrentUser,
    query: Annotated[str, Query(description="Free-text query.")] = "",
    kinds: str | None = Query(
        default=None,
        description=(
            "Comma-separated kinds: MEMBER, EVENT, MEMORY, ASSET, ALBUM, "
            "OBJECT, SCENE, LOCATION, PHOTO_DESCRIPTION."
        ),
    ),
    member_id: Annotated[list[str] | None, Query()] = None,
    event_id: Annotated[list[str] | None, Query()] = None,
    space_id: Annotated[list[str] | None, Query()] = None,
    asset_type: Annotated[list[str] | None, Query()] = None,
    visibility: Annotated[str | None, Query()] = None,
    start_at: Annotated[int | None, Query()] = None,
    end_at: Annotated[int | None, Query()] = None,
    location: Annotated[str | None, Query()] = None,
    cursor: Annotated[str | None, Query()] = None,
    limit: int = Query(default=50, ge=1, le=MAX_LIMIT),
) -> SearchResponse:
    services = _services(server)
    manager = _manager(services)
    page = manager.search(
        family_id,
        user,
        FamilySearchQuery(
            text=query,
            kinds=frozenset(
                part.strip().upper()
                for part in (kinds or "").split(",")
                if part.strip()
            ),
            member_ids=tuple(member_id or ()),
            event_ids=tuple(event_id or ()),
            space_ids=tuple(space_id or ()),
            asset_types=tuple(asset_type or ()),
            start_at=start_at,
            end_at=end_at,
            location=location,
            cursor=cursor,
            limit=limit,
            visibility=visibility,
        ),
    )
    payload = page.as_payload()
    return SearchResponse(
        items=[SearchHit(**item) for item in payload["items"]],
        next_cursor=payload["next_cursor"],
        has_more=payload["has_more"],
        total_candidates=page.total_candidates,
    )


__all__ = [
    "router",
    "CURSOR_TTL_SECONDS",
    "SearchHit",
    "SearchResponse",
    "sanitize_visibility",
]
