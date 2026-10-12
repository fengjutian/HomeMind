"""Process-level lifecycle for Home Assistant state subscriptions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from homemind.infra.db.repos.smart_home import (
    PROVIDER_KIND_HOME_ASSISTANT,
    SmartHomeRepo,
    SmartProviderRow,
)
from homemind.infra.family.smart_home_adapters import HomeAssistantAdapter
from homemind.infra.family.smart_home_descriptors import describe_entity
from homemind.infra.family.smart_home_factory import SecretStore, resolve_secret
from homemind.infra.family.smart_home_subscription import (
    HomeAssistantSubscriber,
    StateChange,
    StreamHandle,
    SubscriptionState,
    connect_home_assistant,
)

logger = logging.getLogger(__name__)

Connect = Callable[[str, str], Awaitable[StreamHandle]]


class SmartHomeSubscriptionManager:
    """Start and stop one live subscription for each enabled HA provider."""

    def __init__(
        self,
        repo: SmartHomeRepo,
        secrets: SecretStore,
        *,
        connect: Connect = connect_home_assistant,
    ) -> None:
        self._repo = repo
        self._secrets = secrets
        self._connect = connect
        self._subscribers: dict[str, HomeAssistantSubscriber] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def start(self) -> None:
        for provider in self._repo.list_enabled_providers():
            try:
                await self.start_provider(provider)
            except Exception:  # noqa: BLE001 - one bad provider must not block boot
                logger.exception("Could not start smart-home provider %s", provider.id)

    async def start_provider(self, provider: SmartProviderRow) -> None:
        if provider.kind != PROVIDER_KIND_HOME_ASSISTANT or not provider.enabled:
            return
        await self.stop_provider(provider.id)
        if not provider.base_url:
            return
        try:
            token = resolve_secret(self._secrets, provider.secret_ref)
        except LookupError:
            self._repo.record_sync_result(
                provider.id,
                ok=False,
                error="credential unavailable",
                error_code="CREDENTIAL_UNAVAILABLE",
                reauth_required=True,
            )
            logger.warning("Smart-home provider %s has no stored credential", provider.id)
            return
        if not token:
            return
        adapter = HomeAssistantAdapter(base_url=provider.base_url, token=token)
        subscriber = HomeAssistantSubscriber(
            connect=lambda: self._connect(provider.base_url or "", token),
            reconcile=lambda: self._reconcile(provider, adapter),
            on_change=lambda change: self._apply(provider, change),
            on_status=lambda status, reconnects, error: self._status(
                provider.id,
                status,
                provider.subscription_reconnect_count + reconnects,
                error,
            ),
        )
        task = asyncio.create_task(
            subscriber.run(),
            name=f"homemind-ha-{provider.id}",
        )
        task.add_done_callback(self._consume_task_result)
        self._subscribers[provider.id] = subscriber
        self._tasks[provider.id] = task

    async def stop_provider(self, provider_id: str) -> None:
        subscriber = self._subscribers.pop(provider_id, None)
        task = self._tasks.pop(provider_id, None)
        if subscriber is not None:
            await subscriber.stop()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._repo.record_subscription_status(provider_id, SubscriptionState.STOPPED)

    async def stop(self) -> None:
        for provider_id in list(self._tasks):
            await self.stop_provider(provider_id)

    async def _reconcile(
        self,
        provider: SmartProviderRow,
        adapter: HomeAssistantAdapter,
    ) -> int:
        entities = await adapter.list_entities()
        for entity in entities:
            descriptor = describe_entity(
                entity,
                provider_id=provider.id,
                adapter_kind=provider.kind,
            )
            self._repo.upsert_entity(
                provider.family_id,
                provider_id=provider.id,
                external_entity_id=entity.external_id,
                domain=entity.domain,
                name=entity.name,
                capabilities=[command.name for command in adapter.commands_for(entity.domain)],
                state={"state": entity.state, "attributes": entity.attributes},
                device_key=descriptor.device_id,
                capabilities_typed=[capability.value for capability in descriptor.capabilities],
            )
        if entities:
            self._repo.prune_missing_entities(
                provider.family_id,
                provider.id,
                [entity.external_id for entity in entities],
            )
        self._repo.record_sync_result(provider.id, ok=True)
        return len(entities)

    async def _apply(self, provider: SmartProviderRow, change: StateChange) -> None:
        entity = self._repo.get_entity_by_external(provider.id, change.entity_id)
        if entity is None:
            # A newly added HA entity needs its descriptor and capabilities;
            # the next reconnect or explicit sync will import it completely.
            return
        self._repo.set_entity_state(
            entity.id,
            {"state": change.state, "attributes": change.attributes},
            changed_at=_timestamp(change.last_changed),
        )
        self._repo.record_subscription_status(
            provider.id,
            SubscriptionState.SUBSCRIBED,
            event_at=_timestamp(change.last_updated) or int(datetime.now().timestamp()),
        )

    async def _status(
        self,
        provider_id: str,
        status: str,
        reconnects: int,
        error_code: str | None,
    ) -> None:
        self._repo.record_subscription_status(
            provider_id,
            status,
            reconnect_count=reconnects,
            error_code=error_code,
        )

    @staticmethod
    def _consume_task_result(task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        try:
            task.result()
        except Exception:  # noqa: BLE001
            logger.exception("Home Assistant subscription task stopped unexpectedly")


def _timestamp(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


__all__ = ["SmartHomeSubscriptionManager"]
