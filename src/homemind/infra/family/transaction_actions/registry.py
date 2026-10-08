"""Registry that maps ``FamilyTransactionRow.action`` to a handler."""

from __future__ import annotations

from homemind.infra.db.repos.family_albums import FamilyAlbumRepo
from homemind.infra.family.calendar import FamilyCalendarManager
from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.knowledge import KnowledgeManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.events import EventCreateHandler
from homemind.infra.family.transaction_actions.filesystem import (
    FilesystemCopyHandler,
    FilesystemDeleteHandler,
    FilesystemMoveHandler,
    FilesystemRenameHandler,
)
from homemind.infra.family.transaction_actions.highrisk import (
    AssetBatchMoveHandler,
    AssetMoveToTrashHandler,
    CalendarCancelEventHandler,
    CalendarCreateEventHandler,
    CalendarUpdateEventHandler,
    DeviceCommandHandler,
    KnowledgeReindexHandler,
)
from homemind.infra.family.transaction_actions.lowrisk import (
    AlbumAssetHandler,
    AlbumCreateHandler,
    EventDeleteHandler,
    EventUpdateHandler,
    MemoryDeprecateHandler,
    MemoryUpdateHandler,
    TaskCancelHandler,
    TaskUpdateHandler,
)
from homemind.infra.family.transaction_actions.memories import (
    MemoryCreateHandler,
)
from homemind.infra.family.transaction_actions.smart_device import (
    SmartDeviceCommandHandler,
)
from homemind.infra.family.transaction_actions.tasks import TaskCreateHandler


class FamilyActionRegistry:
    """Resolve an action name to its handler instance."""

    def __init__(self) -> None:
        self._handlers: dict[str, object] = {}

    def register(self, action: str, handler: object) -> None:
        self._handlers[action] = handler

    def get(self, action: str) -> object | None:
        return self._handlers.get(action)

    def actions(self) -> list[str]:
        return sorted(self._handlers.keys())


def build_default_action_registry(
    *,
    family: FamilyManager | None = None,
    context: FamilyContextManager | None = None,
    tasks: FamilyTaskManager | None = None,
    filesystem: FamilyFilesystemManager | None = None,
    albums: FamilyAlbumRepo | None = None,
    calendars: FamilyCalendarManager | None = None,
    knowledge: KnowledgeManager | None = None,
    devices: DeviceRuntimeManager | None = None,
    smart_home: FamilySmartHomeManager | None = None,
) -> FamilyActionRegistry:
    """Wire every built-in handler. Tests can override any argument to
    inject fakes for specific actions."""
    registry = FamilyActionRegistry()
    if tasks is not None:
        registry.register("task.create", TaskCreateHandler(family, tasks))
        registry.register("task.update", TaskUpdateHandler(tasks))
        registry.register("task.cancel", TaskCancelHandler(tasks))
    if context is not None:
        registry.register("event.create", EventCreateHandler(context))
        registry.register("event.update", EventUpdateHandler(context))
        registry.register("event.delete", EventDeleteHandler(context))
        registry.register("memory.create", MemoryCreateHandler(context))
        registry.register("memory.update", MemoryUpdateHandler(context))
        registry.register("memory.deprecate", MemoryDeprecateHandler(context))
    if filesystem is not None:
        registry.register("filesystem.copy", FilesystemCopyHandler(filesystem))
        registry.register("filesystem.move", FilesystemMoveHandler(filesystem))
        registry.register("filesystem.rename", FilesystemRenameHandler(filesystem))
        registry.register("filesystem.delete", FilesystemDeleteHandler(filesystem))
        registry.register("asset.batch_move", AssetBatchMoveHandler(filesystem))
        registry.register("asset.move_to_trash", AssetMoveToTrashHandler(filesystem))
    if albums is not None:
        registry.register("album.create", AlbumCreateHandler(albums))
        registry.register("album.add_asset", AlbumAssetHandler(albums, add=True))
        registry.register("album.remove_asset", AlbumAssetHandler(albums, add=False))
    if calendars is not None:
        registry.register("calendar.create_event", CalendarCreateEventHandler(calendars))
        registry.register("calendar.update_event", CalendarUpdateEventHandler(calendars))
        registry.register("calendar.cancel_event", CalendarCancelEventHandler(calendars))
    if knowledge is not None:
        registry.register("knowledge.reindex", KnowledgeReindexHandler(knowledge))
    if devices is not None:
        registry.register("device.command", DeviceCommandHandler(devices))
    if smart_home is not None:
        # Server-side smart-home write. Same Transaction pipeline, different
        # execution target from the paired-device ``device.command`` above.
        registry.register("smart_device.command", SmartDeviceCommandHandler(smart_home))
    return registry


__all__ = ["FamilyActionRegistry", "build_default_action_registry"]
