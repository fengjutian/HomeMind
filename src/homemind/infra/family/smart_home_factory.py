"""Smart-home adapter assembly (plan phase 5, "make the link reachable").

The manager could read providers and entities for a long time while never
constructing an adapter: ``adapter_factory`` stayed ``None``, so every
``_adapter()`` returned ``None`` and sync recorded ``adapterUnavailable``.
Two things were missing to close the loop:

* **Credential resolution.** A provider stores a *reference* to a secret, by
  design. Something has to turn that reference into a token at call time —
  without the resolved value ever landing in a row, a log line, or a response.
* **Construction.** A provider row plus a token becomes a live adapter.

This module is that bridge. It is deliberately the only place in the smart-home
stack that touches the secret store.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

from homemind.infra.family.smart_home import SmartHomeAdapter
from homemind.infra.family.smart_home_adapters import (
    HomeAssistantAdapter,
    MqttSmartHomeAdapter,
)
from homemind.infra.db.repos.smart_home import (
    PROVIDER_KIND_HOME_ASSISTANT,
    PROVIDER_KIND_MQTT,
    SmartProviderRow,
)

logger = logging.getLogger(__name__)

#: Provider kinds this factory knows how to build. A new kind is refused here
#: rather than silently producing a provider that cannot sync.
BUILDABLE_KINDS: frozenset[str] = frozenset(
    {PROVIDER_KIND_HOME_ASSISTANT, PROVIDER_KIND_MQTT}
)


class SecretStore(Protocol):
    """The slice of the secret store this module needs."""

    def get(self, k: str) -> bytes | None: ...


class MqttPublisher(Protocol):
    """What an MQTT adapter needs from a broker connection."""

    async def publish(self, topic: str, payload: bytes) -> None: ...


def resolve_secret(store: SecretStore, secret_ref: str | None) -> str | None:
    """Turn a stored reference into the credential text.

    Returns ``None`` when no reference is set. Raises when a reference *is*
    set but cannot be resolved — a provider that points at a deleted secret
    must fail loudly rather than sync with an empty token.
    """
    if not secret_ref:
        return None
    raw = store.get(secret_ref)
    if raw is None:
        raise LookupError(f"secret {secret_ref!r} is referenced but not stored")
    return raw.decode("utf-8", errors="replace")


def build_adapter(
    provider: SmartProviderRow,
    store: SecretStore,
    *,
    mqtt_publisher: Callable[[SmartProviderRow], MqttPublisher] | None = None,
) -> SmartHomeAdapter | None:
    """Construct the adapter for *provider*, or ``None`` when it cannot run.

    Returning ``None`` is the normal "not usable right now" answer: disabled
    provider, unknown kind, missing base URL. It is *not* used to hide a
    credential problem — that raises.
    """
    if not provider.enabled:
        return None
    if provider.kind not in BUILDABLE_KINDS:
        logger.warning("no adapter builder for provider kind %r", provider.kind)
        return None

    if provider.kind == PROVIDER_KIND_HOME_ASSISTANT:
        if not provider.base_url:
            return None
        # Resolved only here: an MQTT provider gets its broker credentials from
        # the injected publisher, so demanding a stored secret for it would
        # fail a provider that is perfectly usable.
        token = resolve_secret(store, provider.secret_ref)
        if not token:
            logger.warning(
                "home assistant provider %s has no credential reference", provider.id
            )
            return None
        return HomeAssistantAdapter(base_url=provider.base_url, token=token)

    # MQTT: the broker connection is owned by the deployment, so the
    # publisher is injected rather than built here. The family owns the
    # topic allow-list, which is the only thing that makes an MQTT write
    # possible at all.
    if mqtt_publisher is None or not provider.topic_allowlist:
        return None
    return MqttSmartHomeAdapter(
        publisher=mqtt_publisher(provider),
        topic_allowlist=list(provider.topic_allowlist),
    )


__all__ = [
    "BUILDABLE_KINDS",
    "MqttPublisher",
    "SecretStore",
    "build_adapter",
    "resolve_secret",
]