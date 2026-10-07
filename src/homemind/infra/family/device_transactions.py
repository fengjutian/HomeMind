"""Wiring that connects a device result back to its transaction (Stage 6).

This module exists so ``HomeMindServer`` does not have to know how a
parked transaction is resumed. It is one small factory, and the rules it
encodes are the whole point of the ``AWAITING_DEVICE`` state:

* **The device's report is not the verification.** A device answering
  ``SUCCEEDED`` means *the command ran*, not that the world ended up in
  the state the handler expected. The handler still gets to say, and a
  disagreement goes to review rather than being reported as success.
* **Any doubt is review, never success.** A missing handler, a vanished
  command, a verify that raised -- all of them mean "we do not know",
  and "we do not know" must not be written down as "it worked".
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from homemind.infra.db.repos.family_devices import FamilyDeviceRepo
from homemind.infra.family.transaction_actions.base import ActionContext
from octop.infra.users.identity import Role, User

logger = logging.getLogger(__name__)


def _system_user(user_id: int) -> User:
    """A stand-in identity for a verification that has no session.

    A device reported minutes or hours after the person who approved it
    closed the tab. Permission was already settled at approval time;
    re-deriving it here would need a live session nobody has.
    """
    return User(user_id, f"user-{user_id}", Role.USER, "system")


def resume_device_transaction(
    transactions: Any,
    device_repo: FamilyDeviceRepo,
) -> Callable[[str, str, dict[str, Any]], None]:
    """Build the ``on_command_result`` callback for ``DeviceRuntimeManager``."""

    def resume(command_id: str, status: str, result: dict[str, Any]) -> None:
        try:
            _resume_once(transactions, device_repo, command_id, result)
        except Exception:  # noqa: BLE001 — never lose the device's own report
            # The command row is already terminal and the periodic sweep
            # will escalate the transaction. Letting an exception escape
            # here would turn a bookkeeping problem into a failed HTTP
            # response to a device that did its job correctly.
            logger.exception("DeviceRuntime: could not resume transaction for %s", command_id)

    return resume


def _resume_once(
    transactions: Any,
    device_repo: FamilyDeviceRepo,
    command_id: str,
    result: dict[str, Any],
) -> None:
    transaction = transactions.repo.get_by_device_command(command_id)
    if transaction is None:
        # A command queued without a transaction, or one already closed.
        # Nothing to do; the device's report is recorded regardless.
        return

    handler = transactions.registry.get(transaction.action)
    verification: dict[str, Any]
    if handler is None:
        verification = {"verified": False, "reason": "handler_unavailable"}
    else:
        command = device_repo.get_command(command_id)
        if command is None:
            verification = {"verified": False, "reason": "command_not_found"}
        else:
            verification = _run_verify(handler, transaction, command_id, result)

    transactions.resume_after_device_result(
        command_id,
        verification=verification,
        verified=bool(verification.get("verified")),
    )


def _run_verify(
    handler: Any,
    transaction: Any,
    command_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    verify = getattr(handler, "verify", None)
    if not callable(verify):
        return {"verified": False, "reason": "handler_has_no_verify"}
    context = ActionContext(
        family_id=transaction.family_id,
        transaction=transaction,
        requester=_system_user(transaction.requested_by),
        payload=json.loads(transaction.payload_json),
    )
    try:
        outcome = verify(context, command_id, dict(result))
    except Exception as exc:  # noqa: BLE001 — a verify crash is not a pass
        return {"verified": False, "error": f"{type(exc).__name__}: {exc}"[:200]}
    if isinstance(outcome, dict):
        return outcome
    return {"verified": bool(outcome)}


__all__ = ["resume_device_transaction"]
