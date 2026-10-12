"""Process-level Home Assistant subscription wiring."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

from homemind.infra.db.repos.smart_home import SmartEntityRow, SmartProviderRow
from homemind.infra.family.smart_home_runtime import SmartHomeSubscriptionManager
from homemind.infra.family.smart_home_subscription import StreamHandle


def _provider(**values: Any) -> SmartProviderRow:
    provider = SmartProviderRow(
        id="provider-1",
        pk=1,
        family_id="family-1",
        kind="HOME_ASSISTANT",
        name="Home Assistant",
        base_url="http://ha.local:8123",
        secret_ref="ha-token",
        topic_allowlist=[],
        enabled=True,
        last_seen_at=None,
        last_error=None,
        last_sync_at=None,
        last_success_at=None,
        last_error_code=None,
        reauth_required=False,
        rate_limited_until=None,
        created_by=1,
        created_at=1,
        updated_at=1,
    )
    return replace(provider, **values)


class _Secrets:
    def __init__(self, value: bytes | None = b"token") -> None:
        self.value = value

    def get(self, _key: str) -> bytes | None:
        return self.value


class _Repo:
    def __init__(self, providers: list[SmartProviderRow]) -> None:
        self.providers = providers
        self.sync_results: list[dict[str, Any]] = []
        self.state_updates: list[tuple[str, dict[str, Any], int | None]] = []
        self.entity = cast(SmartEntityRow, type("Entity", (), {"id": "entity-1"})())

    def list_enabled_providers(self) -> list[SmartProviderRow]:
        return self.providers

    def record_sync_result(self, provider_id: str, **values: Any) -> None:
        self.sync_results.append({"provider_id": provider_id, **values})

    def get_entity_by_external(
        self, provider_id: str, external_entity_id: str
    ) -> SmartEntityRow | None:
        if (provider_id, external_entity_id) == ("provider-1", "light.living_room"):
            return self.entity
        return None

    def set_entity_state(
        self, entity_id: str, state: dict[str, Any], *, changed_at: int | None = None
    ) -> None:
        self.state_updates.append((entity_id, state, changed_at))


class _Socket:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self.frames: asyncio.Queue[Any] = asyncio.Queue()

    async def receive(self) -> Any:
        return await self.frames.get()

    async def send(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self) -> None:
        self.closed = True

    def handle(self) -> StreamHandle:
        return StreamHandle(receive=self.receive, send=self.send, close=self.close)


async def test_manager_applies_live_state_and_stops() -> None:
    repo = _Repo([_provider()])
    socket = _Socket()

    async def connect(_base_url: str, _token: str) -> StreamHandle:
        return socket.handle()

    manager = SmartHomeSubscriptionManager(cast(Any, repo), _Secrets(), connect=connect)
    manager._reconcile = _no_reconcile  # type: ignore[method-assign]
    await manager.start()
    await socket.frames.put(
        {
            "type": "event",
            "event": {
                "event_type": "state_changed",
                "data": {
                    "entity_id": "light.living_room",
                    "new_state": {
                        "state": "on",
                        "attributes": {"brightness": 120},
                        "last_changed": "2026-10-12T08:00:00Z",
                    },
                },
            },
        }
    )
    for _ in range(20):
        if repo.state_updates:
            break
        await asyncio.sleep(0)

    await manager.stop()

    assert socket.sent == [{"id": 1, "type": "subscribe_events", "event_type": "state_changed"}]
    assert repo.state_updates == [
        (
            "entity-1",
            {"state": "on", "attributes": {"brightness": 120}},
            1791792000,
        )
    ]
    assert socket.closed is True


async def test_manager_marks_a_dangling_secret_for_reauthentication() -> None:
    repo = _Repo([])
    manager = SmartHomeSubscriptionManager(cast(Any, repo), _Secrets(None))

    await manager.start_provider(_provider())

    assert repo.sync_results == [
        {
            "provider_id": "provider-1",
            "ok": False,
            "error": "credential unavailable",
            "error_code": "CREDENTIAL_UNAVAILABLE",
            "reauth_required": True,
        }
    ]


async def _no_reconcile(_provider: SmartProviderRow, _adapter: Any) -> int:
    return 0
