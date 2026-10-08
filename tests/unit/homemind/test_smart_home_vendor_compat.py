"""Xiaomi and Huawei reach HomeMind through Home Assistant (plan phases 7–8).

The claims under test are deliberately narrow, because the plan forbids
vendor-direct integration in this round:

1. A Xiaomi lamp and a Philips lamp produce **identical** capabilities.
2. A Huawei device under Matter produces **identical** capabilities to the
   same device from any other source.
3. Vendor relevance never changes risk, writability, or the command set.
4. Locks, cameras and alarm panels stay blocked no matter the brand.
5. Region and account information never enters the agent-facing view.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from homemind.infra.family.smart_home import (
    RISK_BLOCKED,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    risk_for,
)
from homemind.infra.family.smart_home_adapters import HomeAssistantAdapter
from homemind.infra.family.smart_home_descriptors import (
    CapabilityKind,
    DeviceDescriptor,
    SourceKind,
    agent_view,
    capabilities_for_domain,
    describe_entity,
    describe_state,
    device_key_for,
    source_for,
)
from tests.unit.homemind import vendor_fixtures as vf
from tests.unit.homemind.ha_fixtures import STATES as GENERIC_STATES


def _entity(state: dict[str, Any]) -> Any:
    from homemind.infra.family.smart_home import SmartEntity

    return SmartEntity(
        external_id=state["entity_id"],
        domain=state["entity_id"].split(".", 1)[0],
        name=state["attributes"].get("friendly_name", state["entity_id"]),
        state=state["state"],
        attributes=dict(state["attributes"]),
    )


def _descriptor(state: dict[str, Any]) -> DeviceDescriptor:
    return describe_entity(_entity(state), provider_id="p1", adapter_kind="home_assistant")


class TestNoBrandBranching:
    def test_xiaomi_light_matches_a_generic_light_capabilities(self) -> None:
        xiaomi = _descriptor(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light"))
        generic = next(s for s in GENERIC_STATES if s["entity_id"].startswith("light."))
        assert (
            xiaomi.capabilities == describe_entity(_entity(generic), provider_id="p1").capabilities
        )

    def test_huawei_light_matches_a_generic_light(self) -> None:
        huawei = _descriptor(vf.entity(vf.HUAWEI_STATES, "light.huawei_living_room"))
        generic = next(s for s in GENERIC_STATES if s["entity_id"].startswith("light."))
        assert (
            huawei.capabilities == describe_entity(_entity(generic), provider_id="p1").capabilities
        )

    @pytest.mark.parametrize(
        "state",
        vf.ALL_VENDOR_STATES,
        ids=lambda s: s["entity_id"],
    )
    def test_every_vendor_device_maps_through_its_domain_alone(self, state: dict[str, Any]) -> None:
        entity = _entity(state)
        with_attrs = capabilities_for_domain(entity.domain, entity.attributes)
        without_attrs = capabilities_for_domain(entity.domain, {})
        # The base capability is the domain's alone; attributes may only add
        # generic ones on top, never a brand-specific kind.
        assert with_attrs[0] == without_attrs[0]
        assert set(with_attrs) - set(without_attrs) <= {
            CapabilityKind.BRIGHTNESS,
            CapabilityKind.COLOR_TEMPERATURE,
        }
        joined = " ".join(c.value for c in with_attrs).lower()
        for brand in ("xiaomi", "huawei", "roborock", "harmonyos"):
            assert brand not in joined

    def test_no_vendor_name_appears_in_the_capability_mapping_table(self) -> None:
        from homemind.infra.family.smart_home_descriptors import CAPABILITY_BY_DOMAIN

        joined = " ".join(CAPABILITY_BY_DOMAIN).lower()
        for brand in ("xiaomi", "huawei", "roborock", "harmonyos"):
            assert brand not in joined

    def test_vendor_command_set_is_chosen_by_domain(self) -> None:
        """The catalogue must not offer a brand a different command list."""
        adapter = HomeAssistantAdapter(base_url="http://ha.invalid:8123", token="x")
        for state in vf.ALL_VENDOR_STATES:
            domain = state["entity_id"].split(".", 1)[0]
            if risk_for(domain) == RISK_BLOCKED:
                assert adapter.commands_for(domain) == [], domain
            else:
                assert adapter.commands_for(domain), domain


class TestRiskIsBrandAgnostic:
    @pytest.mark.parametrize(
        "state",
        vf.ALL_VENDOR_STATES,
        ids=lambda s: s["entity_id"],
    )
    def test_risk_comes_from_the_domain_not_the_manufacturer(self, state: dict[str, Any]) -> None:
        domain = state["entity_id"].split(".", 1)[0]
        assert risk_for(domain) == _descriptor(state).risk

    @pytest.mark.parametrize("entity_id", ["lock.xiaomi_front_door", "lock.huawei_front_door"])
    def test_vendor_locks_are_blocked(self, entity_id: str) -> None:
        state = next(s for s in vf.ALL_VENDOR_STATES if s["entity_id"] == entity_id)
        assert _descriptor(state).risk == RISK_BLOCKED
        assert _descriptor(state).is_read_only is True

    @pytest.mark.parametrize(
        "entity_id",
        [
            "camera.xiaomi_doorbell",
            "camera.huawei_indoor",
            "alarm_control_panel.huawei_alarm",
        ],
    )
    def test_vendor_cameras_and_alarms_are_blocked(self, entity_id: str) -> None:
        state = next(s for s in vf.ALL_VENDOR_STATES if s["entity_id"] == entity_id)
        assert _descriptor(state).risk == RISK_BLOCKED

    def test_vacuum_is_medium_and_curtain_is_high(self) -> None:
        assert _descriptor(vf.entity(vf.XIAOMI_STATES, "vacuum.roborock_s5")).risk == RISK_MEDIUM
        assert _descriptor(vf.entity(vf.XIAOMI_STATES, "cover.xiaomi_curtain")).risk == RISK_HIGH

    def test_vendor_lights_and_switches_are_low(self) -> None:
        assert (
            _descriptor(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light")).risk == RISK_LOW
        )
        assert _descriptor(vf.entity(vf.HUAWEI_STATES, "switch.huawei_socket")).risk == RISK_LOW


class TestVendorCapabilities:
    @pytest.mark.parametrize(
        "entity_id,capability",
        [
            ("light.xiaomi_ceiling_light", CapabilityKind.LIGHT),
            ("switch.xiaomi_plug", CapabilityKind.SWITCH),
            ("fan.xiaomi_air_purifier", CapabilityKind.AIR_PURIFIER),
            ("fan.xiaomi_evaporator", CapabilityKind.AIR_PURIFIER),
            ("vacuum.roborock_s5", CapabilityKind.VACUUM),
            ("cover.xiaomi_curtain", CapabilityKind.CURTAIN),
            ("sensor.xiaomi_air_quality", CapabilityKind.SENSOR),
            ("light.huawei_living_room", CapabilityKind.LIGHT),
            ("sensor.huawei_climate_sensor", CapabilityKind.SENSOR),
            ("climate.huawei_thermostat", CapabilityKind.CLIMATE),
        ],
    )
    def test_plan_required_devices_resolve_to_capabilities(
        self, entity_id: str, capability: CapabilityKind
    ) -> None:
        state = next(s for s in vf.ALL_VENDOR_STATES if s["entity_id"] == entity_id)
        assert _descriptor(state).capabilities[0] is capability

    def test_vacuum_map_and_camera_are_absent_from_the_catalogue(self) -> None:
        adapter = HomeAssistantAdapter(base_url="http://ha.invalid:8123", token="x")
        names = {c.name for c in adapter.commands_for("vacuum")}
        assert names == {"start", "pause", "return_to_base"}
        assert adapter.commands_for("camera") == []


class TestSourceProvenance:
    def test_matter_devices_are_reported_as_matter(self) -> None:
        desc = _descriptor(vf.entity(vf.HUAWEI_STATES, "light.huawei_living_room"))
        assert desc.source is SourceKind.MATTER
        assert desc.source_detail == vf.MATTER_INTEGRATION

    def test_xiaomi_devices_arrive_through_the_vendor_integration(self) -> None:
        desc = _descriptor(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light"))
        assert desc.source is SourceKind.HOME_ASSISTANT
        assert desc.source_detail == vf.XIAOMI_INTEGRATION

    def test_source_never_changes_the_agent_view_risk(self) -> None:
        for state in vf.ALL_VENDOR_STATES:
            view = agent_view(_descriptor(state))
            assert view["risk"] == risk_for(state["entity_id"].split(".", 1)[0])
            assert view["read_only"] is (view["risk"] == RISK_BLOCKED)

    def test_manufacturer_is_displayed_but_never_branched_on(self) -> None:
        desc = _descriptor(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light"))
        assert desc.manufacturer == vf.XIAOMI_MANUFACTURER
        # Present in the view, absent from the capability decision.
        assert "manufacturer" in agent_view(desc)
        assert "Xiaomi" not in str(desc.capabilities)


class TestPrivacy:
    def test_region_and_account_never_enter_the_view(self) -> None:
        """Nothing vendor-specific about *where* or *who* reaches the agent."""
        forbidden = ("region", "country", "account", "uid", "open_id", "mac", "latitude")
        for state in vf.ALL_VENDOR_STATES:
            blob = json.dumps(agent_view(_descriptor(state))).lower()
            for word in forbidden:
                assert word not in blob, f"{state['entity_id']} leaked {word}"

    def test_raw_attributes_are_not_in_the_agent_view(self) -> None:
        for state in vf.ALL_VENDOR_STATES:
            entity = _entity(state)
            device_state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
            view = agent_view(_descriptor(state), device_state)
            assert "raw" not in view
            assert "friendly_name" not in json.dumps(view)

    def test_normalized_values_survive_for_a_vendor_device(self) -> None:
        state = vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light")
        device_state = describe_state(_entity(state), provider_id="p1", adapter_kind="ha")
        props = device_state.properties
        assert props["state"].value == "on"
        assert props["brightness"].value == 200.0
        assert props["color_temperature"].value == 4000.0
        assert props["color_temperature"].unit == "K"

    def test_device_keys_are_stable_across_a_vendor_rename(self) -> None:
        before = _entity(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light"))
        after_state = dict(vf.entity(vf.XIAOMI_STATES, "light.xiaomi_ceiling_light"))
        after_state["attributes"] = {**after_state["attributes"], "friendly_name": "New Name"}
        assert device_key_for("p1", before) == device_key_for("p1", _entity(after_state))

    def test_purifier_power_sensor_aggregates_with_its_purifier(self) -> None:
        purifier = _entity(vf.entity(vf.XIAOMI_STATES, "fan.xiaomi_air_purifier"))
        sensor = _entity(vf.entity(vf.XIAOMI_STATES, "sensor.xiaomi_air_quality"))
        assert device_key_for("p1", purifier) == device_key_for("p1", sensor)


class TestSourceFor:
    def test_unknown_integration_is_not_a_special_case(self) -> None:
        assert source_for({"integration": "brand_new_thing"}) is SourceKind.HOME_ASSISTANT

    def test_matter_is_the_only_modelled_non_ha_source(self) -> None:
        assert source_for({"integration": "matter"}) is SourceKind.MATTER
        assert source_for({"integration": "hue"}) is SourceKind.HOME_ASSISTANT
