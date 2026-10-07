"""HTTP surface for schedulable family tasks (Stage 4).

The existing ``/tasks`` routes stay exactly as they were — a legacy
client that only knows ``title`` / ``description`` / ``due_at`` still
works. Everything here is additive and lives under ``/task-scheduler``
so a reader can tell at a glance which half of the task surface is the
new machinery.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.task_scheduler import FamilyTaskScheduler
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class ScheduledTaskCreateBody(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    task_type: str = Field(
        default="AGENT", description="MANUAL | AGENT | DEVICE. AGENT and DEVICE need a schedule."
    )
    agent_id: str | None = Field(
        default=None, description="Required for an AGENT task: the agent that will run it."
    )
    schedule_at: int | None = Field(
        default=None, description="UTC epoch seconds this task becomes runnable."
    )
    recurrence_rule: str | None = Field(
        default=None, description="RFC 5545 RRULE; each run spawns exactly one successor."
    )
    parent_task_id: str | None = None
    depends_on: list[str] = Field(
        default_factory=list, description="Task ids that must reach DONE first."
    )
    priority: int = Field(default=0, ge=-100, le=100)
    max_attempts: int = Field(default=3, ge=1, le=20)
    assigned_member_id: str | None = None


class TaskDependencyBody(BaseModel):
    depends_on_task_id: str


class ScheduledTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    family_id: str
    title: str
    description: str
    status: str
    task_type: str
    priority: int
    assigned_member_id: str | None
    due_at: int | None
    schedule_at: int | None
    recurrence_rule: str | None
    agent_id: str | None
    transaction_id: str | None
    parent_task_id: str | None
    attempt_count: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: int | None
    started_at: int | None
    completed_at: int | None
    result_summary: str | None
    last_error: str | None
    version: int
    created_at: int
    updated_at: int


class TaskAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    attempt_number: int
    status: str
    summary: str | None
    error: str | None
    started_at: int
    finished_at: int | None


class TaskDependencyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    depends_on_task_id: str
    created_at: int


class TaskBoardResponse(BaseModel):
    """Tasks grouped by the columns the kanban board renders."""

    todo: list[ScheduledTaskResponse]
    scheduled: list[ScheduledTaskResponse]
    in_progress: list[ScheduledTaskResponse]
    waiting_approval: list[ScheduledTaskResponse]
    done: list[ScheduledTaskResponse]
    failed: list[ScheduledTaskResponse]
    blocked: list[ScheduledTaskResponse]


def _scheduler(server: OctopServer) -> FamilyTaskScheduler:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return FamilyTaskScheduler(FamilyManager(services.family_repo), services.family_task_repo)


@router.post(
    "/{family_id}/task-scheduler/tasks",
    response_model=ScheduledTaskResponse,
    status_code=201,
    summary="Create a schedulable or agent-owned task",
    description=(
        "An AGENT or DEVICE task needs a `schedule_at`, a recurrence "
        "rule, or both; an AGENT task additionally needs the `agent_id` "
        "that will run it. Dependencies are checked for cycles before "
        "the task is stored, so a graph that could wait forever is "
        "refused at write time rather than discovered much later."
    ),
)
async def create_scheduled_task(
    family_id: str, body: ScheduledTaskCreateBody, server: Server, user: CurrentUser
) -> object:
    return _scheduler(server).create_scheduled(family_id, user, **body.model_dump())


@router.get(
    "/{family_id}/task-scheduler/board",
    response_model=TaskBoardResponse,
    summary="Group tasks into board columns",
    description="One query per status, shaped for the kanban view.",
)
async def task_board(family_id: str, server: Server, user: CurrentUser) -> TaskBoardResponse:
    scheduler = _scheduler(server)
    scheduler.family.require_access(family_id, user)
    rows = [
        ScheduledTaskResponse.model_validate(task)
        for task in scheduler.repo.list_for_family(family_id, limit=500)
    ]
    return TaskBoardResponse(
        todo=[t for t in rows if t.status == "TODO"],
        scheduled=[t for t in rows if t.status == "SCHEDULED"],
        in_progress=[t for t in rows if t.status == "IN_PROGRESS"],
        waiting_approval=[t for t in rows if t.status == "WAITING_APPROVAL"],
        done=[t for t in rows if t.status == "DONE"],
        failed=[t for t in rows if t.status == "FAILED"],
        blocked=[t for t in rows if t.status == "BLOCKED"],
    )


@router.post(
    "/{family_id}/task-scheduler/tasks/{task_id}/schedule",
    response_model=ScheduledTaskResponse,
    summary="Set or clear a task's execution time",
    description="A finished task cannot be rescheduled; create a new one instead.",
)
async def schedule_task(
    family_id: str,
    task_id: str,
    server: Server,
    user: CurrentUser,
    schedule_at: Annotated[int | None, Query(description="UTC epoch seconds.")] = None,
) -> object:
    return _scheduler(server).schedule(family_id, task_id, user, schedule_at=schedule_at)


@router.post(
    "/{family_id}/task-scheduler/tasks/{task_id}/cancel",
    response_model=ScheduledTaskResponse,
    summary="Cancel a task so it is never claimed again",
)
async def cancel_task(family_id: str, task_id: str, server: Server, user: CurrentUser) -> object:
    return _scheduler(server).cancel(family_id, task_id, user)


@router.post(
    "/{family_id}/task-scheduler/tasks/{task_id}/retry",
    response_model=ScheduledTaskResponse,
    summary="Put a failed or blocked task back in the queue",
)
async def retry_task(family_id: str, task_id: str, server: Server, user: CurrentUser) -> object:
    return _scheduler(server).retry(family_id, task_id, user)


@router.post(
    "/{family_id}/task-scheduler/tasks/{task_id}/dependencies",
    response_model=TaskDependencyResponse,
    status_code=201,
    summary="Declare that a task waits on another",
)
async def add_dependency(
    family_id: str,
    task_id: str,
    body: TaskDependencyBody,
    server: Server,
    user: CurrentUser,
) -> object:
    scheduler = _scheduler(server)
    scheduler.add_dependency(family_id, task_id, body.depends_on_task_id, user)
    return scheduler.repo.list_dependencies(task_id)[-1]


@router.get(
    "/{family_id}/task-scheduler/tasks/{task_id}/dependencies",
    response_model=list[TaskDependencyResponse],
    summary="List what a task waits on",
)
async def list_dependencies(
    family_id: str, task_id: str, server: Server, user: CurrentUser
) -> object:
    scheduler = _scheduler(server)
    scheduler.family.require_access(family_id, user)
    scheduler._assert_task(family_id, task_id)  # noqa: SLF001 — same-package check
    return scheduler.repo.list_dependencies(task_id)


@router.delete(
    "/{family_id}/task-scheduler/tasks/{task_id}/dependencies/{depends_on_task_id}",
    status_code=204,
    summary="Remove a dependency edge",
)
async def remove_dependency(
    family_id: str, task_id: str, depends_on_task_id: str, server: Server, user: CurrentUser
) -> Response:
    removed = _scheduler(server).remove_dependency(family_id, task_id, depends_on_task_id, user)
    if not removed:
        from octop.infra.errors import ErrorCode, OctopError

        raise OctopError(ErrorCode.NOT_FOUND, "dependency not found")
    return Response(status_code=204)


@router.get(
    "/{family_id}/task-scheduler/tasks/{task_id}/attempts",
    response_model=list[TaskAttemptResponse],
    summary="List a task's execution attempts, newest first",
    description=(
        "One row per run. The task row keeps only the latest summary; "
        "this is where the history lives."
    ),
)
async def list_attempts(
    family_id: str,
    task_id: str,
    server: Server,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> object:
    scheduler = _scheduler(server)
    scheduler.family.require_access(family_id, user)
    scheduler._assert_task(family_id, task_id)  # noqa: SLF001 — same-package check
    return scheduler.repo.list_attempts(task_id, limit=limit)


__all__ = [
    "router",
    "ScheduledTaskCreateBody",
    "ScheduledTaskResponse",
    "TaskAttemptResponse",
    "TaskBoardResponse",
    "TaskDependencyBody",
    "TaskDependencyResponse",
]
