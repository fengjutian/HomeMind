"""The agent-facing device catalogue must not leak, and must be honest
about what the assistant can and cannot control (plan phase 5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations as run_octop_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home import SmartCommand
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_octop_migrations(pool)
    run_homemind_migrations(pool)
    return pool


@pytest.fixture
def env(db: SqlitePool) -> dict[str, Any]:
    services = HomeMindServices.from_pool(db)
    families = FamilyManager(services.family_repo)
    user_row = OctopUserRepo(db).create(username="owner", password_hash="h", role="user")
    owner = User(user_row, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Smart Family", timezone="Asia/Shanghai", locale="zh"
    )
    return {
        "families": families,
        "owner": owner,
        "family": family,
        "manager": FamilySmartHomeManager(
            FamilyManager(services.family_repo), services.smart_home_repo
        ),
    }


@pytest.fixture
def manager(env: dict[str, Any]) -> FamilySmartHomeManager:
    manager = env["manager"]
    manager.create_provider(
        env["family"].id,
        env["owner"],
        kind="HOME_ASSISTANT",
        name="Home",
        base_url="http://ha.local:8123",
    )
    return manager


def _family_id(manager: FamilySmartHomeManager) -> str:
    return manager.family.family_id


def _catalog(manager: FamilySmartHomeManager) -> list[SmartCommand]:
    return [
        SmartCommand(name="turn_on", domain="light", service="light.turn_on"),
        SmartCommand(name="turn_off", domain="light", service="light.turn_off"),
    ]


def _seed(
    manager: FamilySmartHomeManager,
    *,
    domain: str,
    external: str,
    device_key: str | None,
    typed: list[str],
) -> None:
    manager.repo.upsert_entity(
        _fid(manager),
        provider_id="prov1",
        external_entity_id=external,
        domain=domain,
        name=f"Device {external}",
        state={"state": "on"},
        device_key=device_key,
        capabilities_typed=typed,
    )


def test_read_only_device_is_listed_without_commands(manager: FamilySmartHomeManager) -> None:
    _seed(manager, domain="lock", external="lock.front", device_key="d1", typed=["lock"])
    manager._adapter_factory = lambda _p: _StubAdapter(_catalog(manager))  # type: ignore[attr-defined]

    entries = manager.device_catalog(_fid(manager), "prov1")
    assert len(entries) == 1
    entry = entries[0]
    assert entry["writable"] is False
    assert entry["commands"] == [], "a blocked device must expose no commands"
    assert entry["risk"] == "BLOCKED"
    assert entry["capabilities"] == ["lock"]


def test_writable_device_exposes_its_catalog(manager: FamilySmartHomeManager) -> None:
    _seed(manager, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    manager._adapter_factory = lambda _p: _StubAdapter(_catalog(manager))  # type: ignore[attr-defined]

    entry = manager.device_catalog(_fid(manager), "prov1")[0]
    assert entry["writable"] is True
    assert entry["risk"] == "LOW"
    assert {c["name"] for c in entry["commands"]} == {"turn_on", "turn_off"}


def test_entities_of_one_device_collapse_into_one_entry(
    manager: FamilySmartHomeManager,
) -> None:
    _seed(manager, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    _seed(
        manager,
        domain="sensor",
        external="sensor.lamp_power",
        device_key="d1",
        typed=["sensor"],
    )
    manager._adapter_factory = lambda _p: _StubAdapter([])  # type: ignore[attr-defined]

    entries = manager.device_catalog(_fid(manager), "prov1")
    assert len(entries) == 1
    assert set(entries[0]["entity_ids"]) == {"light.lamp", "sensor.lamp_power"}


def test_catalog_never_contains_adapter_raw_payload(
    manager: FamilySmartHomeManager,
) -> None:
    _seed(manager, domain="light", external="light.lamp", device_key="d1", typed=["light"])
    manager._adapter_factory = lambda _p: _StubAdapter(_catalog(manager))  # type: ignore[attr-defined]

    blob = json.dumps(manager.device_catalog(_fid(manager), "prov1"))
    assert "friendly_name" not in blob
    assert "raw" not in blob
    assert "unique_id" not in blob


def test_unknown_stored_capability_is_skipped(manager: FamilySmartHomeManager) -> None:
    """A capability from a future/older release must not crash the catalogue."""
    _seed(
        manager,
        domain="light",
        external="light.lamp",
        device_key="d1",
        typed=["light", "holographic"],
    )
    manager._adapter_factory = lambda _p: _StubAdapter([])  # type: ignore[attr-defined]

    entry = manager.device_catalog(_fid(manager), "prov1")[0]
    assert entry["capabilities"] == ["light"]


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
