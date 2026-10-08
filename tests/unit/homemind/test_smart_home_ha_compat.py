"""Home Assistant compatibility against recorded, scrubbed fixtures
(plan phase 6).

The plan's acceptance list — rename, offline, deletion, duplicate events,
reconnect, and an unknown domain degrading to read-only — is what these cover.
No test here may reach a real household network.
"""

from __future__ import annotations

from typing import Any

import pytest

from homemind.infra.family.smart_home import (
    RISK_BLOCKED,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    BaseSmartHomeAdapter,
    CommandNotAllowed,
    risk_for,
)
from homemind.infra.family.smart_home_adapters import HA_COMMANDS, HomeAssistantAdapter
from homemind.infra.family.smart_home_descriptors import (
    CapabilityKind,
    describe_entity,
    device_key_for,
)
from tests.unit.homemind import ha_fixtures as fx
from tests.unit.homemind.conftest import seed_entity as _seed


@pytest.fixture
def adapter() -> HomeAssistantAdapter:
    return HomeAssistantAdapter(base_url=fx.FAKE_BASE_URL, token=fx.FAKE_TOKEN)


def _domain_of(entity_id: str) -> str:
    return entity_id.split(".", 1)[0]


# --------------------------------------------------------------- command catalog


class TestCommandCatalog:
    @pytest.mark.parametrize(
        "domain,expected",
        [
            ("light", {"turn_on", "turn_off", "toggle"}),
            ("switch", {"turn_on", "turn_off"}),
            ("scene", {"activate"}),
            ("climate", {"set_temperature", "set_hvac_mode"}),
            ("cover", {"open_cover", "close_cover", "stop_cover", "set_cover_position"}),
            ("fan", {"turn_on", "turn_off", "set_fan_percentage"}),
            ("vacuum", {"start", "pause", "return_to_base"}),
            ("humidifier", {"set_humidity"}),
        ],
    )
    def test_every_planned_domain_has_commands(
        self, adapter: HomeAssistantAdapter, domain: str, expected: set[str]
    ) -> None:
        assert {c.name for c in adapter.commands_for(domain)} == expected

    def test_read_only_domains_have_no_commands(self, adapter: HomeAssistantAdapter) -> None:
        for domain in ("sensor", "binary_sensor", "lock", "camera", "alarm_control_panel"):
            assert adapter.commands_for(domain) == [], domain

    def test_vacuum_camera_and_map_commands_are_absent(self, adapter: HomeAssistantAdapter) -> None:
        """The plan excludes map/camera/lock control from this round."""
        names = {c.name for c in adapter.commands_for("vacuum")}
        for forbidden in ("locate", "set_map", "clean_spot", "camera_on", "stream"):
            assert forbidden not in names

    @pytest.mark.asyncio
    @pytest.mark.asyncio
    async def test_blocked_domain_is_refused_on_the_real_path(
        self, adapter: HomeAssistantAdapter
    ) -> None:
        """A lock has no catalog entry, so the command never resolves."""
        for domain in ("lock", "camera", "alarm_control_panel"):
            assert adapter.commands_for(domain) == [], domain
            with pytest.raises(CommandNotAllowed):
                await adapter.preview_command("lock.front_door", "turn_on", {})


def _command(adapter: HomeAssistantAdapter, domain: str, name: str) -> Any:
    for command in adapter.commands_for(domain):
        if command.name == name:
            return command
    raise AssertionError(f"{domain}.{name} is not in the catalogue")


# ------------------------------------------------------------------- risk model


class TestRiskModel:
    def test_vacuum_is_medium_and_still_approvable(self) -> None:
        assert risk_for("vacuum") == RISK_MEDIUM

    def test_medium_is_distinct_from_high(self) -> None:
        assert risk_for("vacuum") != risk_for("climate")

    def test_lock_and_unknown_stay_blocked(self) -> None:
        assert risk_for("lock") == RISK_BLOCKED
        assert risk_for("hologram") == RISK_BLOCKED

    def test_cover_is_high(self) -> None:
        assert risk_for("cover") == RISK_HIGH

    def test_light_is_low(self) -> None:
        assert risk_for("light") == RISK_LOW


# --------------------------------------------------------------- payload schemas


class TestStructuredCommands:
    def test_cover_position_requires_a_number(self) -> None:
        command = next(
            c for c in HA_COMMANDS if c.domain == "cover" and c.name == "set_cover_position"
        )
        with pytest.raises(CommandNotAllowed):
            BaseSmartHomeAdapter.validate_payload(command, {"position": "wide"})
        with pytest.raises(CommandNotAllowed):
            BaseSmartHomeAdapter.validate_payload(command, {})
        assert BaseSmartHomeAdapter.validate_payload(command, {"position": 40}) == {"position": 40}

    def test_climate_hvac_mode_is_enum_constrained_in_the_schema(self) -> None:
        command = next(c for c in HA_COMMANDS if c.name == "set_hvac_mode")
        allowed = command.payload_schema["properties"]["hvac_mode"]["enum"]
        assert "heat" in allowed
        assert "cool" in allowed

    def test_every_writable_command_documents_its_schema(self) -> None:
        for command in HA_COMMANDS:
            assert command.payload_schema.get("type") == "object", command.name


# ---------------------------------------------------------------- device identity


class TestDeviceAggregation:
    def test_two_entities_of_one_device_share_a_key(self) -> None:
        lamp = _entity(fx.STATES[0])
        meter = _entity(fx.STATES[1])
        assert lamp.attributes["device_id"] == meter.attributes["device_id"]
        assert device_key_for("p1", lamp) == device_key_for("p1", meter)

    def test_rename_does_not_change_the_key(self) -> None:
        before = _entity(fx.STATES[0])
        after = _entity(fx.renamed("light.living_room_ceiling", "Lounge Light")[0])
        assert before.name != after.name
        assert device_key_for("p1", before) == device_key_for("p1", after)

    def test_distinct_devices_get_distinct_keys(self) -> None:
        keys = {
            device_key_for("p1", _entity(state))
            for state in fx.STATES
            if "device_id" in state["attributes"]
        }
        # 11 states, 9 of which carry a distinct device_id.
        assert len(keys) == 9

    def test_entity_without_device_id_falls_back_per_entity(self) -> None:
        scene = next(s for s in fx.STATES if s["entity_id"] == "scene.movie_time")
        assert device_key_for("p1", _entity(scene)) == "p1:entity:scene.movie_time"


# ------------------------------------------------------------ capability mapping


class TestCapabilityDiscovery:
    @pytest.mark.parametrize(
        "entity_id,capability",
        [
            ("light.living_room_ceiling", CapabilityKind.LIGHT),
            ("switch.robotic_arm_outlet", CapabilityKind.SWITCH),
            ("cover.living_room_curtain", CapabilityKind.CURTAIN),
            ("vacuum.robot_vacuum", CapabilityKind.VACUUM),
            ("fan.air_purifier_bedroom", CapabilityKind.AIR_PURIFIER),
            ("climate.thermostat_hall", CapabilityKind.CLIMATE),
            ("binary_sensor.front_door_contact", CapabilityKind.SENSOR),
            ("scene.movie_time", CapabilityKind.SCENE),
            ("lock.front_door", CapabilityKind.LOCK),
        ],
    )
    def test_domain_maps_to_capability(self, entity_id: str, capability: CapabilityKind) -> None:
        from homemind.infra.family.smart_home_descriptors import capabilities_for_domain

        assert capabilities_for_domain(_domain_of(entity_id))[0] is capability

    def test_brightness_attribute_adds_brightness_capability(self) -> None:
        from homemind.infra.family.smart_home_descriptors import capabilities_for_domain

        caps = capabilities_for_domain(
            "light",
            {"brightness": 180, "color_temp_kelvin": 3200, "supported_color_modes": ["color_temp"]},
        )
        assert CapabilityKind.BRIGHTNESS in caps
        assert CapabilityKind.COLOR_TEMPERATURE in caps

    def test_unknown_domain_degrades_to_unknown(self) -> None:
        from homemind.infra.family.smart_home_descriptors import capabilities_for_domain

        assert capabilities_for_domain("hologram") == [CapabilityKind.UNKNOWN]


# ------------------------------------------------------------------- state shape


class TestEntityParsing:
    @pytest.mark.parametrize("state", fx.STATES, ids=lambda s: s["entity_id"])
    def test_every_fixture_row_parses(self, state: dict[str, Any]) -> None:
        entity = _entity(state)
        assert entity.external_id == state["entity_id"]
        assert entity.domain == _domain_of(state["entity_id"])
        assert isinstance(entity.attributes, dict)

    def test_offline_entity_keeps_its_identity(self) -> None:
        states = fx.went_unavailable("light.living_room_ceiling")
        offline = _entity(next(s for s in states if s["entity_id"] == "light.living_room_ceiling"))
        assert offline.state == "unavailable"
        assert offline.external_id == "light.living_room_ceiling"

    def test_removed_entity_is_absent_from_the_next_snapshot(self) -> None:
        remaining = fx.states_without("vacuum.robot_vacuum")
        assert all(s["entity_id"] != "vacuum.robot_vacuum" for s in remaining)

    def test_duplicate_state_changed_events_are_identical(self) -> None:
        """A reconnect replay must not look like a different change."""
        assert fx.state_changed_event() == fx.state_changed_event()

    def test_state_changed_event_carries_no_credentials(self) -> None:
        blob = str(fx.state_changed_event())
        assert fx.FAKE_TOKEN not in blob
        assert "Bearer" not in blob


class TestSourceProvenance:
    """Source is display metadata. It must never widen what may be done."""

    def test_matter_integration_is_reported_as_matter(self) -> None:
        from homemind.infra.family.smart_home_descriptors import SourceKind, source_for

        assert source_for({"integration": "matter"}) is SourceKind.MATTER

    def test_mqtt_adapter_kind_is_reported_as_mqtt(self) -> None:
        from homemind.infra.family.smart_home_descriptors import SourceKind, source_for

        assert source_for({}, adapter_kind="mqtt") is SourceKind.MQTT

    def test_unknown_vendor_integration_falls_back_to_home_assistant(self) -> None:
        """Xiaomi Home / HarmonyOS arrive as a named integration, not a brand branch."""
        from homemind.infra.family.smart_home_descriptors import SourceKind, source_for

        assert source_for({"integration": "xiaomi_miio"}) is SourceKind.HOME_ASSISTANT
        assert source_for({"integration": "huawei_harmonyos"}) is SourceKind.HOME_ASSISTANT

    def test_source_does_not_change_risk(self) -> None:
        """Same domain, different provenance, same risk and same writability."""
        for integration in ("matter", "xiaomi_miio", "huawei_harmonyos", ""):
            desc = describe_entity(
                _entity(_with(fx.STATES[0], {"integration": integration})),
                provider_id="p1",
            )
            assert desc.risk == RISK_LOW, integration
            assert desc.is_read_only is False, integration

    def test_a_vendor_label_cannot_unlock_a_blocked_device(self) -> None:
        for integration in ("matter", "xiaomi_miio", "huawei_harmonyos"):
            desc = describe_entity(
                _entity(_with(fx.STATES[-1], {"integration": integration})),
                provider_id="p1",
            )
            assert desc.risk == RISK_BLOCKED, integration
            assert desc.is_read_only is True, integration

    def test_source_detail_records_the_integration_name(self) -> None:
        desc = describe_entity(
            _entity(_with(fx.STATES[0], {"integration": "matter"})), provider_id="p1"
        )
        assert desc.source.value == "matter"
        assert desc.source_detail == "matter"


def _with(state: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return {**state, "attributes": {**state["attributes"], **extra}}


class TestEntityPruning:
    """A device removed in Home Assistant must not linger as a ghost."""

    def test_removed_entity_row_is_deleted(self, env: dict[str, Any]) -> None:
        _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
        _seed(env, domain="switch", external="switch.old", device_key="d2", typed=["switch"])

        repo = env["manager"].repo
        repo.prune_missing_entities(env["family_id"], env["provider_id"], ["light.lamp"])

        remaining = {e.external_entity_id for e in repo.list_entities(env["family_id"])}
        assert remaining == {"light.lamp"}

    def test_empty_snapshot_is_not_treated_as_everything_removed(self, env: dict[str, Any]) -> None:
        """A poll that returns nothing must never wipe the device map."""
        _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
        repo = env["manager"].repo

        assert repo.prune_missing_entities(env["family_id"], env["provider_id"], []) == []
        assert len(repo.list_entities(env["family_id"])) == 1

    def test_pruning_is_scoped_to_one_provider(self, env: dict[str, Any]) -> None:
        _seed(env, domain="light", external="light.lamp", device_key="d1", typed=["light"])
        repo = env["manager"].repo

        repo.prune_missing_entities(env["family_id"], "other-provider", [])
        assert len(repo.list_entities(env["family_id"])) == 1


# --------------------------------------------------------------------- helpers


def _entity(state: dict[str, Any]) -> Any:
    from homemind.infra.family.smart_home import SmartEntity

    return SmartEntity(
        external_id=state["entity_id"],
        domain=_domain_of(state["entity_id"]),
        name=state["attributes"].get("friendly_name", state["entity_id"]),
        state=state["state"],
        attributes=dict(state["attributes"]),
    )
