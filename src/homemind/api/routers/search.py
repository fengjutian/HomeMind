"""Unified HomeMind family search HTTP surface."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.search import FamilySearchManager, SearchKind
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class SearchResultResponse(BaseModel):
    kind: SearchKind
    id: str
    title: str
    snippet: str
    score: float
    metadata: dict[str, object]


def build_search_manager(server: OctopServer) -> FamilySearchManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    family = FamilyManager(services.family_repo)
    return FamilySearchManager(
        family,
        FamilyContextManager(family, services.family_context_repo),
        FamilyAssetManager(services.family_repo, services.family_asset_repo),
    )


@router.get(
    "/{family_id}/search",
    response_model=list[SearchResultResponse],
    summary="Search family context",
    description=(
        "Search members, events, memories, and indexed assets using structured "
        "filters and deterministic keyword ranking."
    ),
)
async def search_family(
    family_id: str,
    server: Server,
    user: CurrentUser,
    query: str = Query(default="", max_length=500),
    kinds: list[SearchKind] | None = Query(default=None),
    asset_type: str | None = Query(default=None, max_length=50),
    limit: int = Query(default=50, ge=1, le=200),
) -> object:
    return build_search_manager(server).search(
        family_id,
        user,
        query=query,
        kinds=set(kinds) if kinds else None,
        asset_type=asset_type,
        limit=limit,
    )
