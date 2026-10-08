"""The agent-facing device catalogue must not leak, and must be honest
about what the assistant can and cannot control (plan phase 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home import SmartCommand
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from octop.infra.db.migrate import run_migrations as run_octop_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo as OctopUserRepo
from octop.infra.users.identity import Role, User


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_octop_migrations(pool)
    run_homemind_migrations(pool)
    return pool


@pytest.fixture
def env(db: SqlitePool) -> dict[str, Any]:
    services = HomeMindServices.from_pool(db)
    user_row = OctopUserRepo(db).create(username="owner", password_hash="h", role="user")
    owner = User(user_row, "owner", Role.USER, "Owner")
    family = FamilyManager(services.family_repo).create_family(
        owner, name="Smart Family", timezone="Asia/Shanghai", locale="zh"
    )
    manager = FamilySmartHomeManager(FamilyManager(services.family_repo), services.smart_home_repo)
    provider = manager.create_provider(
        family.id,
        owner,
        kind="HOME_ASSISTANT",
        name="Home",
        base_url="http://ha.local:8123",
    )
    return {
        "owner": owner,
        "family_id": family.id,
        "manager": manager,
        "provider_id": provider.id,
    }


def _seed(
    env: dict[str, Any],
    *,
    domain: str,
    external: str,
    device_key: str | None,
    typed: list[str],
) -> None:
    env["manager"].repo.upsert_entity(
        env["family_id"],
        provider_id=env["provider_id"],
        external_entity_id=external,
        domain=domain,
        name=f"Device {external}",
        state={"state": "on"},
        device_key=device_key,
        capabilities_typed=typed,
    )


def _use(manager: FamilySmartHomeManager, commands: list[SmartCommand]) -> None:
    manager._adapter_factory = lambda _p: _StubAdapter(commands)  # type: ignore[attr-defined]


_LIGHT_COMMANDS = [
    SmartCommand(name="turn_on", domain="light", service="light.turn_on"),
    SmartCommand(name="turn_off", domain="light", service="light.turn_off"),
]


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
