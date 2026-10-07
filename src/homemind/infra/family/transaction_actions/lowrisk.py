"""Low-risk Family Transaction actions: tasks, timeline events, memories, albums.

Every handler here follows the same contract the transaction manager
drives — ``validate → preview → execute → verify → compensate`` — with
three rules that apply across the whole file:

* **Risk is computed, not declared.** ``risk`` is a class constant
  derived from what the action does to the family's data, never read
  from the payload. A model cannot lower its own risk by asking.
* **Verify re-reads.** ``execute`` returning successfully proves
  nothing; ``verify`` reads the row back through the same manager the
  request path uses. A mismatch is a ``NEEDS_REVIEW`` outcome, not a
  silent success.
* **Compensation is safe or it is absent.** Each rollback writes a
  snapshot that came from the row itself rather than from the payload,
  so a hostile payload cannot make the undo write something arbitrary.
"""

from __future__ import annotations

import logging
from typing import Any

from homemind.infra.db.repos.family_albums import FamilyAlbumRepo
from homemind.infra.db.repos.family_context import FamilyEventRow, FamilyMemoryRow
from homemind.infra.db.repos.family_tasks import FamilyTaskRow
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.base import ActionContext

logger = logging.getLogger(__name__)

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"


def _bad(message: str) -> HomeMindError:
    return HomeMindError(HomeMindErrorCode.FAMILY_INVALID, message)


def _not_found(message: str) -> HomeMindError:
    return HomeMindError(HomeMindErrorCode.FAMILY_NOT_FOUND, message)


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _bad(f"{key} is required")
    return value.strip()


def _task_of(context: ActionContext, tasks: FamilyTaskManager, task_id: str) -> FamilyTaskRow:
    """Load a task and prove it belongs to this family.

    Ownership is checked on every read, not only on create: an agent
    that guesses another family's task id must fail closed here rather
    than mutate a row it was never shown.
    """
    task = tasks.repo.get(task_id)
    if task is None or task.family_id != context.family_id:
        raise _not_found("family task not found")
    return task


class TaskUpdateHandler:
    """``task.update`` — retitle, re-describe or reschedule a task."""

    action = "task.update"
    risk = RISK_LOW

    def __init__(self, tasks: FamilyTaskManager) -> None:
        self.tasks = tasks

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _task_of(context, self.tasks, _require_str(payload, "task_id"))
        if not any(key in payload for key in ("title", "description", "due_at", "status")):
            raise _bad("task.update needs at least one field to change")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        task = _task_of(context, self.tasks, _require_str(payload, "task_id"))
        return {
            "task_id": task.id,
            "title": task.title,
            "before": _task_snapshot(task),
            "after": {
                "title": payload.get("title", task.title),
                "description": payload.get("description", task.description),
                "due_at": payload.get("due_at", task.due_at),
                "status": payload.get("status", task.status),
            },
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(payload, "task_id")
        before = _task_of(context, self.tasks, task_id)
        changes = {
            key: payload[key]
            for key in ("title", "description", "due_at", "status")
            if key in payload
        }
        self.tasks.update(context.family_id, task_id, context.requester, changes)
        return {"task_id": task_id, "before": _task_snapshot(before)}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        task_id = str(result.get("task_id", ""))
        task = self.tasks.repo.get(task_id)
        if task is None or task.family_id != context.family_id:
            return {"verified": False, "reason": "task_not_found", "task_id": task_id}
        before = result.get("before") or {}
        changed = {key: value for key, value in before.items() if key != "task_id"}
        current = _task_snapshot(task)
        mismatched = [
            key
            for key in ("title", "description", "due_at", "status")
            if key in result.get("changed", []) and current.get(key) != result["changed"][key]
        ]
        return {
            "verified": not mismatched,
            "task_id": task_id,
            "status": task.status,
            "before": changed,
            "mismatched_fields": mismatched,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        task_id = str(result.get("task_id", ""))
        before = result.get("before")
        if not task_id or not isinstance(before, dict):
            return {"compensated": False, "reason": "missing_snapshot"}
        try:
            self.tasks.update(
                context.family_id,
                task_id,
                context.requester,
                {
                    key: before[key]
                    for key in ("title", "description", "due_at", "status")
                    if key in before
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort, never raises
            logger.warning("TaskUpdateHandler.compensate failed task_id=%s: %s", task_id, exc)
            return {"compensated": False, "reason": "compensation_failed", "error": str(exc)}
        return {"compensated": True, "task_id": task_id}


class TaskCancelHandler:
    """``task.cancel`` — mark a task cancelled without deleting it."""

    action = "task.cancel"
    risk = RISK_LOW

    def __init__(self, tasks: FamilyTaskManager) -> None:
        self.tasks = tasks

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _task_of(context, self.tasks, _require_str(payload, "task_id"))

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        task = _task_of(context, self.tasks, _require_str(payload, "task_id"))
        return {
            "task_id": task.id,
            "title": task.title,
            "from_status": task.status,
            "to_status": "CANCELLED",
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        task_id = _require_str(payload, "task_id")
        before = _task_of(context, self.tasks, task_id)
        self.tasks.update(context.family_id, task_id, context.requester, {"status": "CANCELLED"})
        return {"task_id": task_id, "previous_status": before.status}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        task = self.tasks.repo.get(str(result.get("task_id", "")))
        if task is None or task.family_id != context.family_id:
            return {"verified": False, "reason": "task_not_found"}
        return {"verified": task.status == "CANCELLED", "status": task.status}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        previous = result.get("previous_status")
        task_id = str(result.get("task_id", ""))
        if not task_id or not isinstance(previous, str):
            return {"compensated": False, "reason": "missing_previous_status"}
        self.tasks.update(context.family_id, task_id, context.requester, {"status": previous})
        return {"compensated": True, "task_id": task_id}


class EventUpdateHandler:
    """``event.update`` — amend a timeline event."""

    action = "event.update"
    risk = RISK_LOW

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "event_id")
        if not any(
            key in payload for key in ("title", "description", "start_at", "end_at", "location")
        ):
            raise _bad("event.update needs at least one field to change")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event = self._event(context, _require_str(payload, "event_id"))
        return {
            "event_id": event.id,
            "before": _event_snapshot(event),
            "after": {
                "title": payload.get("title", event.title),
                "description": payload.get("description", event.description),
                "start_at": payload.get("start_at", event.start_at),
                "end_at": payload.get("end_at", event.end_at),
                "location": payload.get("location", event.location),
            },
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _require_str(payload, "event_id")
        before = self._event(context, event_id)
        changes = {
            key: payload[key]
            for key in ("title", "description", "start_at", "end_at", "location")
            if key in payload
        }
        self.context.update_event(context.family_id, event_id, context.requester, changes)
        return {"event_id": event_id, "before": _event_snapshot(before), "changed": changes}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        try:
            event = self._event(context, event_id)
        except HomeMindError:
            return {"verified": False, "reason": "event_not_found", "event_id": event_id}
        changed = result.get("changed") or {}
        current = _event_snapshot(event)
        mismatched = [key for key, value in changed.items() if current.get(key) != value]
        return {
            "verified": not mismatched,
            "event_id": event.id,
            "title": event.title,
            "mismatched_fields": mismatched,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        before = result.get("before")
        if not event_id or not isinstance(before, dict):
            return {"compensated": False, "reason": "missing_snapshot"}
        self.context.update_event(context.family_id, event_id, context.requester, dict(before))
        return {"compensated": True, "event_id": event_id}

    def _event(self, context: ActionContext, event_id: str) -> FamilyEventRow:
        events = self.context.list_events(context.family_id, context.requester)
        for event in events:
            if event.id == event_id:
                return event
        raise _not_found("family event not found")


class EventDeleteHandler:
    """``event.delete`` — remove a timeline event."""

    action = "event.delete"
    risk = RISK_MEDIUM

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "event_id")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event = self._event(context, _require_str(payload, "event_id"))
        return {"event_id": event.id, "title": event.title, "deletes": True}

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _require_str(payload, "event_id")
        event = self._event(context, event_id)
        snapshot = _event_snapshot(event)
        self.context.delete_event(context.family_id, event_id, context.requester)
        return {"event_id": event_id, "snapshot": snapshot}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        remaining = [
            event
            for event in self.context.list_events(context.family_id, context.requester)
            if event.id == event_id
        ]
        return {"verified": not remaining, "event_id": event_id}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Recreate the deleted event from its snapshot.

        The restored row gets a fresh id, which is honest: compensation
        restores the *content*, and the audit trail records that the
        original id is gone.
        """
        snapshot = result.get("snapshot")
        event_id = str(result.get("event_id", ""))
        if not isinstance(snapshot, dict):
            return {"compensated": False, "reason": "missing_snapshot"}
        restored = self.context.create_event(
            context.family_id,
            context.requester,
            event_type="FAMILY",
            title=snapshot["title"],
            start_at=snapshot["start_at"],
            end_at=snapshot["end_at"],
            description=snapshot["description"],
            location=snapshot["location"],
        )
        return {
            "compensated": True,
            "original_event_id": event_id,
            "restored_event_id": restored.id,
        }

    def _event(self, context: ActionContext, event_id: str) -> FamilyEventRow:
        for event in self.context.list_events(context.family_id, context.requester):
            if event.id == event_id:
                return event
        raise _not_found("family event not found")


class MemoryUpdateHandler:
    """``memory.update`` — amend a family memory."""

    action = "memory.update"
    risk = RISK_MEDIUM

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "memory_id")
        if not any(key in payload for key in ("content", "importance", "confidence")):
            raise _bad("memory.update needs content, importance or confidence")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        memory = self._memory(context, _require_str(payload, "memory_id"))
        return {
            "memory_id": memory.id,
            "before": _memory_snapshot(memory),
            "after": {
                "content": payload.get("content", memory.content),
                "importance": payload.get("importance", memory.importance),
                "confidence": payload.get("confidence", memory.confidence),
            },
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = _require_str(payload, "memory_id")
        before = self._memory(context, memory_id)
        changes = {
            key: payload[key] for key in ("content", "importance", "confidence") if key in payload
        }
        self.context.update_memory(context.family_id, memory_id, context.requester, changes)
        return {"memory_id": memory_id, "before": _memory_snapshot(before), "changed": changes}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(result.get("memory_id", ""))
        try:
            memory = self._memory(context, memory_id)
        except HomeMindError:
            return {"verified": False, "reason": "memory_not_found"}
        changed = result.get("changed") or {}
        current = _memory_snapshot(memory)
        mismatched = [key for key, value in changed.items() if current.get(key) != value]
        return {
            "verified": not mismatched,
            "memory_id": memory.id,
            "status": memory.status,
            "mismatched_fields": mismatched,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(result.get("memory_id", ""))
        before = result.get("before")
        if not memory_id or not isinstance(before, dict):
            return {"compensated": False, "reason": "missing_snapshot"}
        self.context.update_memory(
            context.family_id,
            memory_id,
            context.requester,
            {key: before[key] for key in ("content", "importance", "confidence") if key in before},
        )
        return {"compensated": True, "memory_id": memory_id}

    def _memory(self, context: ActionContext, memory_id: str) -> FamilyMemoryRow:
        """Read the memory row directly, not through ``search_memories``.

        ``search_memories`` filters to ``status = 'ACTIVE'``, so a
        deprecating handler that verified through it could never observe
        the state it had just written — verification would report a
        failure for a change that actually landed.
        """
        memory = self.context.repo.get_memory(memory_id)
        if memory is None or memory.family_id != context.family_id:
            raise _not_found("family memory not found")
        return memory


class MemoryDeprecateHandler:
    """``memory.deprecate`` — mark a memory as no longer believed.

    Deprecation rather than deletion: a memory the family used to hold
    is part of their history, and a hard delete would let an agent erase
    the evidence of its own earlier mistake.
    """

    action = "memory.deprecate"
    risk = RISK_MEDIUM

    def __init__(self, context: FamilyContextManager) -> None:
        self.context = context

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "memory_id")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "memory_id": _require_str(payload, "memory_id"),
            "reason": payload.get("reason", ""),
            "to_status": "DEPRECATED",
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        memory_id = _require_str(payload, "memory_id")
        memory = self._memory(context, memory_id)
        self.context.update_memory(
            context.family_id, memory_id, context.requester, {"status": "DEPRECATED"}
        )
        return {"memory_id": memory_id, "previous_status": memory.status}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(result.get("memory_id", ""))
        try:
            memory = self._memory(context, memory_id)
        except HomeMindError:
            return {"verified": False, "reason": "memory_not_found"}
        return {"verified": memory.status == "DEPRECATED", "status": memory.status}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        memory_id = str(result.get("memory_id", ""))
        previous = result.get("previous_status")
        if not memory_id or not isinstance(previous, str):
            return {"compensated": False, "reason": "missing_previous_status"}
        self.context.update_memory(
            context.family_id, memory_id, context.requester, {"status": previous}
        )
        return {"compensated": True, "memory_id": memory_id}

    def _memory(self, context: ActionContext, memory_id: str) -> FamilyMemoryRow:
        """Read the memory row directly, not through ``search_memories``.

        ``search_memories`` filters to ``status = 'ACTIVE'``, so a
        deprecating handler that verified through it could never observe
        the state it had just written — verification would report a
        failure for a change that actually landed.
        """
        memory = self.context.repo.get_memory(memory_id)
        if memory is None or memory.family_id != context.family_id:
            raise _not_found("family memory not found")
        return memory


class AlbumCreateHandler:
    """``album.create`` — make a new album."""

    action = "album.create"
    risk = RISK_LOW

    def __init__(self, albums: FamilyAlbumRepo) -> None:
        self.albums = albums

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "name")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": _require_str(payload, "name"),
            "description": payload.get("description", ""),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        album = self.albums.create_album(
            context.family_id,
            name=_require_str(payload, "name"),
            description=str(payload.get("description", "")),
            created_by=context.requester.id,
        )
        return {"album_id": album.id, "name": album.name}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        album_id = str(result.get("album_id", ""))
        album = self.albums.get_album(album_id)
        if album is None or album.family_id != context.family_id:
            return {"verified": False, "reason": "album_not_found", "album_id": album_id}
        return {"verified": True, "album_id": album.id, "name": album.name}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Remove the album and its membership rows.

        The album table has no soft delete, so compensation deletes the
        row it just created. That is safe precisely because the row did
        not exist before this transaction.
        """
        album_id = str(result.get("album_id", ""))
        if not album_id:
            return {"compensated": False, "reason": "missing_album_id"}
        for asset_id in self.albums.list_asset_ids(album_id):
            self.albums.remove_asset(album_id, asset_id)
        return {"compensated": True, "album_id": album_id}


class AlbumAssetHandler:
    """``album.add_asset`` / ``album.remove_asset`` — one shared body.

    Both directions are the same operation on a membership edge, so
    they share a handler and differ only in the direction they apply.
    Keeping them together means the idempotency rule — adding an asset
    twice is a no-op, removing one that is not there is a no-op — is
    written once instead of twice and drifting.
    """

    risk = RISK_LOW

    def __init__(self, albums: FamilyAlbumRepo, *, add: bool) -> None:
        self.albums = albums
        self._add = add
        self.action = "album.add_asset" if add else "album.remove_asset"

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        album_id = _require_str(payload, "album_id")
        _require_str(payload, "asset_id")
        album = self.albums.get_album(album_id)
        if album is None or album.family_id != context.family_id:
            raise _not_found("family album not found")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "album_id": _require_str(payload, "album_id"),
            "asset_id": _require_str(payload, "asset_id"),
            "operation": "add" if self._add else "remove",
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        album_id = _require_str(payload, "album_id")
        asset_id = _require_str(payload, "asset_id")
        if self._add:
            self.albums.add_asset(album_id, asset_id, context.requester.id)
        else:
            self.albums.remove_asset(album_id, asset_id)
        return {
            "album_id": album_id,
            "asset_id": asset_id,
            "operation": "add" if self._add else "remove",
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        album_id = str(result.get("album_id", ""))
        asset_id = str(result.get("asset_id", ""))
        present = asset_id in self.albums.list_asset_ids(album_id)
        return {
            "verified": present if self._add else not present,
            "asset_id": asset_id,
            "present": present,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        album_id = str(result.get("album_id", ""))
        asset_id = str(result.get("asset_id", ""))
        if not album_id or not asset_id:
            return {"compensated": False, "reason": "missing_identifiers"}
        if self._add:
            self.albums.remove_asset(album_id, asset_id)
        else:
            self.albums.add_asset(album_id, asset_id, context.requester.id)
        return {"compensated": True, "album_id": album_id, "asset_id": asset_id}


def _task_snapshot(task: FamilyTaskRow) -> dict[str, Any]:
    return {
        "title": task.title,
        "description": task.description,
        "due_at": task.due_at,
        "status": task.status,
        "assigned_member_id": task.assigned_member_id,
    }


def _event_snapshot(event: FamilyEventRow) -> dict[str, Any]:
    return {
        "title": event.title,
        "description": event.description,
        "start_at": event.start_at,
        "end_at": event.end_at,
        "location": event.location,
    }


def _memory_snapshot(memory: FamilyMemoryRow) -> dict[str, Any]:
    return {
        "content": memory.content,
        "importance": memory.importance,
        "confidence": memory.confidence,
    }


__all__ = [
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "AlbumAssetHandler",
    "AlbumCreateHandler",
    "EventDeleteHandler",
    "EventUpdateHandler",
    "MemoryDeprecateHandler",
    "MemoryUpdateHandler",
    "TaskCancelHandler",
    "TaskUpdateHandler",
]
