"""The agent-facing device catalogue must not leak, and must be honest
about what the assistant can and cannot control (plan phase 5)."""

from __future__ import annotations

import json
from typing import Any

from homemind.infra.family.smart_home import SmartCommand
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from tests.unit.homemind.conftest import seed_entity as _seed

_LIGHT_COMMANDS = [
    SmartCommand(name="turn_on", domain="light", service="light.turn_on"),
    SmartCommand(name="turn_off", domain="light", service="light.turn_off"),
]


def _use(manager: FamilySmartHomeManager, commands: list[SmartCommand]) -> None:
    manager._adapter_factory = lambda _p: _StubAdapter(commands)  # type: ignore[attr-defined]


def test_read_only_device_is_listed_without_commands(env: dict[str, Any]) -> None:
    _seed(env, domain="lock", external="lock.front", device_key="d1", typed=["lock"])
    _use(env["manager"], _LIGHT_COMMANDS)

    entries = env["manager"].device_catalog(env["family_id"], env["provider_id"])
    assert len(entries) == 1
    entry = entries[0]
    assert entry["writable"] is False
    assert entry["commands"] == [], "a blocked device must expose no commands"
    assert entry["risk"] == "BLOCKED"
    assert entry["capabilities"] == ["lock"]


def test_writable_device_exposes_its_catalog(env: dict[str, Any]) -> None:
    _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    _use(env["manager"], _LIGHT_COMMANDS)

    entry = env["manager"].device_catalog(env["family_id"], env["provider_id"])[0]
    assert entry["writable"] is True
    assert entry["reachable"] is True
    assert entry["risk"] == "LOW"
    assert {c["name"] for c in entry["commands"]} == {"turn_on", "turn_off"}


def test_entities_of_one_device_collapse_into_one_entry(env: dict[str, Any]) -> None:
    _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    _seed(env, domain="sensor", external="sensor.lamp_power", device_key="d1", typed=["sensor"])
    _use(env["manager"], [])

    entries = env["manager"].device_catalog(env["family_id"], env["provider_id"])
    assert len(entries) == 1
    assert set(entries[0]["entity_ids"]) == {"light.lamp", "sensor.lamp_power"}


def test_catalog_never_contains_adapter_raw_payload(env: dict[str, Any]) -> None:
    _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    _use(env["manager"], _LIGHT_COMMANDS)

    blob = json.dumps(env["manager"].device_catalog(env["family_id"], env["provider_id"]))
    for leaked in ("friendly_name", "unique_id", '"raw"', "attributes"):
        assert leaked not in blob, leaked


def test_unknown_stored_capability_is_skipped(env: dict[str, Any]) -> None:
    """A capability from a future/older release must not crash the catalogue."""
    _seed(
        env,
        domain="light",
        external="light.lamp",
        device_key="d1",
        typed=["light", "holographic"],
    )
    _use(env["manager"], [])

    entry = env["manager"].device_catalog(env["family_id"], env["provider_id"])[0]
    assert entry["capabilities"] == ["light"]


def test_disabled_provider_is_reachable_false_not_non_writable(
    env: dict[str, Any],
) -> None:
    """A stopped provider must not make a light look permanently unwritable."""
    _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    _use(env["manager"], _LIGHT_COMMANDS)
    env["manager"].repo.update_provider(env["provider_id"], enabled=0)

    entry = env["manager"].device_catalog(env["family_id"], env["provider_id"])[0]
    assert entry["reachable"] is False
    assert entry["writable"] is True, "domain risk is unchanged by a stopped bridge"
    assert entry["commands"] == [], "nothing may be offered while unreachable"


class _StubAdapter:
    kind = "stub"

    def __init__(self, commands: list[SmartCommand]) -> None:
        self.commands = tuple(commands)

    async def probe(self) -> bool:
        return True

    async def list_entities(self) -> list[Any]:
        return []

    def commands_for(self, domain: str) -> list[SmartCommand]:
        return [c for c in self.commands if c.domain == domain]
