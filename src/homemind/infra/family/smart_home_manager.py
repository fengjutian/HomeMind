"""Smart-home domain service (Stage 6).

Owns the mapping between HomeMind's entity rows and whatever system
actually runs the house, plus the rules that decide whether a write may
happen at all.

**Nothing here executes a command.** A smart-home write is a Family
Transaction (``device.command`` with a smart-home payload), which is
what puts it behind approval and gives it a verify step. This module
supplies the catalogue, the risk classification, and the preview — the
three things a transaction needs to decide whether it *may* run, and
what the approver is being asked to agree to.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from homemind.infra.db.repos.smart_home import (
    PROVIDER_KIND_HOME_ASSISTANT,
    PROVIDER_KIND_MQTT,
    PROVIDER_KINDS,
    SmartEntityRow,
    SmartHomeRepo,
    SmartProviderRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home import (
    RISK_BLOCKED,
    RISK_HIGH,
    CommandNotAllowed,
    CommandPreview,
    SmartCommand,
    SmartEntity,
    SmartHomeAdapter,
    risk_for,
)
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


class FamilySmartHomeManager:
    """Providers, entity mappings, and the command catalogue."""

    def __init__(
        self,
        family: FamilyManager,
        repo: SmartHomeRepo,
        *,
        adapter_factory: Callable[[SmartProviderRow], SmartHomeAdapter | None] | None = None,
    ) -> None:
        self.family = family
        self.repo = repo
        # Optional so a deployment without a bridge can still list what
        # it has; writes then refuse rather than pretend.
        self._adapter_factory = adapter_factory

    # ------------------------------------------------------------- providers

    def create_provider(
        self,
        family_id: str,
        user: User,
        *,
        kind: str,
        name: str,
        base_url: str | None = None,
        secret_ref: str | None = None,
        topic_allowlist: list[str] | None = None,
    ) -> SmartProviderRow:
        """Register a connection.

        Manager-only: adding a bridge to the house is a household-level
        decision, not something any member may do.

        ``secret_ref`` is a *reference* to a stored credential. This
        module never receives the secret itself, so it cannot leak one.
        """
        self.family.require_manager(family_id, user)
        if kind not in PROVIDER_KINDS:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "unknown provider kind")
        if kind == PROVIDER_KIND_MQTT and topic_allowlist:
            # An MQTT provider with no allow-list cannot publish at all;
            # say so now rather than at the first command.
            _validate_allowlist_syntax(topic_allowlist)
        return self.repo.create_provider(
            family_id,
            kind=kind,
            name=name.strip(),
            base_url=base_url,
            secret_ref=secret_ref,
            topic_allowlist=topic_allowlist,
            created_by=user.id,
        )

    def list_providers(self, family_id: str, user: User) -> list[SmartProviderRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_providers(family_id)

    def delete_provider(self, family_id: str, provider_id: str, user: User) -> bool:
        self.family.require_manager(family_id, user)
        provider = self._provider(family_id, provider_id)
        self.repo.delete_entities_for_provider(provider.id)
        return self.repo.delete_provider(provider.id)

    def set_topic_allowlist(
        self, family_id: str, provider_id: str, user: User, topics: list[str]
    ) -> SmartProviderRow:
        """Replace the topics a family permits publishing to."""
        self.family.require_manager(family_id, user)
        provider = self._provider(family_id, provider_id)
        _validate_allowlist_syntax(topics)
        updated = self.repo.set_topic_allowlist(provider.id, topics)
        assert updated is not None
        return updated

    def probe(self, family_id: str, provider_id: str, user: User) -> bool:
        """Test a connection and record the outcome.

        The recorded error is the exception *type* only: a connection
        error message can carry a host name, which is not something a
        family status field should be echoing back to a member.
        """
        self.family.require_manager(family_id, user)
        provider = self._provider(family_id, provider_id)
        adapter = self._adapter(provider)
        if adapter is None:
            self.repo.record_probe(provider.id, ok=False, error="adapterUnavailable")
            return False
        import asyncio

        ok = asyncio.run(adapter.probe())
        self.repo.record_probe(provider.id, ok=ok, error=None if ok else "probeFailed")
        return ok

    # -------------------------------------------------------------- entities

    def sync_entities(self, family_id: str, provider_id: str, user: User) -> list[SmartEntityRow]:
        """Read the provider's entities and refresh the family's mapping."""
        self.family.require_manager(family_id, user)
        provider = self._provider(family_id, provider_id)
        adapter = self._adapter(provider)
        if adapter is None:
            return self.repo.list_entities(family_id, provider_id=provider.id)

        import asyncio

        try:
            entities = asyncio.run(adapter.list_entities())
        except Exception as exc:  # noqa: BLE001 — an outage is recorded, not raised
            logger.warning(
                "SmartHome: %s could not list entities: %s", provider.kind, type(exc).__name__
            )
            self.repo.record_probe(provider.id, ok=False, error=type(exc).__name__)
            return self.repo.list_entities(family_id, provider_id=provider.id)

        self.repo.record_probe(provider.id, ok=True)
        rows: list[SmartEntityRow] = []
        for entity in entities:
            rows.append(
                self.repo.upsert_entity(
                    family_id,
                    provider_id=provider.id,
                    external_entity_id=entity.external_id,
                    domain=entity.domain,
                    name=entity.name,
                    capabilities=[command.name for command in adapter.commands_for(entity.domain)],
                    state={"state": entity.state, "attributes": entity.attributes},
                )
            )
        return rows

    def list_entities(
        self, family_id: str, user: User, *, domain: str | None = None
    ) -> list[SmartEntityRow]:
        self.family.require_access(family_id, user)
        return self.repo.list_entities(family_id, domain=domain)

    def get_entity(self, family_id: str, entity_id: str, user: User) -> SmartEntityRow:
        self.family.require_access(family_id, user)
        entity = self.repo.get_entity(entity_id)
        if entity is None or entity.family_id != family_id:
            raise HomeMindError(HomeMindErrorCode.FAMILY_NOT_FOUND, "smart entity not found")
        return entity

    # ------------------------------------------------------------- catalogue

    def commands_for(self, family_id: str, entity_id: str, user: User) -> list[dict[str, Any]]:
        """The documented commands for one entity.

        An agent may only ever name a command from this list. That is
        the mechanism behind "no arbitrary service calls": there is
        nothing else it could name.
        """
        entity = self.get_entity(family_id, entity_id, user)
        provider = self._provider(family_id, entity.provider_id)
        adapter = self._adapter(provider)
        if adapter is None:
            return []
        return [
            {
                "name": command.name,
                "domain": command.domain,
                "description": command.description,
                "risk": risk_for(command.domain),
                "payload_schema": command.payload_schema,
            }
            for command in adapter.commands_for(entity.domain)
        ]

    def preview_command(
        self,
        family_id: str,
        entity_id: str,
        user: User,
        *,
        command_name: str,
        payload: dict[str, Any] | None = None,
    ) -> CommandPreview:
        """Describe what a command would do, without doing it.

        This is what an approval screen renders, so it has to name the
        entity, the command, the payload and the risk — a human
        approving "turn something on somewhere" has agreed to nothing.
        """
        entity = self.get_entity(family_id, entity_id, user)
        risk = risk_for(entity.domain)
        if risk == RISK_BLOCKED:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"{entity.domain} cannot be controlled by an assistant",
            )
        provider = self._provider(family_id, entity.provider_id)
        adapter = self._adapter(provider)
        if adapter is None:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "this provider is not connected")
        import asyncio

        try:
            return asyncio.run(
                adapter.preview_command(
                    entity.external_entity_id, command_name, dict(payload or {})
                )
            )
        except CommandNotAllowed as exc:
            raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, str(exc)) from exc

    def requires_approval(self, entity_id: str, *, family_policy: str = "AUTO") -> bool:
        """Whether a write needs a human before it runs.

        ``HIGH_RISK_DOMAINS`` always do. Lights and switches follow the
        family's own policy, so a household that wants every switch
        approved can have it without this code changing.
        """
        entity = self.repo.get_entity(entity_id)
        if entity is None:
            return True
        risk = risk_for(entity.domain)
        if risk == RISK_BLOCKED:
            return True
        if risk == RISK_HIGH:
            return True
        return family_policy == "APPROVE_ALL"

    # --------------------------------------------------------------- helpers

    def _provider(self, family_id: str, provider_id: str) -> SmartProviderRow:
        provider = self.repo.get_provider(provider_id)
        if provider is None or provider.family_id != family_id:
            raise HomeMindError(HomeMindErrorCode.FAMILY_NOT_FOUND, "smart provider not found")
        return provider

    def _adapter(self, provider: SmartProviderRow) -> SmartHomeAdapter | None:
        if not provider.enabled:
            return None
        if self._adapter_factory is None:
            return None
        return self._adapter_factory(provider)


def _validate_allowlist_syntax(topics: list[str]) -> None:
    """Refuse an allow-list that would permit a wildcard publish."""
    for topic in topics:
        if "#" in topic:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                f"allow-list entry {topic!r} may not contain a wildcard",
            )
        if not topic.strip():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "allow-list entries may not be empty"
            )


__all__ = [
    "PROVIDER_KIND_HOME_ASSISTANT",
    "PROVIDER_KIND_MQTT",
    "FamilySmartHomeManager",
    "SmartCommand",
    "SmartEntity",
]
