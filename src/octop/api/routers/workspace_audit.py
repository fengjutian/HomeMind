"""Owner-scoped workspace change audit query APIs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from octop.api.common.agent import require_agent_owner_row
from octop.api.deps import current_user, get_server
from octop.infra.db.repos.workspace_changes import WorkspaceChangeRow
from octop.infra.errors import ErrorCode, OctopError

router = APIRouter()


class WorkspaceChangeOut(BaseModel):
    change_id: str
    ts: int
    actor_type: str
    actor_id: str | None = None
    user_id: int | None = None
    agent_id: str
    thread_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    source: str
    operation: str
    path: str
    destination: str | None = None
    before_exists: bool
    after_exists: bool
    before_size: int | None = None
    after_size: int | None = None
    before_hash: str | None = None
    after_hash: str | None = None
    diff_kind: str | None = None
    diff_text: str | None = None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    integrity_hash: str
    schema_version: int


class WorkspaceChangeListOut(BaseModel):
    items: list[WorkspaceChangeOut]


def _out(row: WorkspaceChangeRow) -> WorkspaceChangeOut:
    return WorkspaceChangeOut(
        **{
            key: value
            for key, value in row.__dict__.items()
            if key not in {"metadata_json"}
        },
        metadata=json.loads(row.metadata_json),
    )


def _require_owner(agent_id: str, *, user: Any, as_user: int | None, server: Any) -> None:
    require_agent_owner_row(agent_id, user=user, as_user=as_user, server=server)


@router.get(
    "/agents/{agent_id}/workspace/changes",
    response_model=WorkspaceChangeListOut,
    summary="List workspace changes",
)
async def list_workspace_changes(
    agent_id: str,
    thread_id: str | None = Query(default=None),
    path: str | None = Query(default=None),
    before_ts: int | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> WorkspaceChangeListOut:
    """Return newest-first workspace audit events owned by this agent."""
    _require_owner(agent_id, user=user, as_user=as_user, server=server)
    assert server.services is not None
    rows = server.services.workspace_change_repo.list_for_agent(
        agent_id,
        thread_id=thread_id,
        path=path,
        before_ts=before_ts,
        limit=limit,
    )
    return WorkspaceChangeListOut(items=[_out(row) for row in rows])


@router.get(
    "/agents/{agent_id}/workspace/changes/{change_id}",
    response_model=WorkspaceChangeOut,
    summary="Get workspace change detail",
)
async def get_workspace_change(
    agent_id: str,
    change_id: str,
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> WorkspaceChangeOut:
    """Return hashes, diff, status, and correlation metadata for one change."""
    _require_owner(agent_id, user=user, as_user=as_user, server=server)
    assert server.services is not None
    row = server.services.workspace_change_repo.get(change_id)
    if row is None or row.agent_id != agent_id:
        raise OctopError(ErrorCode.NOT_FOUND, f"workspace change {change_id!r} not found")
    return _out(row)
