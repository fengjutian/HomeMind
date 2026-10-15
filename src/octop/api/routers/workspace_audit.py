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

_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "client_secret",
}


def _redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.lower() in _SENSITIVE_KEYS else _redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in value]
    return value


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
    id: str
    kind: str
    ts: float
    status: str | None = None
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
    next_before_ts: float | None = None
    has_more: bool = False


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
    before_ts: float | None = Query(default=None),
    since_ts: float | None = Query(default=None),
    kinds: str | None = Query(default=None),
    turn_id: str | None = Query(default=None),
    tool_call_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    include_sensitive: bool = Query(default=False),
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
    requested_kinds = (
        {value.strip() for value in kinds.split(",") if value.strip()} if kinds else None
    )
    source_limit = min(500, max(limit * 3, limit + 1))
    messages, messages_have_more = server.services.thread_message_repo.page_before_time(
        thread_id, before_ts=before_ts, limit=source_limit
    )
    for row in messages:
        try:
            body = json.loads(row.message_json)
        except json.JSONDecodeError:
            body = {"role": row.role, "content": row.message_json}
        timeline.append(
            ThreadAuditItemOut(
                id=f"message:{row.seq}",
                kind="message",
                ts=float(row.created_at),
                status="complete",
                payload=body if include_sensitive else _redact_sensitive(body),
            )
        )

    runtime = getattr(server, "app_runtime", None)
    trajectory_service = getattr(runtime, "trajectory_service", None)
    events, events_have_more = (
        trajectory_service.list_events_before_time(
            thread_id, before_ts=before_ts, limit=source_limit
        )
        if trajectory_service is not None
        else ([], False)
    )
    for event in events:
        payload = asdict(event)
        payload.pop("thread_id", None)
        payload.pop("agent_id", None)
        item_kind = (
            "tool_call"
            if event.kind == "tool"
            else "model_call"
            if event.kind in {"assistant", "token", "reasoning"}
            else "trajectory"
        )
        timeline.append(
            ThreadAuditItemOut(
                id=f"trajectory:{event.event_id}",
                kind=item_kind,
                ts=event.ts,
                status="error" if event.is_error else "complete",
                turn_id=event.turn_id,
                tool_call_id=str(event.payload.get("tool_call_id"))
                if event.payload.get("tool_call_id") is not None
                else None,
                payload=payload if include_sensitive else _redact_sensitive(payload),
            )
        )

    changes = server.services.workspace_change_repo.list_for_agent(
        thread.agent_id,
        thread_id=thread_id,
        before_ts=int(before_ts) + 1 if before_ts is not None else None,
        limit=source_limit + 1,
    )
    changes_have_more = len(changes) > source_limit
    changes = changes[:source_limit]
    for row in changes:
        change = _out(row)
        timeline.append(
            ThreadAuditItemOut(
                id=f"workspace:{row.change_id}",
                kind="workspace_change",
                ts=float(row.ts),
                status=row.status,
                turn_id=row.turn_id,
                tool_call_id=row.tool_call_id,
                payload=(
                    change.model_dump()
                    if include_sensitive
                    else _redact_sensitive(change.model_dump())
                ),
            )
        )

    def included(item: ThreadAuditItemOut) -> bool:
        if before_ts is not None and item.ts >= before_ts:
            return False
        if since_ts is not None and item.ts < since_ts:
            return False
        if requested_kinds is not None and item.kind not in requested_kinds:
            return False
        if turn_id is not None and item.turn_id != turn_id:
            return False
        if tool_call_id is not None and item.tool_call_id != tool_call_id:
            return False
        return status is None or item.status == status

    timeline = sorted((item for item in timeline if included(item)), key=lambda item: item.ts)
    wants_messages = requested_kinds is None or "message" in requested_kinds
    wants_events = requested_kinds is None or bool(
        requested_kinds & {"trajectory", "model_call", "tool_call"}
    )
    wants_changes = requested_kinds is None or "workspace_change" in requested_kinds
    has_more = (
        len(timeline) > limit
        or (wants_messages and messages_have_more)
        or (wants_events and events_have_more)
        or (wants_changes and changes_have_more)
    )
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
        next_before_ts=timeline[0].ts if has_more and timeline else None,
        has_more=has_more,
    )
