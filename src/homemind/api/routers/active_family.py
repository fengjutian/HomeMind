"""Stage 3: active-family HTTP surface.

Per-user state — which family the dashboard / agent calls as the
"current" family. Saved in the shared ``settings`` table under a
per-user key so other servers (or the dashboard) see the same value.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from homemind.infra.active_family import ActiveFamilyResolver
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


def _resolver(server: OctopServer) -> ActiveFamilyResolver:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return ActiveFamilyResolver(
        SettingsRepo(server.services.db),
        FamilyManager(services.family_repo),
    )


class ActiveFamilyResponse(BaseModel):
    family_id: str | None


class ActiveFamilySetBody(BaseModel):
    family_id: str = Field(min_length=1, max_length=100)


@router.get(
    "/me/active-family",
    response_model=ActiveFamilyResponse,
    summary="Read the current active family for the caller",
)
async def get_active_family(
    server: Server, user: CurrentUser,
) -> ActiveFamilyResponse:
    family_id = _resolver(server).get(user)
    return ActiveFamilyResponse(family_id=family_id)


@router.put(
    "/me/active-family",
    response_model=ActiveFamilyResponse,
    summary="Switch the caller's active family",
)
async def set_active_family(
    body: ActiveFamilySetBody,
    server: Server,
    user: CurrentUser,
) -> ActiveFamilyResponse:
    family_id = _resolver(server).set(user, body.family_id)
    return ActiveFamilyResponse(family_id=family_id)


@router.delete(
    "/me/active-family",
    response_model=ActiveFamilyResponse,
    summary="Clear the caller's active family",
)
async def clear_active_family(
    server: Server, user: CurrentUser,
) -> ActiveFamilyResponse:
    _resolver(server).clear(user)
    return ActiveFamilyResponse(family_id=None)
