"""HTTP adapters for the HomeMind family foundation."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server
from octop.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    RelationshipType,
    SpaceType,
)
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


def _manager(server: OctopServer) -> FamilyManager:
    assert server.services is not None
    return FamilyManager(server.services.family_repo)


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
