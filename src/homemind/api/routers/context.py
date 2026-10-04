"""HTTP adapters for family events, memories, and context resolution."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.context import FamilyContextManager, MemoryType
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class _RowModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class EventBody(BaseModel):
    event_type: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    start_at: int
    end_at: int
    location: str | None = Field(default=None, max_length=200)
    description: str = Field(default="", max_length=5000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EventUpdateBody(BaseModel):
    event_type: str | None = Field(default=None, min_length=1, max_length=50)
    title: str | None = Field(default=None, min_length=1, max_length=200)
    start_at: int | None = None
    end_at: int | None = None
    location: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    metadata: dict[str, Any] | None = None


class EventResponse(_RowModel):
    id: str
    family_id: str
    event_type: str
    title: str
    start_at: int
    end_at: int
    location: str | None
    description: str
    metadata: dict[str, Any]
    created_by: int
    created_at: int
    updated_at: int


class MemoryBody(BaseModel):
    subject_type: Literal["FAMILY", "MEMBER", "EVENT", "ASSET"]
    subject_id: str | None = None
    content: str = Field(min_length=1, max_length=10000)
    memory_type: MemoryType
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=0.5, ge=0, le=1)
    visibility: Literal["PUBLIC", "FAMILY", "PRIVATE", "SENSITIVE"] = "FAMILY"
    source_type: str = Field(min_length=1, max_length=50)
    source_id: str | None = None
    expires_at: int | None = None


class MemoryUpdateBody(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    memory_type: MemoryType | None = None
    importance: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    visibility: Literal["PUBLIC", "FAMILY", "PRIVATE", "SENSITIVE"] | None = None
    expires_at: int | None = None
    status: Literal["ACTIVE", "DISABLED", "ARCHIVED"] | None = None


class MemoryResponse(_RowModel):
    id: str
    family_id: str
    subject_type: str
    subject_id: str | None
    content: str
    memory_type: str
    importance: float
    confidence: float
    visibility: str
    source_type: str
    source_id: str | None
    created_by: int
    created_at: int
    updated_at: int
    expires_at: int | None
    status: str


class ContextResolveBody(BaseModel):
    query: str = Field(min_length=1, max_length=2000)


class ContextResponse(_RowModel):
    family_id: str
    current_member_id: str
    member_ids: list[str]
    relationship_ids: list[str]
    event_ids: list[str]
    memory_ids: list[str]
    permissions: list[str]


def _manager(server: OctopServer) -> FamilyContextManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return FamilyContextManager(FamilyManager(services.family_repo), services.family_context_repo)


def _event_response(row: Any) -> EventResponse:
    return EventResponse.model_validate(
        {**row.__dict__, "metadata": json.loads(row.metadata_json)}
    )


@router.post(
    "/{family_id}/events",
    response_model=EventResponse,
    status_code=201,
    summary="Create a family event",
)
async def create_event(
    family_id: str, body: EventBody, server: Server, user: CurrentUser
) -> EventResponse:
    return _event_response(_manager(server).create_event(family_id, user, **body.model_dump()))


@router.get(
    "/{family_id}/events",
    response_model=list[EventResponse],
    summary="List family events",
)
async def list_events(
    family_id: str,
    server: Server,
    user: CurrentUser,
    start_at: int | None = Query(default=None),
    end_at: int | None = Query(default=None),
) -> list[EventResponse]:
    rows = _manager(server).list_events(
        family_id, user, start_at=start_at, end_at=end_at
    )
    return [_event_response(row) for row in rows]


@router.patch(
    "/{family_id}/events/{event_id}",
    response_model=EventResponse,
    summary="Update a family event",
)
async def update_event(
    family_id: str,
    event_id: str,
    body: EventUpdateBody,
    server: Server,
    user: CurrentUser,
) -> EventResponse:
    row = _manager(server).update_event(
        family_id, event_id, user, body.model_dump(exclude_unset=True)
    )
    return _event_response(row)


@router.delete("/{family_id}/events/{event_id}", status_code=204, summary="Delete a family event")
async def delete_event(
    family_id: str, event_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_event(family_id, event_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/memories",
    response_model=MemoryResponse,
    status_code=201,
    summary="Create a family memory",
)
async def create_memory(
    family_id: str, body: MemoryBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create_memory(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/memories",
    response_model=list[MemoryResponse],
    summary="Search family memories",
)
async def search_memories(
    family_id: str,
    server: Server,
    user: CurrentUser,
    query: str | None = Query(default=None, max_length=500),
) -> object:
    return _manager(server).search_memories(family_id, user, query)


@router.patch(
    "/{family_id}/memories/{memory_id}",
    response_model=MemoryResponse,
    summary="Update a family memory",
)
async def update_memory(
    family_id: str,
    memory_id: str,
    body: MemoryUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update_memory(
        family_id, memory_id, user, body.model_dump(exclude_unset=True)
    )


@router.delete(
    "/{family_id}/memories/{memory_id}",
    status_code=204,
    summary="Delete a family memory",
)
async def delete_memory(
    family_id: str, memory_id: str, server: Server, user: CurrentUser
) -> Response:
    _manager(server).delete_memory(family_id, memory_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/context/resolve",
    response_model=ContextResponse,
    summary="Resolve family context",
)
async def resolve_context(
    family_id: str, body: ContextResolveBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).resolve(family_id, user, body.query)
