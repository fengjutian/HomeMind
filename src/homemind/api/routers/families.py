"""HTTP adapters for the HomeMind family foundation."""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    RelationshipType,
    SpaceType,
)
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class FamilyResponse(_RowModel):
    id: str
    name: str
    avatar: str | None
    owner_user_id: int
    timezone: str
    locale: str
    created_at: int
    updated_at: int


class FamilyCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Family display name.")
    avatar: str | None = Field(default=None, max_length=500)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=100)
    locale: str = Field(default="zh", pattern="^(zh|en)$")


class FamilyUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    avatar: str | None = Field(default=None, max_length=500)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)
    locale: str | None = Field(default=None, pattern="^(zh|en)$")


class FamilyMemberResponse(_RowModel):
    id: str
    family_id: str
    user_id: int | None
    display_name: str
    role: str
    avatar: str | None
    birthday: str | None
    status: str
    created_at: int
    updated_at: int


class FamilyMemberCreateBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    role: MemberRole = MemberRole.MEMBER
    user_id: int | None = None
    avatar: str | None = Field(default=None, max_length=500)
    birthday: str | None = Field(default=None, max_length=10, description="ISO date (YYYY-MM-DD).")


class FamilyMemberUpdateBody(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    role: MemberRole | None = None
    avatar: str | None = Field(default=None, max_length=500)
    birthday: str | None = Field(default=None, max_length=10)
    status: Literal["ACTIVE", "INACTIVE"] | None = None


class FamilyRelationshipResponse(_RowModel):
    id: str
    family_id: str
    from_member_id: str
    to_member_id: str
    relationship_type: str
    created_at: int
    updated_at: int


class FamilyRelationshipCreateBody(BaseModel):
    from_member_id: str
    to_member_id: str
    relationship_type: RelationshipType


class FamilySpaceResponse(_RowModel):
    id: str
    family_id: str
    name: str
    space_type: str
    owner_member_id: str | None
    created_at: int
    updated_at: int


class FamilySpaceCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    space_type: SpaceType
    owner_member_id: str | None = None


class FamilySpaceUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    space_type: SpaceType | None = None
    owner_member_id: str | None = None


class FamilyPermissionResponse(_RowModel):
    id: str
    family_id: str
    subject_member_id: str | None
    space_id: str | None
    action: str
    effect: str
    expires_at: int | None
    created_by: int
    created_at: int
    updated_at: int


class FamilyPermissionCreateBody(BaseModel):
    subject_member_id: str | None = Field(
        default=None, description="Null applies the rule to all family members."
    )
    space_id: str | None = Field(default=None, description="Null applies to all spaces.")
    action: str = Field(min_length=1, max_length=100, examples=["photo.read"])
    effect: PermissionEffect
    expires_at: int | None = Field(default=None, description="Optional Unix timestamp.")


class FamilyPermissionUpdateBody(BaseModel):
    subject_member_id: str | None = None
    space_id: str | None = None
    action: str | None = Field(default=None, min_length=1, max_length=100)
    effect: PermissionEffect | None = None
    expires_at: int | None = None


class FamilyPermissionEvaluateBody(BaseModel):
    subject_member_id: str
    action: str = Field(min_length=1, max_length=100)
    space_id: str | None = None


class FamilyPermissionDecision(BaseModel):
    effect: PermissionEffect


class FamilyAssetResponse(BaseModel):
    id: str
    family_id: str
    source_id: str | None
    space_id: str | None
    asset_type: str
    name: str
    uri: str
    mime_type: str
    size_bytes: int
    content_hash: str
    captured_at: int | None
    indexed_at: int
    metadata: dict[str, object]
    visibility: str
    status: str


class FamilyPhotoMetadataResponse(_RowModel):
    asset_id: str
    width: int | None
    height: int | None
    camera_make: str | None
    camera_model: str | None
    latitude: float | None
    longitude: float | None
    taken_at: int | None


class FamilyAssetScanBody(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)
    space_id: str | None = None
    recursive: bool = True
    visibility: Literal["PUBLIC", "FAMILY", "PRIVATE", "SENSITIVE"] = "FAMILY"


class FamilyAssetScanResponse(BaseModel):
    source_id: str
    indexed: int
    unchanged: int
    skipped: int
    failed: int
    missing: int
    asset_ids: list[str]
    errors: list[str]


class FamilyAssetSourceResponse(_RowModel):
    id: str
    family_id: str
    space_id: str | None
    directory_uri: str
    recursive: bool
    visibility: str
    status: str
    last_scanned_at: int | None
    created_by: int
    created_at: int
    updated_at: int


def _manager(server: OctopServer) -> FamilyManager:
    assert server.services is not None
    run_migrations(server.services.db)
    return FamilyManager(HomeMindServices.from_pool(server.services.db).family_repo)


def _asset_manager(server: OctopServer) -> FamilyAssetManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return FamilyAssetManager(services.family_repo, services.family_asset_repo)


def _asset_response(row: Any) -> FamilyAssetResponse:
    return FamilyAssetResponse(
        id=row.id,
        family_id=row.family_id,
        source_id=row.source_id,
        space_id=row.space_id,
        asset_type=row.asset_type,
        name=row.name,
        uri=row.uri,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        content_hash=row.content_hash,
        captured_at=row.captured_at,
        indexed_at=row.indexed_at,
        metadata=json.loads(row.metadata_json),
        visibility=row.visibility,
        status=row.status,
    )


Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


@router.post("", response_model=FamilyResponse, status_code=201, summary="Create a family")
async def create_family(body: FamilyCreateBody, server: Server, user: CurrentUser) -> object:
    return _manager(server).create_family(user, **body.model_dump())


@router.get("", response_model=list[FamilyResponse], summary="List my families")
async def list_families(server: Server, user: CurrentUser) -> object:
    return _manager(server).list_families(user)


@router.get("/{family_id}", response_model=FamilyResponse, summary="Get a family")
async def get_family(family_id: str, server: Server, user: CurrentUser) -> object:
    return _manager(server).require_access(family_id, user)


@router.patch("/{family_id}", response_model=FamilyResponse, summary="Update a family")
async def update_family(
    family_id: str, body: FamilyUpdateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).update_family(
        family_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete("/{family_id}", status_code=204, summary="Delete a family")
async def delete_family(family_id: str, server: Server, user: CurrentUser) -> Response:
    _manager(server).delete_family(family_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/members",
    response_model=FamilyMemberResponse,
    status_code=201,
    summary="Add a family member",
)
async def create_member(
    family_id: str, body: FamilyMemberCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_member(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/members", response_model=list[FamilyMemberResponse], summary="List family members"
)
async def list_members(family_id: str, server: Server, user: CurrentUser) -> object:
    manager = _manager(server)
    manager.require_access(family_id, user)
    return manager.repo.list_members(family_id)


@router.patch(
    "/{family_id}/members/{member_id}",
    response_model=FamilyMemberResponse,
    summary="Update a family member",
)
async def update_member(
    family_id: str,
    member_id: str,
    body: FamilyMemberUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update_member(
        family_id, member_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{family_id}/members/{member_id}", status_code=204, summary="Delete a family member"
)
async def delete_member(
    family_id: str, member_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_member(family_id, member_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/relationships",
    response_model=FamilyRelationshipResponse,
    status_code=201,
    summary="Create a family relationship",
)
async def create_relationship(
    family_id: str, body: FamilyRelationshipCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_relationship(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/relationships",
    response_model=list[FamilyRelationshipResponse],
    summary="List family relationships",
)
async def list_relationships(family_id: str, server: Server, user: CurrentUser) -> object:
    manager = _manager(server)
    manager.require_access(family_id, user)
    return manager.repo.list_relationships(family_id)


@router.delete(
    "/{family_id}/relationships/{relationship_id}",
    status_code=204,
    summary="Delete a family relationship",
)
async def delete_relationship(
    family_id: str, relationship_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_relationship(family_id, relationship_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/spaces",
    response_model=FamilySpaceResponse,
    status_code=201,
    summary="Create a family space",
)
async def create_space(
    family_id: str, body: FamilySpaceCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_space(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/spaces", response_model=list[FamilySpaceResponse], summary="List family spaces"
)
async def list_spaces(family_id: str, server: Server, user: CurrentUser) -> object:
    manager = _manager(server)
    manager.require_access(family_id, user)
    return manager.repo.list_spaces(family_id)


@router.patch(
    "/{family_id}/spaces/{space_id}",
    response_model=FamilySpaceResponse,
    summary="Update a family space",
)
async def update_space(
    family_id: str,
    space_id: str,
    body: FamilySpaceUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update_space(
        family_id, space_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{family_id}/spaces/{space_id}", status_code=204, summary="Delete a family space"
)
async def delete_space(
    family_id: str, space_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_space(family_id, space_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/permissions",
    response_model=FamilyPermissionResponse,
    status_code=201,
    summary="Create a family permission rule",
)
async def create_permission(
    family_id: str, body: FamilyPermissionCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_permission(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/permissions",
    response_model=list[FamilyPermissionResponse],
    summary="List family permission rules",
)
async def list_permissions(family_id: str, server: Server, user: CurrentUser) -> object:
    manager = _manager(server)
    manager.require_access(family_id, user)
    return manager.repo.list_permissions(family_id)


@router.patch(
    "/{family_id}/permissions/{permission_id}",
    response_model=FamilyPermissionResponse,
    summary="Update a family permission rule",
)
async def update_permission(
    family_id: str,
    permission_id: str,
    body: FamilyPermissionUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update_permission(
        family_id, permission_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{family_id}/permissions/{permission_id}",
    status_code=204,
    summary="Delete a family permission rule",
)
async def delete_permission(
    family_id: str, permission_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_permission(family_id, permission_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/permissions/evaluate",
    response_model=FamilyPermissionDecision,
    summary="Evaluate a family permission",
)
async def evaluate_permission(
    family_id: str,
    body: FamilyPermissionEvaluateBody,
    server: Server,
    user: CurrentUser,
) -> FamilyPermissionDecision:
    effect = _manager(server).evaluate_permission(
        family_id, user, **body.model_dump()
    )
    return FamilyPermissionDecision(effect=effect)


@router.post(
    "/{family_id}/assets/scan",
    response_model=FamilyAssetScanResponse,
    summary="Scan a local directory into the family asset index",
    description="Reads file metadata and hashes only; source files are never copied or modified.",
)
async def scan_assets(
    family_id: str,
    body: FamilyAssetScanBody,
    server: Server,
    user: CurrentUser,
) -> object:
    manager = _asset_manager(server)
    scan = partial(manager.scan_directory, family_id, user, **body.model_dump())
    return await asyncio.get_running_loop().run_in_executor(None, scan)


@router.get(
    "/{family_id}/assets",
    response_model=list[FamilyAssetResponse],
    summary="Search family assets",
)
async def search_assets(
    family_id: str,
    server: Server,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=200),
    asset_type: str | None = Query(default=None),
    space_id: str | None = Query(default=None),
    content_hash: str | None = Query(default=None, min_length=64, max_length=64),
    status: Literal["INDEXED", "MISSING"] | None = Query(default="INDEXED"),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[FamilyAssetResponse]:
    rows = _asset_manager(server).search(
        family_id,
        user,
        query=query,
        asset_type=asset_type,
        space_id=space_id,
        content_hash=content_hash,
        status=status,
        limit=limit,
    )
    return [_asset_response(row) for row in rows]


@router.get(
    "/{family_id}/asset-sources",
    response_model=list[FamilyAssetSourceResponse],
    summary="List configured family asset sources",
)
async def list_asset_sources(
    family_id: str, server: Server, user: CurrentUser
) -> object:
    return _asset_manager(server).list_sources(family_id, user)


@router.post(
    "/{family_id}/asset-sources/{source_id}/scan",
    response_model=FamilyAssetScanResponse,
    summary="Rescan a configured family asset source",
)
async def rescan_asset_source(
    family_id: str, source_id: str, server: Server, user: CurrentUser
) -> object:
    manager = _asset_manager(server)
    scan = partial(manager.scan_source, family_id, source_id, user)
    return await asyncio.get_running_loop().run_in_executor(None, scan)


@router.get(
    "/{family_id}/assets/duplicates",
    response_model=list[list[FamilyAssetResponse]],
    summary="List exact duplicate asset groups",
)
async def duplicate_assets(
    family_id: str, server: Server, user: CurrentUser
) -> list[list[FamilyAssetResponse]]:
    groups = _asset_manager(server).duplicate_groups(family_id, user)
    return [[_asset_response(row) for row in group] for group in groups]


@router.get(
    "/{family_id}/assets/{asset_id}",
    response_model=FamilyAssetResponse,
    summary="Get a family asset",
)
async def get_asset(
    family_id: str, asset_id: str, server: Server, user: CurrentUser
) -> FamilyAssetResponse:
    return _asset_response(_asset_manager(server).get(family_id, asset_id, user))


@router.get(
    "/{family_id}/assets/{asset_id}/photo-metadata",
    response_model=FamilyPhotoMetadataResponse | None,
    summary="Get indexed photo metadata",
)
async def get_photo_metadata(
    family_id: str, asset_id: str, server: Server, user: CurrentUser
) -> object:
    return _asset_manager(server).photo_metadata(family_id, asset_id, user)


@router.delete(
    "/{family_id}/assets/{asset_id}",
    status_code=204,
    summary="Remove an asset from the index",
    description="Deletes metadata only; the source file is never removed.",
)
async def delete_asset_index(
    family_id: str, asset_id: str, server: Server, user: CurrentUser
) -> Response:
    _asset_manager(server).delete_index(family_id, asset_id, user)
    return Response(status_code=204)
