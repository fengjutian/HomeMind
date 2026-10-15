from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.middleware.workspace_audit import WorkspaceAuditMiddleware
from octop.infra.workspace_audit.context import workspace_audit_scope
from octop.infra.workspace_audit.service import WorkspaceAuditContext, WorkspaceChangeService


class _Repo:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def insert(self, values: dict[str, Any]) -> None:
        self.rows.append(values)


class _Workspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def aexists(self, path: str) -> bool:
        return path in self.files

    async def adownload_bytes(self, path: str) -> bytes | None:
        return self.files.get(path)

    def present_path(self, path: str) -> str:
        return path.lstrip("/")

    async def als(self, path: str) -> Any:
        prefix = "" if path == "." else path.rstrip("/") + "/"
        children: dict[str, bool] = {}
        for file_path in self.files:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
            first, separator, _rest = remainder.partition("/")
            children[prefix + first] = bool(separator)
        return SimpleNamespace(
            entries=[{"path": child, "is_dir": is_dir} for child, is_dir in children.items()]
        )


def _request(name: str, path: str) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {"file_path": path}, "id": "call-42"},
        tool=None,
        state={},
        runtime=None,  # type: ignore[arg-type]
    )


def _context() -> WorkspaceAuditContext:
    return WorkspaceAuditContext(
        actor_type="user",
        actor_id="7",
        user_id=7,
        agent_id="agent-1",
        source="agent_tool",
        thread_id="thread-1",
        turn_id="turn-1",
    )


async def test_write_file_records_turn_tool_and_diff() -> None:
    workspace = _Workspace()
    workspace.files["notes.txt"] = b"old\n"
    repo = _Repo()
    middleware = WorkspaceAuditMiddleware(
        workspace=workspace,
        service=WorkspaceChangeService(repo),  # type: ignore[arg-type]
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        workspace.files["notes.txt"] = b"new\n"
        return ToolMessage(content="ok", tool_call_id="call-42")

    with workspace_audit_scope(_context()):
        await middleware.awrap_tool_call(_request("write_file", "notes.txt"), handler)

    assert len(repo.rows) == 1
    row = repo.rows[0]
    assert row["operation"] == "overwrite"
    assert row["thread_id"] == "thread-1"
    assert row["turn_id"] == "turn-1"
    assert row["tool_call_id"] == "call-42"
    assert "-old" in row["diff_text"]
    assert "+new" in row["diff_text"]


async def test_read_tool_and_calls_without_turn_context_are_not_audited() -> None:
    workspace = _Workspace()
    repo = _Repo()
    middleware = WorkspaceAuditMiddleware(
        workspace=workspace,
        service=WorkspaceChangeService(repo),  # type: ignore[arg-type]
    )

    async def handler(request: ToolCallRequest) -> ToolMessage:
        workspace.files["notes.txt"] = b"new"
        return ToolMessage(content="ok", tool_call_id=str(request.tool_call["id"]))

    await middleware.awrap_tool_call(_request("write_file", "notes.txt"), handler)
    with workspace_audit_scope(_context()):
        await middleware.awrap_tool_call(_request("read_file", "notes.txt"), handler)

    assert repo.rows == []


async def test_execute_records_each_file_changed_by_script() -> None:
    workspace = _Workspace()
    workspace.files = {"keep.txt": b"same", "old.txt": b"old"}
    repo = _Repo()
    middleware = WorkspaceAuditMiddleware(
        workspace=workspace,
        service=WorkspaceChangeService(repo),  # type: ignore[arg-type]
    )

    async def handler(_request: ToolCallRequest) -> ToolMessage:
        workspace.files["old.txt"] = b"new"
        workspace.files["created.py"] = b"print('ok')\n"
        return ToolMessage(content="ok", tool_call_id="call-42")

    with workspace_audit_scope(_context()):
        await middleware.awrap_tool_call(_request("execute", "ignored"), handler)

    assert {(row["operation"], row["path"]) for row in repo.rows} == {
        ("create", "created.py"),
        ("overwrite", "old.txt"),
    }
    assert {row["tool_call_id"] for row in repo.rows} == {"call-42"}
