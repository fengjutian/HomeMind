"""HTTP adapters for family albums and photo organization plans."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.albums import FamilyAlbumManager, OrganizationStrategy
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AlbumCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)


class AlbumResponse(_RowModel):
    id: str
    family_id: str
    name: str
    description: str
    cover_asset_id: str | None
    created_by: int
    created_at: int
    updated_at: int


class AlbumAssetBody(BaseModel):
    asset_id: str


class OrganizationPlanCreateBody(BaseModel):
    strategy: OrganizationStrategy


class OrganizationPlanResponse(BaseModel):
    id: str
    family_id: str
    strategy: str
    status: str
    groups: list[dict[str, object]]
    created_by: int
    created_at: int
    applied_at: int | None


Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


def _manager(server: OctopServer) -> FamilyAlbumManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    families = FamilyManager(services.family_repo)
    assets = FamilyAssetManager(services.family_repo, services.family_asset_repo)
    return FamilyAlbumManager(families, assets, services.family_album_repo)


def _plan_response(row: object) -> OrganizationPlanResponse:
    return OrganizationPlanResponse(
        id=row.id,  # type: ignore[attr-defined]
        family_id=row.family_id,  # type: ignore[attr-defined]
        strategy=row.strategy,  # type: ignore[attr-defined]
        status=row.status,  # type: ignore[attr-defined]
        groups=json.loads(row.groups_json),  # type: ignore[attr-defined]
        created_by=row.created_by,  # type: ignore[attr-defined]
        created_at=row.created_at,  # type: ignore[attr-defined]
        applied_at=row.applied_at,  # type: ignore[attr-defined]
    )


@router.post(
    "/{family_id}/albums",
    response_model=AlbumResponse,
    status_code=201,
    summary="Create a family album",
)
async def create_album(
    family_id: str, body: AlbumCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/albums",
    response_model=list[AlbumResponse],
    summary="List family albums",
)
async def list_albums(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).list(family_id, user)


@router.get(
    "/{family_id}/albums/{album_id}/assets",
    response_model=list[str],
    summary="List assets in a family album",
)
async def list_album_assets(
    family_id: str, album_id: str, server: Server, user: CurrentUser
) -> object:
    return _manager(server).asset_ids(family_id, album_id, user)


@router.post(
    "/{family_id}/albums/{album_id}/assets",
    status_code=204,
    summary="Add an asset to a family album",
)
async def add_album_asset(
    family_id: str,
    album_id: str,
    body: AlbumAssetBody,
    server: Server,
    user: CurrentUser,
) -> Response:
    _manager(server).add_asset(family_id, album_id, body.asset_id, user)
    return Response(status_code=204)


@router.delete(
    "/{family_id}/albums/{album_id}/assets/{asset_id}",
    status_code=204,
    summary="Remove an asset from a family album",
)
async def remove_album_asset(
    family_id: str,
    album_id: str,
    asset_id: str,
    server: Server,
    user: CurrentUser,
) -> Response:
    _manager(server).remove_asset(family_id, album_id, asset_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/organization-plans",
    response_model=OrganizationPlanResponse,
    status_code=201,
    summary="Create a non-destructive photo organization plan",
)
async def create_plan(
    family_id: str,
    body: OrganizationPlanCreateBody,
    server: Server,
    user: CurrentUser,
) -> OrganizationPlanResponse:
    return _plan_response(_manager(server).plan(family_id, user, body.strategy))


@router.get(
    "/{family_id}/organization-plans/{plan_id}",
    response_model=OrganizationPlanResponse,
    summary="Get a photo organization plan",
)
async def get_plan(
    family_id: str, plan_id: str, server: Server, user: CurrentUser
) -> OrganizationPlanResponse:
    return _plan_response(_manager(server).get_plan(family_id, plan_id, user))


@router.post(
    "/{family_id}/organization-plans/{plan_id}/apply",
    response_model=OrganizationPlanResponse,
    summary="Apply an organization plan as album links",
    description="Creates album links only; original files are never moved or deleted.",
)
async def apply_plan(
    family_id: str, plan_id: str, server: Server, user: CurrentUser
) -> OrganizationPlanResponse:
    return _plan_response(_manager(server).apply_plan(family_id, plan_id, user))
