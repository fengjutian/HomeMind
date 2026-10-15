"""Owner-scoped workspace change audit query APIs."""

from __future__ import annotations

import json
from dataclasses import asdict
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


class ThreadAuditItemOut(BaseModel):
    kind: str
    ts: float
    turn_id: str | None = None
    tool_call_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ThreadAuditSummaryOut(BaseModel):
    model: str | None = None
    status: str
    has_errors: bool
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    model_calls: int = 0


class ThreadAuditOut(BaseModel):
    thread_id: str
    agent_id: str
    summary: ThreadAuditSummaryOut
    timeline: list[ThreadAuditItemOut]


def _out(row: WorkspaceChangeRow) -> WorkspaceChangeOut:
    return WorkspaceChangeOut(
        **{key: value for key, value in row.__dict__.items() if key not in {"metadata_json"}},
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
    source: str | None = Query(default=None),
    operation: str | None = Query(default=None),
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
        source=source,
        operation=operation,
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


@router.get(
    "/threads/{thread_id}/audit",
    response_model=ThreadAuditOut,
    summary="Get conversation audit timeline",
)
async def get_thread_audit(
    thread_id: str,
    limit: int = Query(default=200, ge=1, le=500),
    as_user: int | None = None,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> ThreadAuditOut:
    """Merge messages, trajectory/tool events, and workspace changes chronologically."""
    assert server.services is not None
    thread = server.services.thread_repo.get(thread_id)
    if thread is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"thread {thread_id!r} not found")
    _require_owner(thread.agent_id, user=user, as_user=as_user, server=server)

    timeline: list[ThreadAuditItemOut] = []
    messages, _ = server.services.thread_message_repo.page(thread_id, limit=limit)
    for row in messages:
        try:
            body = json.loads(row.message_json)
        except json.JSONDecodeError:
            body = {"role": row.role, "content": row.message_json}
        timeline.append(ThreadAuditItemOut(kind="message", ts=float(row.created_at), payload=body))

    runtime = getattr(server, "app_runtime", None)
    trajectory_service = getattr(runtime, "trajectory_service", None)
    events = (
        trajectory_service.list_events(thread_id, before_seq=None, limit=limit, kinds=None)
        if trajectory_service is not None
        else []
    )
    for event in events:
        payload = asdict(event)
        payload.pop("thread_id", None)
        payload.pop("agent_id", None)
        timeline.append(
            ThreadAuditItemOut(
                kind="trajectory",
                ts=event.ts,
                turn_id=event.turn_id,
                tool_call_id=str(event.payload.get("tool_call_id"))
                if event.payload.get("tool_call_id") is not None
                else None,
                payload=payload,
            )
        )

    changes = server.services.workspace_change_repo.list_for_agent(
        thread.agent_id, thread_id=thread_id, limit=limit
    )
    for row in changes:
        change = _out(row)
        timeline.append(
            ThreadAuditItemOut(
                kind="workspace_change",
                ts=float(row.ts),
                turn_id=row.turn_id,
                tool_call_id=row.tool_call_id,
                payload=change.model_dump(),
            )
        )

    timeline.sort(key=lambda item: item.ts)
    timeline = timeline[-limit:]
    totals = server.services.usage_repo.thread_totals(agent_id=thread.agent_id, thread_id=thread_id)
    has_errors = any(event.is_error for event in events) or any(
        row.status == "failed" for row in changes
    )
    return ThreadAuditOut(
        thread_id=thread_id,
        agent_id=thread.agent_id,
        summary=ThreadAuditSummaryOut(
            model=thread.model_ref,
            status=server.services.thread_message_repo.projection_status(thread_id),
            has_errors=has_errors,
            input_tokens=totals["input_tokens"],
            output_tokens=totals["output_tokens"],
            total_tokens=totals["total_tokens"],
            model_calls=totals["model_calls"],
        ),
        timeline=timeline,
    )
