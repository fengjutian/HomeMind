"""Domain service that mutates a workspace and appends an audit event."""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Any

from octop.infra.db.repos.workspace_changes import WorkspaceChangeRepo
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

_MAX_DIFF_BYTES = 65_536
_SENSITIVE_NAMES = frozenset({".env", ".octop-auth", "id_rsa", "id_ed25519"})


@dataclass(frozen=True)
class WorkspaceAuditContext:
    actor_type: str
    actor_id: str | None
    user_id: int | None
    agent_id: str
    source: str
    thread_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    request_id: str | None = None
    correlation_id: str | None = None


@dataclass(frozen=True)
class _State:
    exists: bool
    content: bytes | None

    @property
    def size(self) -> int | None:
        return len(self.content) if self.content is not None else None

    @property
    def digest(self) -> str | None:
        return hashlib.sha256(self.content).hexdigest() if self.content is not None else None


class WorkspaceChangeService:
    def __init__(self, repo: WorkspaceChangeRepo) -> None:
        self._repo = repo

    async def write_bytes(
        self,
        workspace: Any,
        path: str,
        data: bytes,
        *,
        context: WorkspaceAuditContext,
    ) -> None:
        before = await self._state(workspace, path)

        async def mutate() -> None:
            await workspace.aupload_bytes(path, data)

        await self._run(
            workspace,
            path=path,
            destination=None,
            operation="overwrite" if before.exists else "create",
            before=before,
            mutate=mutate,
            context=context,
        )

    async def mkdir(
        self, workspace: Any, path: str, *, context: WorkspaceAuditContext
    ) -> None:
        before = await self._state(workspace, path, read_content=False)

        async def mutate() -> None:
            await workspace.amkdir(path)

        await self._run(
            workspace,
            path=path,
            destination=None,
            operation="mkdir",
            before=before,
            mutate=mutate,
            context=context,
            read_after_content=False,
        )

    async def delete(
        self, workspace: Any, path: str, *, context: WorkspaceAuditContext
    ) -> None:
        before = await self._state(workspace, path)

        async def mutate() -> None:
            await workspace.adelete(path)

        await self._run(
            workspace,
            path=path,
            destination=None,
            operation="delete",
            before=before,
            mutate=mutate,
            context=context,
        )

    async def move(
        self,
        workspace: Any,
        path: str,
        destination: str,
        *,
        context: WorkspaceAuditContext,
    ) -> None:
        before = await self._state(workspace, path)

        async def mutate() -> None:
            await workspace.amove(path, destination)

        await self._run(
            workspace,
            path=path,
            destination=destination,
            operation="move",
            before=before,
            mutate=mutate,
            context=context,
            after_path=destination,
        )

    async def _run(
        self,
        workspace: Any,
        *,
        path: str,
        destination: str | None,
        operation: str,
        before: _State,
        mutate: Callable[[], Awaitable[None]],
        context: WorkspaceAuditContext,
        after_path: str | None = None,
        read_after_content: bool = True,
    ) -> None:
        try:
            await mutate()
        except Exception as exc:
            self._record(
                context=context,
                operation=operation,
                path=path,
                destination=destination,
                before=before,
                after=_State(False, None),
                status="failed",
                error=exc,
            )
            raise
        after = await self._state(
            workspace,
            after_path or path,
            read_content=read_after_content,
        )
        self._record(
            context=context,
            operation=operation,
            path=path,
            destination=destination,
            before=before,
            after=after,
            status="succeeded",
        )

    async def _state(
        self, workspace: Any, path: str, *, read_content: bool = True
    ) -> _State:
        exists = bool(await workspace.aexists(path))
        if not exists or not read_content:
            return _State(exists, None)
        try:
            content = await workspace.adownload_bytes(path)
        except (IsADirectoryError, PermissionError):
            content = None
        return _State(exists, bytes(content) if content is not None else None)

    def _record(
        self,
        *,
        context: WorkspaceAuditContext,
        operation: str,
        path: str,
        destination: str | None,
        before: _State,
        after: _State,
        status: str,
        error: Exception | None = None,
    ) -> None:
        change_id = new_ulid()
        ts = int(time.time())
        diff_kind, diff_text, metadata = _diff(path, before.content, after.content)
        payload: dict[str, Any] = {
            "change_id": change_id,
            "ts": ts,
            **asdict(context),
            "operation": operation,
            "path": path,
            "destination": destination,
            "before_exists": int(before.exists),
            "after_exists": int(after.exists),
            "before_size": before.size,
            "after_size": after.size,
            "before_hash": before.digest,
            "after_hash": after.digest,
            "diff_kind": diff_kind,
            "diff_text": diff_text,
            "status": status,
            "error_code": type(error).__name__ if error is not None else None,
            "error_message": str(error)[:2_000] if error is not None else None,
            "metadata_json": json.dumps(metadata, separators=(",", ":"), sort_keys=True),
            "schema_version": 1,
        }
        canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        payload["integrity_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        try:
            self._repo.insert(payload)
        except Exception:
            logger.exception(
                "workspace audit write failed",
                extra={"agent_id": context.agent_id, "operation": operation, "path": path},
            )


def _diff(
    path: str, before: bytes | None, after: bytes | None
) -> tuple[str | None, str | None, dict[str, bool]]:
    normalized = path.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    sensitive = any(part in _SENSITIVE_NAMES for part in parts) or ".octop/auth" in normalized
    if sensitive:
        return None, None, {"content_redacted": True}
    if (before is not None and len(before) > _MAX_DIFF_BYTES) or (
        after is not None and len(after) > _MAX_DIFF_BYTES
    ):
        return None, None, {"content_truncated": True}
    try:
        before_text = before.decode("utf-8") if before is not None else ""
        after_text = after.decode("utf-8") if after is not None else ""
    except UnicodeDecodeError:
        return "binary", None, {}
    if before is None and after is None:
        return None, None, {}
    diff = "".join(
        difflib.unified_diff(
            before_text.splitlines(keepends=True),
            after_text.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )
    return "unified", diff, {}
