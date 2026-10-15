"""Audit mutations made by the harness built-in workspace file tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from pathlib import PurePosixPath
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from octop.infra.workspace_audit.context import (
    current_workspace_audit_context,
    workspace_audit_tool_scope,
)
from octop.infra.workspace_audit.service import WorkspaceChangeService

_MUTATION_TOOLS = frozenset({"write_file", "edit_file", "delete_file"})
_SNAPSHOT_TOOLS = frozenset({"execute", "bash", "run_terminal_cmd", "python"})
_SNAPSHOT_SKIP_DIRS = frozenset(
    {".git", ".octop", "__pycache__", ".venv", "node_modules", ".cache"}
)


class WorkspaceAuditMiddleware(AgentMiddleware[Any, Any]):
    def __init__(self, *, workspace: Any, service: WorkspaceChangeService) -> None:
        super().__init__()
        self._workspace = workspace
        self._service = service

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        tool_id, name, path = _mutation_call(request)
        if path is None:
            return handler(request)
        before_exists = self._workspace.exists(path)
        before = self._workspace.download_bytes(path) if before_exists else None
        with workspace_audit_tool_scope(tool_id):
            try:
                result = handler(request)
            except Exception as exc:
                self._record(
                    name=name,
                    path=path,
                    before=before,
                    after=before,
                    before_exists=before_exists,
                    after_exists=before_exists,
                    succeeded=False,
                    error_message=str(exc),
                )
                raise
            after_exists = self._workspace.exists(path)
            after = self._workspace.download_bytes(path) if after_exists else None
            succeeded, error = _tool_result_status(result)
            self._record(
                name=name,
                path=path,
                before=before,
                after=after,
                before_exists=before_exists,
                after_exists=after_exists,
                succeeded=succeeded,
                error_message=error,
            )
            return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        tool_id, name, path = _mutation_call(request)
        if name in _SNAPSHOT_TOOLS:
            return await self._wrap_snapshot_call(request, handler, tool_id)
        if path is None:
            return await handler(request)
        before_exists = await self._workspace.aexists(path)
        before = await self._workspace.adownload_bytes(path) if before_exists else None
        with workspace_audit_tool_scope(tool_id):
            try:
                result = await handler(request)
            except Exception as exc:
                self._record(
                    name=name,
                    path=path,
                    before=before,
                    after=before,
                    before_exists=before_exists,
                    after_exists=before_exists,
                    succeeded=False,
                    error_message=str(exc),
                )
                raise
            after_exists = await self._workspace.aexists(path)
            after = await self._workspace.adownload_bytes(path) if after_exists else None
            succeeded, error = _tool_result_status(result)
            self._record(
                name=name,
                path=path,
                before=before,
                after=after,
                before_exists=before_exists,
                after_exists=after_exists,
                succeeded=succeeded,
                error_message=error,
            )
            return result

    async def _wrap_snapshot_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
        tool_id: str | None,
    ) -> ToolMessage | Command[Any]:
        before = await _snapshot_workspace(self._workspace)
        with workspace_audit_tool_scope(tool_id):
            try:
                result = await handler(request)
            except Exception:
                after = await _snapshot_workspace(self._workspace)
                self._record_snapshot_changes(before, after, succeeded=False)
                raise
            after = await _snapshot_workspace(self._workspace)
            succeeded, _error = _tool_result_status(result)
            self._record_snapshot_changes(before, after, succeeded=succeeded)
            return result

    def _record_snapshot_changes(
        self,
        before: dict[str, bytes],
        after: dict[str, bytes],
        *,
        succeeded: bool,
    ) -> None:
        context = current_workspace_audit_context()
        if context is None:
            return
        for path in sorted(before.keys() | after.keys()):
            old = before.get(path)
            new = after.get(path)
            if old == new:
                continue
            operation = "delete" if new is None else "create" if old is None else "overwrite"
            self._service.record_observed_change(
                context=context,
                operation=operation,
                path=self._workspace.present_path(path),
                before=old,
                after=new,
                succeeded=succeeded,
                before_exists=old is not None,
                after_exists=new is not None,
            )

    def _record(
        self,
        *,
        name: str,
        path: str,
        before: bytes | None,
        after: bytes | None,
        before_exists: bool,
        after_exists: bool,
        succeeded: bool,
        error_message: str | None,
    ) -> None:
        context = current_workspace_audit_context()
        if context is None:
            return
        operation = (
            "delete" if name == "delete_file" else "overwrite" if before_exists else "create"
        )
        self._service.record_observed_change(
            context=context,
            operation=operation,
            path=self._workspace.present_path(path),
            before=before,
            after=after,
            succeeded=succeeded,
            error_message=error_message,
            before_exists=before_exists,
            after_exists=after_exists,
        )


def _mutation_call(request: ToolCallRequest) -> tuple[str | None, str, str | None]:
    tool_call = request.tool_call
    name = str(tool_call.get("name") or "")
    raw_id = tool_call.get("id")
    tool_id = str(raw_id) if raw_id is not None else None
    if name not in _MUTATION_TOOLS:
        return tool_id, name, None
    args = tool_call.get("args")
    if not isinstance(args, Mapping):
        return None, name, None
    raw_path = args.get("file_path") or args.get("path")
    path = str(raw_path).strip() if isinstance(raw_path, str) else ""
    return tool_id, name, path or None


async def _snapshot_workspace(workspace: Any) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    queue = ["."]
    seen: set[str] = set()
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        result = await workspace.als(current)
        for item in getattr(result, "entries", None) or []:
            raw_path = (
                item.get("path") if isinstance(item, Mapping) else getattr(item, "path", None)
            )
            if not raw_path:
                continue
            path = str(raw_path).replace("\\", "/")
            workspace_root = (
                str(getattr(workspace, "workspace_dir", "")).replace("\\", "/").rstrip("/")
            )
            if workspace_root and (path == workspace_root or path.startswith(workspace_root + "/")):
                path = path[len(workspace_root) :]
            path = path.lstrip("/")
            parts = PurePosixPath(path).parts
            if any(part in _SNAPSHOT_SKIP_DIRS for part in parts):
                continue
            is_dir = bool(
                item.get("is_dir") if isinstance(item, Mapping) else getattr(item, "is_dir", False)
            )
            if is_dir:
                queue.append(path)
                continue
            try:
                files[path] = bytes(await workspace.adownload_bytes(path))
            except (FileNotFoundError, IsADirectoryError, PermissionError):
                continue
    return files


def _tool_result_status(result: ToolMessage | Command[Any]) -> tuple[bool, str | None]:
    if isinstance(result, Command):
        return True, None
    status = getattr(result, "status", None)
    if status == "error":
        return False, str(result.content)[:2_000]
    return True, None
