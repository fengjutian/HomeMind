"""Shared smart-home fixtures.

Both the catalogue and the HA-compatibility suites need the same thing: a real
SQLite database, a real family, and a real provider row. Building it in one
place keeps those suites from drifting apart on schema or fixture wiring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from octop.infra.db.migrate import run_migrations as run_octop_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.users import UserRepo as OctopUserRepo
from octop.infra.users.identity import Role, User


@pytest.fixture
def sh_db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_octop_migrations(pool)
    run_homemind_migrations(pool)
    return pool


@pytest.fixture
def env(sh_db: SqlitePool) -> dict[str, Any]:
    services = HomeMindServices.from_pool(sh_db)
    user_row = OctopUserRepo(sh_db).create(username="owner", password_hash="h", role="user")
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
        base_url="http://ha.invalid:8123",
    )
    return {
        "owner": owner,
        "family_id": family.id,
        "manager": manager,
        "provider_id": provider.id,
    }


@pytest.fixture
def manager(env: dict[str, Any]) -> FamilySmartHomeManager:
    return env["manager"]


def seed_entity(
    env: dict[str, Any],
    *,
    domain: str,
    external: str,
    device_key: str | None,
    typed: list[str],
) -> None:
    """Insert one smart entity through the real repo."""
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
