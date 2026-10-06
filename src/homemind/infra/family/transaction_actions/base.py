"""Action handler protocol and shared ``ActionContext``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from homemind.infra.db.repos.family_transactions import FamilyTransactionRow
from octop.infra.users.identity import User


@dataclass(frozen=True)
class ActionContext:
    """Inputs every handler needs to drive its lifecycle."""

    family_id: str
    transaction: FamilyTransactionRow
    requester: User
    payload: dict[str, Any]


class FamilyActionHandler(Protocol):
    """Common contract for every family transaction action.

    Implementations expose a single ``action`` attribute and four
    side-effect hooks that the transaction manager drives during the
    ``preview → execute → verify → compensate`` lifecycle. Handlers
    should be stateless — all state they need lives on the
    ``FamilyTransactionRow`` and the surrounding context.
    """

    action: str

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        """Reject malformed payloads before any side effect. Must raise
        ``HomeMappingError`` / ``OctopError`` to abort the transaction."""

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Build a JSON-serializable preview of the upcoming change."""

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply the side effect. Must be idempotent so ``execute`` can
        be retried after recovery."""

    def verify(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Read back the system state and confirm the side effect took."""

    def compensate(self, context: ActionContext, result: dict[str, Any]) -> dict[str, Any]:
        """Best-effort rollback. Should NOT raise; failures are logged
        for human review."""


__all__ = ["ActionContext", "FamilyActionHandler"]