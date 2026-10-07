"""High-risk Family Transaction actions.

Risk here means "if this goes wrong, a human has to notice and fix it".
That covers commanding a physical device, changing a shared calendar,
forcing a re-index of a document library, and moving or trashing family
photos. The shape of a handler is unchanged from the low-risk file —
the difference is what ``verify`` is allowed to conclude and when the
transaction is allowed to say it finished.

**The device-command rule that matters.** ``execute`` for
``device.command`` submits the command and returns. It does **not**
block until the device reports back. A household tablet that is asleep
would otherwise hold an approved transaction open until its lease
expired, and a lease expiry that reads as "timed out" is exactly how a
half-executed command gets mislabelled. Instead the transaction parks
in ``AWAITING_DEVICE``; only a device result report closes it, and an
unknown outcome goes to ``NEEDS_REVIEW`` rather than to success.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from homemind.infra.errors import HomeMindError
from homemind.infra.family.calendar import FamilyCalendarManager
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.knowledge import KnowledgeManager
from homemind.infra.family.transaction_actions.base import ActionContext
from homemind.infra.family.transaction_actions.lowrisk import (
    RISK_HIGH,
    RISK_MEDIUM,
    _bad,
    _not_found,
    _require_str,
)

logger = logging.getLogger(__name__)

# How long a submitted device command may wait for a result before the
# transaction is parked for human review rather than assumed successful.
DEVICE_RESULT_TIMEOUT_SECONDS = 900

# A batch larger than this is always surfaced in the preview in full and
# never auto-approved, whatever the family policy says.
BATCH_PREVIEW_SAMPLE = 10


class DeviceCommandHandler:
    """``device.command`` — submit a command to a paired device.

    Execute is *fire and verify later*: the command goes onto the
    device queue and the transaction stops at ``AWAITING_DEVICE``.
    """

    action = "device.command"
    risk = RISK_HIGH
    #: Tell the transaction manager not to call ``verify`` yet.
    awaits_device_result = True

    def __init__(self, devices: DeviceRuntimeManager) -> None:
        self.devices = devices

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        device_id = _require_str(payload, "device_id")
        capability = _require_str(payload, "capability")
        device_payload = payload.get("payload")
        if not isinstance(device_payload, dict):
            raise _bad("device command payload must be an object")
        device = self.devices.repo.get(device_id)
        if device is None or device.family_id != context.family_id:
            raise _not_found("family device not found")
        if device.status in {"REVOKED", "DISABLED"}:
            raise _bad("device is not usable")
        if capability not in (device.capabilities or []):
            # A capability outside the device's declared list is how an
            # agent would smuggle an arbitrary service call through.
            raise _bad(f"device does not support capability {capability!r}")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        device_id = _require_str(payload, "device_id")
        device = self.devices.repo.get(device_id)
        return {
            "device_id": device_id,
            "device_name": getattr(device, "name", ""),
            "capability": _require_str(payload, "capability"),
            "payload": payload.get("payload", {}),
            "device_status": getattr(device, "status", "UNKNOWN"),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        device_id = _require_str(payload, "device_id")
        expires_at = int(datetime.now(UTC).timestamp()) + DEVICE_RESULT_TIMEOUT_SECONDS
        command = self.devices.enqueue_command(
            context.family_id,
            device_id,
            capability=_require_str(payload, "capability"),
            payload=dict(payload.get("payload") or {}),
            requested_by=context.requester.id,
            expires_at=expires_at,
            transaction_id=context.transaction.id,
        )
        return {
            "command_id": command.id,
            "device_id": device_id,
            "status": command.status,
            "expires_at": expires_at,
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Not reached on the happy path.

        The transaction manager skips ``verify`` while
        ``awaits_device_result`` is set and re-enters this method only
        after the device reports. Kept honest anyway: it reads the real
        command row rather than trusting the execute result.
        """
        command_id = str(result.get("command_id", ""))
        command = self.devices.repo.get_command(command_id)
        if command is None:
            return {"verified": False, "reason": "command_not_found"}
        if command.status in {"SUCCEEDED", "ACKED"}:
            return {"verified": True, "command_id": command_id, "status": command.status}
        if command.status in {"FAILED", "CANCELLED", "EXPIRED"}:
            return {"verified": False, "command_id": command_id, "status": command.status}
        # Still queued or in flight: unknown, not success.
        return {
            "verified": False,
            "command_id": command_id,
            "status": command.status,
            "reason": "device_result_pending",
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Cancel the queued command when it has not run yet.

        A command that already executed cannot be undone from here, so
        the handler says so instead of pretending: the device would have
        to reverse it, and a human needs to know that.
        """
        command_id = str(result.get("command_id", ""))
        if not command_id:
            return {"compensated": False, "reason": "missing_command_id"}
        command = self.devices.repo.get_command(command_id)
        if command is None:
            return {"compensated": False, "reason": "command_not_found"}
        if command.status in {"PENDING", "APPROVED"}:
            self.devices.cancel_command(context.family_id, command_id, context.requester)
            return {"compensated": True, "command_id": command_id}
        return {
            "compensated": False,
            "reason": "command_already_dispatched",
            "command_id": command_id,
            "status": command.status,
        }


class CalendarCreateEventHandler:
    """``calendar.create_event`` — put a meeting on the family calendar."""

    action = "calendar.create_event"
    risk = RISK_MEDIUM

    def __init__(self, calendars: FamilyCalendarManager) -> None:
        self.calendars = calendars

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "title")
        _require_str(payload, "calendar_id")
        if not isinstance(payload.get("starts_at"), int):
            raise _bad("starts_at must be an epoch second")
        if not isinstance(payload.get("ends_at"), int):
            raise _bad("ends_at must be an epoch second")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": _require_str(payload, "title"),
            "starts_at": payload.get("starts_at"),
            "ends_at": payload.get("ends_at"),
            "recurrence_rule": payload.get("recurrence_rule"),
            "timezone": payload.get("timezone"),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event = self.calendars.create_event(
            context.family_id,
            context.requester,
            calendar_id=_require_str(payload, "calendar_id"),
            title=_require_str(payload, "title"),
            starts_at=int(payload["starts_at"]),
            ends_at=int(payload["ends_at"]),
            description=str(payload.get("description", "")),
            location=payload.get("location"),
            all_day=bool(payload.get("all_day", False)),
            timezone=payload.get("timezone"),
            recurrence_rule=payload.get("recurrence_rule"),
            source_type="TASK",
            source_id=context.transaction.id,
        )
        return {"event_id": event.id, "starts_at": event.starts_at, "title": event.title}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        try:
            event = self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError:
            return {"verified": False, "reason": "event_not_found", "event_id": event_id}
        return {
            "verified": event.status == "CONFIRMED",
            "event_id": event.id,
            "status": event.status,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        if not event_id:
            return {"compensated": False, "reason": "missing_event_id"}
        self.calendars.delete_event(context.family_id, event_id, context.requester)
        return {"compensated": True, "event_id": event_id}


class CalendarUpdateEventHandler:
    """``calendar.update_event`` — move or re-describe an event."""

    action = "calendar.update_event"
    risk = RISK_MEDIUM

    def __init__(self, calendars: FamilyCalendarManager) -> None:
        self.calendars = calendars

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "event_id")
        if not any(
            key in payload
            for key in (
                "title",
                "starts_at",
                "ends_at",
                "description",
                "location",
                "recurrence_rule",
            )
        ):
            raise _bad("calendar.update_event needs at least one field to change")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event = self._event(context, _require_str(payload, "event_id"))
        return {
            "event_id": event.id,
            "before": {
                "title": event.title,
                "starts_at": event.starts_at,
                "ends_at": event.ends_at,
            },
            "after": {
                "title": payload.get("title", event.title),
                "starts_at": payload.get("starts_at", event.starts_at),
                "ends_at": payload.get("ends_at", event.ends_at),
            },
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _require_str(payload, "event_id")
        before = self._event(context, event_id)
        changes = {
            key: payload[key]
            for key in (
                "title",
                "description",
                "starts_at",
                "ends_at",
                "location",
                "recurrence_rule",
            )
            if key in payload
        }
        self.calendars.update_event(
            context.family_id,
            event_id,
            context.requester,
            changes,
            expected_version=before.version,
        )
        return {
            "event_id": event_id,
            "before": {
                "title": before.title,
                "starts_at": before.starts_at,
                "ends_at": before.ends_at,
                "description": before.description,
                "location": before.location,
                "recurrence_rule": before.recurrence_rule,
            },
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        try:
            event = self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError:
            return {"verified": False, "reason": "event_not_found", "event_id": event_id}
        return {
            "verified": event.status == "CONFIRMED",
            "event_id": event.id,
            "version": event.version,
            "starts_at": event.starts_at,
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        before = result.get("before")
        if not event_id or not isinstance(before, dict):
            return {"compensated": False, "reason": "missing_snapshot"}
        try:
            current = self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError:
            return {"compensated": False, "reason": "event_gone"}
        self.calendars.update_event(
            context.family_id,
            event_id,
            context.requester,
            dict(before),
            expected_version=current.version,
        )
        return {"compensated": True, "event_id": event_id}

    def _event(self, context: ActionContext, event_id: str) -> Any:
        try:
            return self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError as exc:
            raise _not_found("calendar event not found") from exc


class CalendarCancelEventHandler:
    """``calendar.cancel_event`` — call an event off."""

    action = "calendar.cancel_event"
    risk = RISK_MEDIUM

    def __init__(self, calendars: FamilyCalendarManager) -> None:
        self.calendars = calendars

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "event_id")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            event = self.calendars.get_event(
                context.family_id, _require_str(payload, "event_id"), context.requester
            )
        except HomeMindError as exc:
            raise _not_found("calendar event not found") from exc
        return {"event_id": event.id, "title": event.title, "to_status": "CANCELLED"}

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = _require_str(payload, "event_id")
        self.calendars.cancel_event(context.family_id, event_id, context.requester)
        return {"event_id": event_id}

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        try:
            event = self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError:
            return {"verified": False, "reason": "event_not_found", "event_id": event_id}
        return {"verified": event.status == "CANCELLED", "status": event.status}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        event_id = str(result.get("event_id", ""))
        if not event_id:
            return {"compensated": False, "reason": "missing_event_id"}
        try:
            current = self.calendars.get_event(context.family_id, event_id, context.requester)
        except HomeMindError:
            return {"compensated": False, "reason": "event_gone"}
        self.calendars.update_event(
            context.family_id,
            event_id,
            context.requester,
            {"status": "CONFIRMED"},
            expected_version=current.version,
        )
        return {"compensated": True, "event_id": event_id}


class KnowledgeReindexHandler:
    """``knowledge.reindex`` — re-parse and re-embed a family's documents.

    This is the one action whose cost is unbounded: reindexing a large
    library is minutes of embedding spend. The preview therefore always
    states how many documents are affected, so the approval screen has
    something to show before the money is spent.
    """

    action = "knowledge.reindex"
    risk = RISK_HIGH

    def __init__(self, knowledge: KnowledgeManager) -> None:
        self.knowledge = knowledge

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        asset_id = _require_str(payload, "asset_id")
        documents = self.knowledge.list_documents(context.family_id, context.requester)
        if not any(document.asset_id == asset_id for document in documents):
            raise _not_found("indexed document not found for this asset")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        asset_id = _require_str(payload, "asset_id")
        documents = self.knowledge.list_documents(context.family_id, context.requester)
        affected = [d for d in documents if d.asset_id == asset_id]
        return {
            "asset_id": asset_id,
            "document_count": len(affected),
            "chunks_to_rebuild": sum(d.chunk_count for d in affected),
            "documents": [
                {"document_id": d.id, "name": d.name} for d in affected[:BATCH_PREVIEW_SAMPLE]
            ],
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        asset_id = _require_str(payload, "asset_id")
        document = self.knowledge.index_asset(context.family_id, asset_id, context.requester)
        return {
            "document_id": document.id,
            "status": document.status,
            "chunk_count": document.chunk_count,
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        document_id = str(result.get("document_id", ""))
        documents = self.knowledge.list_documents(context.family_id, context.requester)
        for document in documents:
            if document.id == document_id:
                return {
                    "verified": document.status == "INDEXED",
                    "document_id": document.id,
                    "status": document.status,
                    "chunk_count": document.chunk_count,
                }
        return {"verified": False, "reason": "document_not_found", "document_id": document_id}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """No rollback for a re-index.

        The previous chunks are gone the moment indexing starts, and
        rebuilding them from a version that may itself be broken would
        be worse than the failure. A NEEDS_REVIEW is the honest answer.
        """
        return {
            "compensated": False,
            "reason": "reindex_is_not_reversible",
            "document_id": result.get("document_id"),
        }


class AssetBatchMoveHandler:
    """``asset.batch_move`` — move many photos into one space.

    The preview is mandatory and informative: a batch that silently
    moves three hundred files is indistinguishable, to the person
    approving it, from one that moves three. Item results are persisted
    so a partial failure is visible and retryable rather than lost.
    """

    action = "asset.batch_move"
    risk = RISK_HIGH

    def __init__(self, filesystem: FamilyFilesystemManager) -> None:
        self.filesystem = filesystem

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        self._batch(payload)
        _require_str(payload, "source_id")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        paths, destination = self._batch(payload)
        source_id = _require_str(payload, "source_id")
        return {
            "source_id": source_id,
            "destination": destination,
            "item_count": len(paths),
            "sample": paths[:BATCH_PREVIEW_SAMPLE],
            "truncated": len(paths) > BATCH_PREVIEW_SAMPLE,
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        paths, destination = self._batch(payload)
        source_id = _require_str(payload, "source_id")
        results: list[dict[str, Any]] = []
        for path in paths:
            target = f"{destination.rstrip('/')}/{path.rsplit('/', 1)[-1]}"
            item: dict[str, Any] = {"path": path, "destination_path": target}
            try:
                self.filesystem.execute(
                    context.family_id,
                    context.requester,
                    action="filesystem.move",
                    transaction_id=context.transaction.id,
                    payload={
                        "source_id": source_id,
                        "path": path,
                        "destination": target,
                    },
                )
                item["status"] = "SUCCEEDED"
            except Exception as exc:  # noqa: BLE001 — one bad file must not sink the batch
                # Recorded, not raised: a partial batch is a normal
                # outcome and the item list is how a human sees it.
                item["status"] = "FAILED"
                item["error"] = str(exc)[:300]
            results.append(item)
        return {
            "source_id": source_id,
            "item_results": results,
            "succeeded": sum(1 for item in results if item["status"] == "SUCCEEDED"),
            "failed": sum(1 for item in results if item["status"] == "FAILED"),
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        items = result.get("item_results") or []
        failed = [item for item in items if item.get("status") == "FAILED"]
        return {
            # A partial batch is a real outcome, not a success: the
            # transaction says so and leaves the failures addressable.
            "verified": not failed,
            "reason": "partial_failure" if failed else None,
            "succeeded": result.get("succeeded", 0),
            "failed": result.get("failed", 0),
            "failed_items": failed[:BATCH_PREVIEW_SAMPLE],
        }

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Only the succeeded items are moved back.

        Retrying the failed ones would be nonsense — they never moved —
        and the plan's rule is that a retry touches failures only.
        """
        items = [
            item for item in (result.get("item_results") or []) if item.get("status") == "SUCCEEDED"
        ]
        if not items:
            return {"compensated": True, "reason": "nothing_to_undo", "moved_back": 0}
        source_id = str(result.get("source_id", ""))
        moved_back = 0
        for item in items:
            try:
                self.filesystem.execute(
                    context.family_id,
                    context.requester,
                    action="filesystem.move",
                    transaction_id=context.transaction.id,
                    payload={
                        "source_id": source_id,
                        "path": item["destination_path"],
                        "destination": item["path"],
                    },
                )
                moved_back += 1
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.warning("AssetBatchMoveHandler could not undo %s: %s", item.get("path"), exc)
        return {
            "compensated": moved_back == len(items),
            "moved_back": moved_back,
            "attempted": len(items),
        }

    def _batch(self, payload: dict[str, Any]) -> tuple[list[str], str]:
        paths = payload.get("paths")
        if not isinstance(paths, list) or not paths:
            raise _bad("paths must be a non-empty list")
        cleaned = [str(path).strip() for path in paths if str(path).strip()]
        if not cleaned:
            raise _bad("paths must be a non-empty list")
        return cleaned, _require_str(payload, "destination")


class AssetMoveToTrashHandler:
    """``asset.move_to_trash`` — soft-delete family photos.

    Never a permanent delete. The filesystem manager already moves the
    file under ``.homemind-trash/<transaction_id>/``; this action just
    routes to it, which is what makes the family recoverable.
    """

    action = "asset.move_to_trash"
    risk = RISK_MEDIUM

    def __init__(self, filesystem: FamilyFilesystemManager) -> None:
        self.filesystem = filesystem

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        _require_str(payload, "path")
        _require_str(payload, "source_id")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_id": _require_str(payload, "source_id"),
            "path": _require_str(payload, "path"),
            "recoverable": True,
            "trash_location": ".homemind-trash",
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        source_id = _require_str(payload, "source_id")
        path = _require_str(payload, "path")
        outcome = self.filesystem.execute(
            context.family_id,
            context.requester,
            action="filesystem.delete",
            transaction_id=context.transaction.id,
            payload={"source_id": source_id, "path": path},
        )
        return {
            "source_id": source_id,
            "path": path,
            "verified": getattr(outcome, "verified", False),
        }

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        source_id = str(result.get("source_id", ""))
        path = str(result.get("path", ""))
        try:
            entries = self.filesystem.list(
                context.family_id, context.requester, source_id=source_id, path=path
            )
        except Exception as exc:  # noqa: BLE001 — a vanished source is a verify failure
            return {"verified": False, "reason": "source_unreadable", "error": str(exc)[:200]}
        still_there = any(entry.path == path for entry in entries)
        return {"verified": not still_there, "path": path, "recoverable": True}

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Move the file back out of the trash.

        The recovery path is deterministic (the transaction id names the
        trash folder), which is what makes a soft delete safe to expose
        as an approvable action at all.
        """
        source_id = str(result.get("source_id", ""))
        path = str(result.get("path", ""))
        if not source_id or not path:
            return {"compensated": False, "reason": "missing_identifiers"}
        try:
            self.filesystem.execute(
                context.family_id,
                context.requester,
                action="filesystem.move",
                transaction_id=context.transaction.id,
                payload={
                    "source_id": source_id,
                    "path": f".homemind-trash/{context.transaction.id}/{path}",
                    "destination": path,
                },
            )
        except Exception as exc:  # noqa: BLE001 — best-effort
            return {"compensated": False, "reason": "restore_failed", "error": str(exc)[:300]}
        return {"compensated": True, "path": path}


__all__ = [
    "BATCH_PREVIEW_SAMPLE",
    "DEVICE_RESULT_TIMEOUT_SECONDS",
    "AssetBatchMoveHandler",
    "AssetMoveToTrashHandler",
    "CalendarCancelEventHandler",
    "CalendarCreateEventHandler",
    "CalendarUpdateEventHandler",
    "DeviceCommandHandler",
    "KnowledgeReindexHandler",
]
