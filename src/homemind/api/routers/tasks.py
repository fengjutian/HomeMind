"""HTTP adapters for HomeMind family tasks."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager, TaskStatus
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class TaskCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    assigned_member_id: str | None = None
    due_at: int | None = None


class TaskUpdateBody(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus | None = None
    assigned_member_id: str | None = None
    due_at: int | None = None


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    title: str
    description: str
    status: str
    assigned_member_id: str | None
    due_at: int | None
    created_by: int
    created_at: int
    updated_at: int


def _manager(server: OctopServer) -> FamilyTaskManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return FamilyTaskManager(FamilyManager(services.family_repo), services.family_task_repo)


@router.post(
    "/{family_id}/tasks",
    response_model=TaskResponse,
    status_code=201,
    summary="Create a family task",
)
async def create_task(
    family_id: str, body: TaskCreateBody, server: Server, user: CurrentUser
) -> object:
    return _manager(server).create(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/tasks",
    response_model=list[TaskResponse],
    summary="List family tasks",
)
async def list_tasks(
    family_id: str,
    server: Server,
    user: CurrentUser,
    status: Annotated[TaskStatus | None, Query()] = None,
) -> object:
    return _manager(server).list(family_id, user, status=status)


@router.patch(
    "/{family_id}/tasks/{task_id}",
    response_model=TaskResponse,
    summary="Update a family task",
)
async def update_task(
    family_id: str,
    task_id: str,
    body: TaskUpdateBody,
    server: Server,
    user: CurrentUser,
) -> object:
    return _manager(server).update(family_id, task_id, user, body.model_dump(exclude_unset=True))


@router.delete(
    "/{family_id}/tasks/{task_id}",
    status_code=204,
    summary="Delete a family task",
)
async def delete_task(family_id: str, task_id: str, server: Server, user: CurrentUser) -> Response:
    _manager(server).delete(family_id, task_id, user)
    return Response(status_code=204)
