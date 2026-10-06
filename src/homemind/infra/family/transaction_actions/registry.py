"""Registry that maps ``FamilyTransactionRow.action`` to a handler."""

from __future__ import annotations

from homemind.infra.family.context import FamilyContextManager
from homemind.infra.family.filesystem import FamilyFilesystemManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.tasks import FamilyTaskManager
from homemind.infra.family.transaction_actions.events import EventCreateHandler
from homemind.infra.family.transaction_actions.filesystem import (
    FilesystemCopyHandler,
    FilesystemDeleteHandler,
    FilesystemMoveHandler,
    FilesystemRenameHandler,
)
from homemind.infra.family.transaction_actions.memories import (
    MemoryCreateHandler,
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
) -> FamilyActionRegistry:
    """Wire every built-in handler. Tests can override any argument to
    inject fakes for specific actions."""
    registry = FamilyActionRegistry()
    if tasks is not None:
        registry.register("task.create", TaskCreateHandler(family, tasks))
    if context is not None:
        registry.register("event.create", EventCreateHandler(context))
        registry.register("memory.create", MemoryCreateHandler(context))
    if filesystem is not None:
        registry.register("filesystem.copy", FilesystemCopyHandler(filesystem))
        registry.register("filesystem.move", FilesystemMoveHandler(filesystem))
        registry.register("filesystem.rename", FilesystemRenameHandler(filesystem))
        registry.register("filesystem.delete", FilesystemDeleteHandler(filesystem))
    return registry


__all__ = ["FamilyActionRegistry", "build_default_action_registry"]