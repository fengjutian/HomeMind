"""Aggregate dashboard summary for the HomeMind home page.

The home page shows family, member count, today's tasks, upcoming
events, pending approvals, recent memories, recent assets, device
online state, and recent background jobs. Fetching each of those
separately would mean a dozen round trips on page load, so this router
answers all of them in one request.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.asset_jobs import (
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
)
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]

DAY_SECONDS = 24 * 60 * 60


class RecentAsset(BaseModel):
    id: str
    name: str
    asset_type: str
    captured_at: int | None = None


class RecentMemory(BaseModel):
    id: str
    content: str
    memory_type: str
    importance: float


class UpcomingEvent(BaseModel):
    id: str
    title: str
    event_type: str
    start_at: int
    end_at: int
    location: str | None = None


class DeviceSummary(BaseModel):
    total: int
    online: int
    offline: int


class JobSummary(BaseModel):
    id: str
    job_type: str
    status: str
    total_items: int
    processed_items: int
    failed_items: int


class DashboardSummary(BaseModel):
    """One request, everything the home page renders."""

    family_id: str
    family_name: str
    timezone: str
    member_count: int = 0
    today_task_count: int = 0
    open_task_count: int = 0
    pending_approval_count: int = 0
    pending_memory_candidate_count: int = 0
    upcoming_events: list[UpcomingEvent] = Field(default_factory=list)
    recent_memories: list[RecentMemory] = Field(default_factory=list)
    recent_assets: list[RecentAsset] = Field(default_factory=list)
    devices: DeviceSummary = Field(default_factory=lambda: DeviceSummary(total=0, online=0, offline=0))
    recent_jobs: list[JobSummary] = Field(default_factory=list)


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


@router.get(
    "/{family_id}/dashboard-summary",
    response_model=DashboardSummary,
    summary="Aggregate everything the HomeMind home page needs",
    description=(
        "One request instead of a dozen. Each section is independently "
        "permission-scoped: a member without approval rights still gets "
        "a usable summary, just with an empty approvals section."
    ),
)
async def dashboard_summary(
    family_id: str, server: Server, user: CurrentUser,
) -> DashboardSummary:
    services = _services(server)
    family = FamilyManager(services.family_repo)
    record = family.require_access(family_id, user)
    now = int(time.time())

    is_manager = _is_manager(family, family_id, user)
    pending_approvals = 0
    if is_manager:
        pending_approvals = len(
            services.family_transaction_repo.list_approvals(family_id, "PENDING"),
        )
    pending_candidates = services.memory_candidate_repo.list_for_family(
        family_id, status="PENDING", limit=100,
    ).__len__()

    events = services.family_context_repo.list_events(family_id)
    upcoming = [event for event in events if event.end_at >= now][:5]

    memories = [
        memory
        for memory in services.family_context_repo.list_all_memories(family_id)
        if memory.status == "ACTIVE"
    ]
    memories.sort(key=lambda m: (-m.importance, -m.updated_at))

    assets = services.family_asset_repo.search(family_id, limit=5)
    devices = services.family_device_repo.list_for_family(family_id)
    jobs = services.asset_job_repo.list_jobs(family_id, limit=5)

    tasks = services.family_task_repo.list_for_family(family_id)
    day_start = now - (now % DAY_SECONDS)

    return DashboardSummary(
        family_id=family_id,
        family_name=record.name,
        timezone=record.timezone,
        member_count=len(services.family_repo.list_members(family_id)),
        today_task_count=sum(
            1 for task in tasks if (task.due_at or 0) >= day_start
        ),
        open_task_count=sum(
            1 for task in tasks if task.status not in {"DONE", "CANCELLED"}
        ),
        pending_approval_count=pending_approvals,
        pending_memory_candidate_count=pending_candidates,
        upcoming_events=[
            UpcomingEvent(
                id=event.id,
                title=event.title,
                event_type=event.event_type,
                start_at=event.start_at,
                end_at=event.end_at,
                location=event.location,
            )
            for event in upcoming
        ],
        recent_memories=[
            RecentMemory(
                id=memory.id,
                content=memory.content[:200],
                memory_type=memory.memory_type,
                importance=memory.importance,
            )
            for memory in memories[:5]
        ],
        recent_assets=[
            RecentAsset(
                id=asset.id,
                name=asset.name,
                asset_type=asset.asset_type,
                captured_at=asset.captured_at,
            )
            for asset in assets
        ],
        devices=DeviceSummary(
            total=len(devices),
            online=sum(1 for device in devices if device.status == "ONLINE"),
            offline=sum(1 for device in devices if device.status != "ONLINE"),
        ),
        recent_jobs=[
            JobSummary(
                id=job.id,
                job_type=job.job_type,
                status=job.status,
                total_items=job.total_items,
                processed_items=job.processed_items,
                failed_items=job.failed_items,
            )
            for job in jobs
            if job.status in {JOB_STATUS_PENDING, JOB_STATUS_RUNNING}
        ],
    )


def _is_manager(family: FamilyManager, family_id: str, user: User) -> bool:
    if user.is_admin:
        return True
    membership = family.repo.get_membership(family_id, user.id)
    if membership is None:
        return False
    return str(membership["role"]) in {"OWNER", "ADMIN"}


__all__ = [
    "router",
    "DashboardSummary",
    "DeviceSummary",
    "JobSummary",
    "RecentAsset",
    "RecentMemory",
    "UpcomingEvent",
]
