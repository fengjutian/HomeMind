"""``smart_device.command`` — a reviewed command to a smart-home entity.

Plan phase 5 requires that no device write bypasses the Family Transaction
pipeline (Permission → Approval → Execute → Verify → Audit), and that the
existing ``device.command`` handler is *not* extended to cover this.

The two are genuinely different targets:

* ``device.command`` queues a command for a **paired device** (a phone or
  computer the family already runs) and parks the transaction at
  ``AWAITING_DEVICE`` until that device reports a result.
* A smart-home entity is executed **server-side** through the adapter, so the
  effect can be verified immediately rather than awaited.

Reusing the paired-device handler would put a Home Assistant light on a
device queue that nothing is polling. This is a separate action over the same
pipeline instead.
"""

from __future__ import annotations

import logging
from typing import Any

from homemind.infra.db.repos.smart_home import SmartProviderRow
from homemind.infra.family.smart_home import RISK_BLOCKED, risk_for
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from homemind.infra.family.transaction_actions.base import ActionContext
from homemind.infra.family.transaction_actions.lowrisk import (
    _bad,
    _not_found,
    _require_str,
)

logger = logging.getLogger(__name__)


class SmartDeviceCommandHandler:
    """Execute a catalogued command against a smart-home entity."""

    action = "smart_device.command"
    #: Per-domain risk is computed during validate; this is the ceiling.
    risk = "HIGH"
    #: Server-side execution, so verification can happen right away.
    awaits_device_result = False

    def __init__(self, smart_home: FamilySmartHomeManager) -> None:
        self.smart_home = smart_home

    def validate(self, context: ActionContext, payload: dict[str, Any]) -> None:
        entity_id = _require_str(payload, "entity_id")
        command_name = _require_str(payload, "command")

        entity = self._entity(entity_id, context.family_id)

        # Risk comes from the domain, never from the payload.
        if risk_for(entity.domain) == RISK_BLOCKED:
            raise _bad(
                f"{entity.domain} is not writable by the assistant; "
                "this device is read-only in this release"
            )

        provider = self._provider_for(entity)
        if provider.family_id != context.family_id:
            raise _not_found("smart provider not found")
        if not provider.enabled:
            raise _bad("smart provider is disabled")

        catalog = self.smart_home.command_catalog(provider)
        names = {command.name for command in catalog if command.domain == entity.domain}
        if command_name not in names:
            available = ", ".join(sorted(names)) or "none"
            raise _bad(
                f"{command_name!r} is not a documented command for {entity.domain}; "
                f"available: {available}"
            )

        args = payload.get("payload")
        if args is not None and not isinstance(args, dict):
            raise _bad("command payload must be an object")

    def preview(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        entity_id = _require_str(payload, "entity_id")
        entity = self.smart_home.repo.get_entity(entity_id)
        return {
            "entity_id": entity_id,
            "entity_name": getattr(entity, "name", ""),
            "domain": getattr(entity, "domain", ""),
            "command": _require_str(payload, "command"),
            "payload": dict(payload.get("payload") or {}),
            "risk": risk_for(getattr(entity, "domain", "")),
        }

    def execute(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        entity_id = _require_str(payload, "entity_id")
        entity = self._entity(entity_id, context.family_id)
        provider = self._provider_for(entity)
        command = _require_str(payload, "command")
        args = dict(payload.get("payload") or {})
        idempotency_key = context.transaction.id

        result = self.smart_home.execute_command(
            provider,
            entity,
            command,
            args,
            idempotency_key=idempotency_key,
        )
        return {
            "entity_id": entity_id,
            "command": command,
            "result": result,
        }

    def verify(self, context: ActionContext, payload: dict[str, Any]) -> dict[str, Any]:
        """Re-read the entity and confirm the effect actually landed.

        A command that reported success but left the device unchanged is a
        failure, and the transaction has to say so.
        """
        entity_id = _require_str(payload, "entity_id")
        command = _require_str(payload, "command")
        entity = self._entity(entity_id, context.family_id)
        provider = self._provider_for(entity)

        confirmation = self.smart_home.verify_command(
            provider,
            entity,
            command,
            dict(payload.get("payload") or {}),
        )
        return {"entity_id": entity_id, "command": command, "confirmed": confirmation}

    def _entity(self, entity_id: str, family_id: str) -> Any:
        """Fetch the entity, refusing cross-family access."""
        entity = self.smart_home.repo.get_entity(entity_id)
        if entity is None or entity.family_id != family_id:
            raise _not_found("smart entity not found")
        return entity

    def _provider_for(self, entity: Any) -> SmartProviderRow:
        provider = self.smart_home.repo.get_provider(entity.provider_id)
        if provider is None:
            raise _not_found("smart provider not found")
        return provider


__all__ = ["SmartDeviceCommandHandler"]
