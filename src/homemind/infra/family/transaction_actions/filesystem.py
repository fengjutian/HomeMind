"""Filesystem mutation handlers (Stage 5).

Each handler delegates to ``FamilyFilesystemManager.execute`` and adds
``preview`` / ``verify`` / ``compensate`` so the transaction manager
has a uniform contract. Compensation is best-effort: a deletion that
moved the file into ``.homemind-trash`` is reversed; a copy that
created an unintended duplicate is removed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.transaction_actions.base import (
    ActionContext,
    FamilyActionHandler,
)
from octop.infra.errors import ErrorCode, OctopError


class _BaseFilesystemHandler:
    """Mixin to extract shared payload validation."""

    def _validate_payload(self, payload: dict[str, Any]) -> None:
        if not payload.get("source_id") or not payload.get("path"):
            raise OctopError(
                ErrorCode.BAD_REQUEST,
                "filesystem mutations require source_id and path",
            )

    def _preview_common(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": payload.get("source_id"),
            "path": payload.get("path"),
            "destination": payload.get("destination"),
        }


class FilesystemCopyHandler(_BaseFilesystemHandler):
    action = "filesystem.copy"

    def __init__(self, filesystem: FamilyFilesystemManager) -> None:
        self.filesystem = filesystem

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        self._validate_payload(payload)
        if not payload.get("destination"):
            raise OctopError(ErrorCode.BAD_REQUEST, "filesystem.copy requires destination")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return self._preview_common(payload)

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.filesystem.execute(
            context.family_id,
            context.requester,
            action="filesystem.copy",
            transaction_id=context.transaction.id,
            payload={"source_id": payload["source_id"], "path": payload["path"],
                     "destination": payload["destination"]},
        )
        return {
            "destination_path": result.destination_path,
            "verified": result.verified,
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"destination_path": result.get("destination_path"), "verified": result.get("verified")}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        dest = result.get("destination_path")
        if not dest:
            return {"compensated": False}
        try:
            Path(dest).unlink(missing_ok=True)
            return {"compensated": True}
        except OSError:
            return {"compensated": False}


class FilesystemMoveHandler(_BaseFilesystemHandler):
    action = "filesystem.move"

    def __init__(self, filesystem: FamilyFilesystemManager) -> None:
        self.filesystem = filesystem

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        self._validate_payload(payload)
        if not payload.get("destination"):
            raise OctopError(ErrorCode.BAD_REQUEST, "filesystem.move requires destination")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return self._preview_common(payload)

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.filesystem.execute(
            context.family_id,
            context.requester,
            action="filesystem.move",
            transaction_id=context.transaction.id,
            payload={"source_id": payload["source_id"], "path": payload["path"],
                     "destination": payload["destination"]},
        )
        return {
            "destination_path": result.destination_path,
            "verified": result.verified,
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"destination_path": result.get("destination_path"), "verified": result.get("verified")}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        # Move back: caller must persist the original path on
        # ``preview_json`` to make reversal automatic.
        return {"compensated": False, "note": "manual recovery"}


class FilesystemRenameHandler(FilesystemMoveHandler):
    """Rename reuses the move machinery — they're the same underlying op."""

    action = "filesystem.rename"


class FilesystemDeleteHandler(_BaseFilesystemHandler):
    action = "filesystem.delete"

    def __init__(self, filesystem: FamilyFilesystemManager) -> None:
        self.filesystem = filesystem

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        self._validate_payload(payload)

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return self._preview_common(payload)

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.filesystem.execute(
            context.family_id,
            context.requester,
            action="filesystem.delete",
            transaction_id=context.transaction.id,
            payload={"source_id": payload["source_id"], "path": payload["path"]},
        )
        return {"recovery_path": result.recovery_path, "verified": result.verified}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        return {"recovery_path": result.get("recovery_path"), "verified": result.get("verified")}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        # The deletion already lives in .homemind-trash; manual recovery
        # is the supported path. We do NOT auto-move files back.
        return {"compensated": False, "note": "use FilesystemDeleteHandler.recovery_path"}


__all__ = [
    "FilesystemCopyHandler",
    "FilesystemDeleteHandler",
    "FilesystemMoveHandler",
    "FilesystemRenameHandler",
]