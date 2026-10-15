"""BackendProtocol decorator that audits agent filesystem tool mutations."""

from __future__ import annotations

from typing import Any

from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.workspace_audit.context import current_workspace_audit_context
from octop.infra.workspace_audit.service import WorkspaceChangeService


class AuditedBackend:
    """Delegate reads unchanged and observe writes when a turn context is active."""

    def __init__(
        self, backend: Any, *, workspace: BackendWorkspace, service: WorkspaceChangeService
    ):
        self._backend = backend
        self._workspace = workspace
        self._service = service

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    def write(self, file_path: str, content: str) -> Any:
        before = self._download(file_path)
        result = self._backend.write(file_path, content)
        self._record(file_path, before, self._download(file_path), result)
        return result

    async def awrite(self, file_path: str, content: str) -> Any:
        before = await self._adownload(file_path)
        result = await self._backend.awrite(file_path, content)
        self._record(file_path, before, await self._adownload(file_path), result)
        return result

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        before = self._download(file_path)
        result = self._backend.edit(file_path, old_string, new_string, replace_all=replace_all)
        self._record(file_path, before, self._download(file_path), result)
        return result

    async def aedit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> Any:
        before = await self._adownload(file_path)
        result = await self._backend.aedit(
            file_path, old_string, new_string, replace_all=replace_all
        )
        self._record(file_path, before, await self._adownload(file_path), result)
        return result

    def upload_files(self, files: list[tuple[str, bytes]]) -> Any:
        before = {path: self._download(path) for path, _data in files}
        results = self._backend.upload_files(files)
        by_path = {str(getattr(item, "path", "")): item for item in results}
        for path, _data in files:
            self._record(path, before[path], self._download(path), by_path.get(path))
        return results

    async def aupload_files(self, files: list[tuple[str, bytes]]) -> Any:
        before = {path: await self._adownload(path) for path, _data in files}
        results = await self._backend.aupload_files(files)
        by_path = {str(getattr(item, "path", "")): item for item in results}
        for path, _data in files:
            self._record(path, before[path], await self._adownload(path), by_path.get(path))
        return results

    def delete(self, file_path: str) -> Any:
        before = self._download(file_path)
        result = self._backend.delete(file_path)
        self._record(file_path, before, None, result, operation="delete", before_exists=True)
        return result

    async def adelete(self, file_path: str) -> Any:
        before = await self._adownload(file_path)
        result = await self._backend.adelete(file_path)
        self._record(file_path, before, None, result, operation="delete", before_exists=True)
        return result

    def _download(self, path: str) -> bytes | None:
        try:
            rows = self._backend.download_files([path])
        except Exception:
            return None
        return _downloaded_bytes(rows)

    async def _adownload(self, path: str) -> bytes | None:
        try:
            rows = await self._backend.adownload_files([path])
        except Exception:
            return None
        return _downloaded_bytes(rows)

    def _record(
        self,
        path: str,
        before: bytes | None,
        after: bytes | None,
        result: Any,
        *,
        operation: str | None = None,
        before_exists: bool | None = None,
    ) -> None:
        context = current_workspace_audit_context()
        if context is None:
            return
        error = getattr(result, "error", None) if result is not None else None
        self._service.record_observed_change(
            context=context,
            operation=operation or ("overwrite" if before is not None else "create"),
            path=self._workspace.present_path(path),
            before=before,
            after=after,
            succeeded=not bool(error),
            error_message=str(error) if error else None,
            before_exists=before_exists,
            after_exists=False if operation == "delete" and not error else None,
        )


def _downloaded_bytes(rows: Any) -> bytes | None:
    if not rows:
        return None
    row = rows[0]
    if getattr(row, "error", None):
        return None
    content = getattr(row, "content", None)
    if content is None:
        return None
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content)
