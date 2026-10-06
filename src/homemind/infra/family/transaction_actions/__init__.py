"""Family transaction action handlers (Stage 5).

Each action (``task.create``, ``event.create``, ``memory.create``,
``filesystem.copy`` / ``filesystem.move`` / ``filesystem.rename`` /
``filesystem.delete``) implements the same ``FamilyActionHandler``
protocol so the transaction manager can drive a uniform
``preview → execute → verify → compensate`` lifecycle without growing
an if/elif tree.
"""

from homemind.infra.family.transaction_actions.base import (
    ActionContext,
    FamilyActionHandler,
)
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
from homemind.infra.family.transaction_actions.registry import (
    build_default_action_registry,
    FamilyActionRegistry,
)

_DEFAULT_REGISTRY = build_default_action_registry()


def get_default_registry() -> FamilyActionRegistry:
    """Return the singleton default registry populated with the built-in
    ``task.create`` / ``event.create`` / ``memory.create`` / filesystem
    handlers. Tests can build their own registry via
    ``build_default_action_registry`` when they need to swap one piece.
    """
    return _DEFAULT_REGISTRY


__all__ = [
    "ActionContext",
    "EventCreateHandler",
    "FamilyActionHandler",
    "FamilyActionRegistry",
    "FilesystemCopyHandler",
    "FilesystemDeleteHandler",
    "FilesystemMoveHandler",
    "FilesystemRenameHandler",
    "MemoryCreateHandler",
    "TaskCreateHandler",
    "build_default_action_registry",
    "get_default_registry",
]