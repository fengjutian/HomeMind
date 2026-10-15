"""Request-local workspace audit attribution for agent backend operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from octop.infra.workspace_audit.service import WorkspaceAuditContext

_CURRENT: ContextVar[WorkspaceAuditContext | None] = ContextVar(
    "octop_workspace_audit_context", default=None
)


def current_workspace_audit_context() -> WorkspaceAuditContext | None:
    return _CURRENT.get()


def set_workspace_audit_tool_call(tool_call_id: str | None) -> None:
    context = _CURRENT.get()
    if context is not None:
        _CURRENT.set(replace(context, tool_call_id=tool_call_id))


@contextmanager
def workspace_audit_tool_scope(tool_call_id: str | None) -> Iterator[None]:
    context = _CURRENT.get()
    if context is None:
        yield
        return
    token = _CURRENT.set(replace(context, tool_call_id=tool_call_id))
    try:
        yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def workspace_audit_scope(context: WorkspaceAuditContext) -> Iterator[None]:
    token = _CURRENT.set(context)
    try:
        yield
    finally:
        _CURRENT.reset(token)
