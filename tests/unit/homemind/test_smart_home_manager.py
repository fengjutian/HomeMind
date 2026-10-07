"""Smart-home provider and entity mapping (Stage 6)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.smart_home import (
    RISK_BLOCKED,
    RISK_HIGH,
    RISK_LOW,
    SmartEntity,
)
from homemind.infra.family.smart_home_adapters import HomeAssistantAdapter
from homemind.infra.family.smart_home_manager import FamilySmartHomeManager
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


class _StubHa(HomeAssistantAdapter):
    """Home Assistant with the network calls replaced."""

    kind = "home_assistant"

    def __init__(self, entities: list[SmartEntity] | None = None) -> None:
        super().__init__(base_url="http://ha.local", token="t")
        self._entities = entities or []
        self.reachable = True

    async def probe(self) -> bool:
        return self.reachable

    async def list_entities(self) -> list[SmartEntity]:
        if not self.reachable:
            raise ConnectionError("broker down")
        return list(self._entities)

    async def preview_command(self, entity_id: str, command_name: str, payload: dict[str, Any]):
        from homemind.infra.family.smart_home import CommandPreview

        domain = entity_id.partition(".")[0]
        risk = self._check_allowed(domain)
        command = self._resolve_command(domain, command_name)
        return CommandPreview(
            entity_external_id=entity_id,
            command_name=command.name,
            domain=domain,
            risk=risk,
            summary=f"{command.name} -> {entity_id}",
            payload=dict(payload),
        )


@pytest.fixture
def env(tmp_path: Path):  # noqa: ANN201 — small namespace fixture
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        for user_id, name in ((1, "owner"), (2, "spouse")):
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, disabled, locale, "
                "created_at) VALUES (?, ?, 'x', 'user', 0, 'zh', 1)",
                (user_id, name),
            )
    services = HomeMindServices.from_pool(pool)
    families = FamilyManager(services.family_repo)
    owner = User(1, "owner", Role.USER, "Owner")
    family = families.create_family(
        owner, name="Smart Family", timezone="Asia/Shanghai", locale="zh"
    )
    families.create_member(
        family.id, owner, display_name="Spouse", role=MemberRole.MEMBER, user_id=2
    )
    spouse = User(2, "spouse", Role.USER, "Spouse")
    adapter = _StubHa(
        [
            SmartEntity("light.hall", "light", "Hall", state="on"),
            SmartEntity("light.kitchen", "light", "Kitchen", state="off"),
            SmartEntity("sensor.temp", "sensor", "Temperature", state="21"),
        ]
    )
    manager = FamilySmartHomeManager(
        families, services.smart_home_repo, adapter_factory=lambda provider: adapter
    )
    return {
        "services": services,
        "families": families,
        "family": family,
        "owner": owner,
        "spouse": spouse,
        "manager": manager,
        "adapter": adapter,
        "repo": services.smart_home_repo,
    }


# --------------------------------------------------------------- providers


def test_a_provider_stores_a_secret_reference_not_a_secret(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id,
        env["owner"],
        kind="HOME_ASSISTANT",
        name="Home",
        base_url="http://ha.local:8123",
        secret_ref="homemind/ha-token",
    )
    # The reference round-trips; no token was ever accepted.
    assert provider.secret_ref == "homemind/ha-token"
    assert provider.base_url == "http://ha.local:8123"


def test_only_a_manager_may_add_a_provider(env) -> None:  # noqa: ANN001
    with pytest.raises(OctopError) as excinfo:
        env["manager"].create_provider(
            env["family"].id, env["spouse"], kind="MQTT", name="Broker"
        )
    assert excinfo.value.code is ErrorCode.FORBIDDEN


def test_an_unknown_provider_kind_is_refused(env) -> None:  # noqa: ANN001
    with pytest.raises(HomeMindError) as excinfo:
        env["manager"].create_provider(
            env["family"].id, env["owner"], kind="ZWAVE", name="Nope"
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_a_wildcard_allow_list_entry_is_refused_at_write_time(env) -> None:  # noqa: ANN001
    """A wildcard in the allow-list would defeat the allow-list."""
    with pytest.raises(HomeMindError) as excinfo:
        env["manager"].create_provider(
            env["family"].id,
            env["owner"],
            kind="MQTT",
            name="Broker",
            topic_allowlist=["homemind/#"],
        )
    assert "wildcard" in str(excinfo.value)


def test_replacing_the_allow_list_validates_it(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="MQTT", name="Broker"
    )
    updated = env["manager"].set_topic_allowlist(
        env["family"].id, provider.id, env["owner"], ["homemind/+/set/on"]
    )
    assert updated.topic_allowlist == ["homemind/+/set/on"]
    with pytest.raises(HomeMindError):
        env["manager"].set_topic_allowlist(
            env["family"].id, provider.id, env["owner"], ["homemind/#"]
        )


def test_a_provider_of_another_family_is_not_found(env) -> None:  # noqa: ANN001
    other = env["families"].create_family(
        env["spouse"], name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    theirs = env["manager"].create_provider(
        other.id, env["spouse"], kind="MQTT", name="Theirs"
    )
    with pytest.raises(HomeMindError) as excinfo:
        env["manager"].probe(env["family"].id, theirs.id, env["owner"])
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_NOT_FOUND


# ------------------------------------------------------------------- probe


def test_probe_records_the_outcome(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    assert env["manager"].probe(env["family"].id, provider.id, env["owner"]) is True
    assert env["repo"].get_provider(provider.id).last_error is None

    env["adapter"].reachable = False
    assert env["manager"].probe(env["family"].id, provider.id, env["owner"]) is False
    stored = env["repo"].get_provider(provider.id)
    assert stored.last_error == "probeFailed"
    assert stored.last_seen_at is not None


# ---------------------------------------------------------------- entities


def test_sync_records_the_entities_and_their_catalogue(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    rows = env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    assert {row.external_entity_id for row in rows} == {
        "light.hall",
        "light.kitchen",
        "sensor.temp",
    }
    hall = env["repo"].get_entity_by_external(provider.id, "light.hall")
    assert hall.state["state"] == "on"
    # The capability list is what an agent may actually ask for.
    assert hall.capabilities == ["turn_on", "turn_off", "toggle"]
    sensor = env["repo"].get_entity_by_external(provider.id, "sensor.temp")
    assert sensor.capabilities == []


def test_sync_is_idempotent(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    first = {row.id for row in env["manager"].list_entities(env["family"].id, env["owner"])}
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    second = {row.id for row in env["manager"].list_entities(env["family"].id, env["owner"])}
    assert first == second
    assert len(first) == 3


def test_an_outage_leaves_the_previous_mapping_intact(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    env["adapter"].reachable = False
    rows = env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    # A broker that went away must not erase what the family had.
    assert len(rows) == 3
    # The recorded reason is the exception *type*, never its message: a
    # connection error can carry a host name.
    assert env["repo"].get_provider(provider.id).last_error == "ConnectionError"


def test_an_entity_of_another_family_is_not_found(env) -> None:  # noqa: ANN001
    other = env["families"].create_family(
        env["spouse"], name="Neighbour", timezone="Asia/Shanghai", locale="zh"
    )
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    entity = env["manager"].list_entities(env["family"].id, env["owner"])[0]
    with pytest.raises(HomeMindError):
        env["manager"].get_entity(other.id, entity.id, env["spouse"])


# --------------------------------------------------------------- catalogue


def test_the_catalogue_is_all_an_agent_may_ask_for(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    hall = env["repo"].get_entity_by_external(provider.id, "light.hall")
    commands = env["manager"].commands_for(env["family"].id, hall.id, env["owner"])
    assert sorted(c["name"] for c in commands) == ["toggle", "turn_off", "turn_on"]
    assert all(c["risk"] == RISK_LOW for c in commands)


def test_a_sensor_exposes_no_commands(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    sensor = env["repo"].get_entity_by_external(provider.id, "sensor.temp")
    assert env["manager"].commands_for(env["family"].id, sensor.id, env["owner"]) == []


def test_previewing_a_command_describes_it_without_running_it(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    hall = env["repo"].get_entity_by_external(provider.id, "light.hall")
    preview = env["manager"].preview_command(
        env["family"].id, hall.id, env["owner"], command_name="turn_off"
    )
    assert preview.risk == RISK_LOW
    assert "light.hall" in preview.summary


def test_previewing_a_lock_is_refused(env) -> None:  # noqa: ANN001
    """Even a mapped lock refuses: the domain is blocked outright."""
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    lock = env["repo"].upsert_entity(
        env["family"].id,
        provider_id=provider.id,
        external_entity_id="lock.front_door",
        domain="lock",
        name="Front door",
    )
    with pytest.raises(HomeMindError) as excinfo:
        env["manager"].preview_command(
            env["family"].id, lock.id, env["owner"], command_name="unlock"
        )
    assert excinfo.value.code is HomeMindErrorCode.FAMILY_INVALID


def test_previewing_a_read_only_sensor_is_refused(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    sensor = env["repo"].upsert_entity(
        env["family"].id,
        provider_id=provider.id,
        external_entity_id="sensor.temp",
        domain="sensor",
        name="Temperature",
    )
    with pytest.raises(HomeMindError):
        env["manager"].preview_command(
            env["family"].id, sensor.id, env["owner"], command_name="turn_on"
        )


# ------------------------------------------------------------------ policy


def test_high_risk_domains_always_require_approval(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    thermostat = env["repo"].upsert_entity(
        env["family"].id,
        provider_id=provider.id,
        external_entity_id="climate.hall",
        domain="climate",
        name="Hall thermostat",
    )
    assert env["manager"].requires_approval(thermostat.id, family_policy="AUTO") is True


def test_a_light_follows_the_family_policy(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    lamp = env["repo"].upsert_entity(
        env["family"].id,
        provider_id=provider.id,
        external_entity_id="light.hall",
        domain="light",
        name="Hall",
    )
    assert env["manager"].requires_approval(lamp.id, family_policy="AUTO") is False
    assert env["manager"].requires_approval(lamp.id, family_policy="APPROVE_ALL") is True


def test_an_unknown_entity_always_requires_approval(env) -> None:  # noqa: ANN001
    assert env["manager"].requires_approval("no-such-entity") is True


# ----------------------------------------------------------------- cleanup


def test_deleting_a_provider_removes_its_entities(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    assert env["manager"].delete_provider(env["family"].id, provider.id, env["owner"]) is True
    assert env["manager"].list_entities(env["family"].id, env["owner"]) == []
    assert env["manager"].list_providers(env["family"].id, env["owner"]) == []


def test_only_a_manager_may_delete_a_provider(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="MQTT", name="Broker"
    )
    with pytest.raises(OctopError):
        env["manager"].delete_provider(env["family"].id, provider.id, env["spouse"])


def test_a_member_may_read_but_not_sync(env) -> None:  # noqa: ANN001
    provider = env["manager"].create_provider(
        env["family"].id, env["owner"], kind="HOME_ASSISTANT", name="Home"
    )
    env["manager"].sync_entities(env["family"].id, provider.id, env["owner"])
    # Reading state is harmless; refreshing the mapping is a manager act.
    assert len(env["manager"].list_entities(env["family"].id, env["spouse"])) == 3
    with pytest.raises(OctopError):
        env["manager"].sync_entities(env["family"].id, provider.id, env["spouse"])


def test_risk_constants_stay_distinct() -> None:
    assert len({RISK_LOW, RISK_HIGH, RISK_BLOCKED}) == 3
