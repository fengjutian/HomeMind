"""Unified device capability model (plan phase 5).

The load-bearing property: capability is derived from the ecosystem's domain
and attributes, never from a vendor name. A Xiaomi lamp and a Philips lamp
resolve to the same capabilities because both are ``light``.
"""

from __future__ import annotations

from homemind.infra.family.smart_home import RISK_BLOCKED, RISK_HIGH, RISK_LOW, SmartEntity
from homemind.infra.family.smart_home_descriptors import (
    CAPABILITY_BY_DOMAIN,
    CapabilityKind,
    agent_view,
    capabilities_for_domain,
    describe_entity,
    describe_state,
    device_key_for,
)


def _entity(
    external_id: str,
    domain: str,
    *,
    name: str = "Lamp",
    state: str = "on",
    attributes: dict[str, object] | None = None,
) -> SmartEntity:
    return SmartEntity(
        external_id=external_id,
        domain=domain,
        name=name,
        state=state,
        attributes=dict(attributes or {}),
    )


class TestCapabilityMapping:
    def test_maps_by_domain_not_by_brand(self) -> None:
        for domain, expected in (
            ("light", CapabilityKind.LIGHT),
            ("switch", CapabilityKind.SWITCH),
            ("climate", CapabilityKind.CLIMATE),
            ("cover", CapabilityKind.CURTAIN),
            ("vacuum", CapabilityKind.VACUUM),
            ("fan", CapabilityKind.AIR_PURIFIER),
            ("sensor", CapabilityKind.SENSOR),
            ("scene", CapabilityKind.SCENE),
        ):
            assert CAPABILITY_BY_DOMAIN[domain] is expected

    def test_no_vendor_name_appears_in_the_mapping(self) -> None:
        banned = ("xiaomi", "huawei", "philips", "brand", "matter")
        keys = " ".join(CAPABILITY_BY_DOMAIN).lower()
        assert not any(word in keys for word in banned)

    def test_same_domain_yields_same_capabilities_across_brands(self) -> None:
        xiaomi = _entity("light.a", "light", attributes={"manufacturer": "Xiaomi"})
        philips = _entity("light.b", "light", attributes={"manufacturer": "Philips"})
        assert describe_entity(xiaomi, provider_id="p1").capabilities == (
            describe_entity(philips, provider_id="p1").capabilities
        )

    def test_unknown_domain_degrades_to_unknown_and_stays_read_only(self) -> None:
        caps = capabilities_for_domain("some_future_domain")
        assert caps == [CapabilityKind.UNKNOWN]
        desc = describe_entity(_entity("x.y", "some_future_domain"), provider_id="p1")
        assert desc.risk == RISK_BLOCKED, "an unrecognized domain must not become writable"

    def test_attributes_promote_capabilities(self) -> None:
        caps = capabilities_for_domain("light", {"brightness": 120, "color_temp": 3000})
        assert caps == [
            CapabilityKind.LIGHT,
            CapabilityKind.BRIGHTNESS,
            CapabilityKind.COLOR_TEMPERATURE,
        ]

    def test_blocked_domains_keep_their_capability_kind(self) -> None:
        assert capabilities_for_domain("lock") == [CapabilityKind.LOCK]
        desc = describe_entity(_entity("lock.front", "lock"), provider_id="p1")
        assert desc.risk == RISK_BLOCKED
        assert desc.is_read_only is True

    def test_high_risk_domains_keep_high_risk(self) -> None:
        desc = describe_entity(_entity("cover.blind", "cover"), provider_id="p1")
        assert desc.risk == RISK_HIGH

    def test_low_risk_domains_stay_low_risk(self) -> None:
        desc = describe_entity(_entity("light.a", "light"), provider_id="p1")
        assert desc.risk == RISK_LOW


class TestDeviceIdentity:
    def test_uses_integration_device_id_when_present(self) -> None:
        entity = _entity("light.a", "light", attributes={"device_id": "abc123"})
        assert device_key_for("p1", entity) == "p1:abc123"

    def test_falls_back_to_external_id_without_device_id(self) -> None:
        assert device_key_for("p1", _entity("light.a", "light")) == "p1:entity:light.a"

    def test_rename_does_not_fork_the_device(self) -> None:
        before = _entity("light.a", "light", name="Old", attributes={"device_id": "d1"})
        after = _entity("light.a", "light", name="New", attributes={"device_id": "d1"})
        assert device_key_for("p1", before) == device_key_for("p1", after)

    def test_several_entities_of_one_device_share_a_key(self) -> None:
        main = _entity("light.a", "light", attributes={"device_id": "d1"})
        energy = _entity("sensor.a_power", "sensor", attributes={"device_id": "d1"})
        assert device_key_for("p1", main) == device_key_for("p1", energy)

    def test_different_providers_never_collide(self) -> None:
        entity = _entity("light.a", "light", attributes={"device_id": "d1"})
        assert device_key_for("p1", entity) != device_key_for("p2", entity)

    def test_blank_device_id_is_ignored(self) -> None:
        entity = _entity("light.a", "light", attributes={"device_id": "   "})
        assert device_key_for("p1", entity) == "p1:entity:light.a"


class TestDescriptorContent:
    def test_carries_identity_facts_from_attributes(self) -> None:
        entity = _entity(
            "light.a",
            "light",
            attributes={
                "manufacturer": "Acme",
                "model_name": "L1000",
                "sw_version": "2.1.0",
                "connection_type": "zigbee",
                "area": "Living Room",
            },
        )
        desc = describe_entity(entity, provider_id="p1", online=True)
        assert desc.manufacturer == "Acme"
        assert desc.model == "L1000"
        assert desc.firmware_version == "2.1.0"
        assert desc.connection == "zigbee"
        assert desc.room == "Living Room"
        assert desc.online is True

    def test_missing_attributes_become_none_not_empty_strings(self) -> None:
        desc = describe_entity(_entity("light.a", "light"), provider_id="p1")
        assert desc.manufacturer is None
        assert desc.model is None
        assert desc.room is None
        assert desc.connection == "unknown"

    def test_properties_are_typed_and_marked_writable(self) -> None:
        entity = _entity(
            "light.a",
            "light",
            state="on",
            attributes={"brightness": 120, "brightness_range_max": 255},
        )
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert state.properties["state"].type == "string"
        assert state.properties["state"].writable is True
        bright = state.properties["brightness"]
        assert (bright.type, bright.unit, bright.maximum) == ("number", "%", 255.0)

    def test_read_only_domain_properties_are_not_writable(self) -> None:
        entity = _entity("sensor.temp", "sensor", attributes={"temperature": 21})
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert state.properties["state"].writable is False
        assert state.properties["temperature"].unit == "°C"

    def test_state_enum_covers_the_domain(self) -> None:
        entity = _entity("cover.blind", "cover")
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert "open" in (state.properties["state"].values or ())


class TestRawIsolation:
    def test_raw_payload_is_namespaced_by_adapter(self) -> None:
        entity = _entity("light.a", "light", attributes={"friendly_name": "Lamp", "x": 1})
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert state.raw == {"home_assistant": {"friendly_name": "Lamp", "x": 1}}

    def test_agent_view_never_exposes_raw(self) -> None:
        entity = _entity(
            "light.a",
            "light",
            attributes={"friendly_name": "Lamp", "integration_secret": "s3cret"},
        )
        desc = describe_entity(entity, provider_id="p1")
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        view = agent_view(desc, state)
        assert "raw" not in view
        assert "s3cret" not in str(view)
        assert "friendly_name" not in str(view)

    def test_properties_carry_normalized_current_values(self) -> None:
        entity = _entity(
            "light.a",
            "light",
            state="on",
            attributes={"brightness": 120, "color_temp": 3000, "temperature": 21.5},
        )
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert state.properties["state"].value == "on"
        assert state.properties["brightness"].value == 120.0
        assert state.properties["color_temperature"].value == 3000.0
        assert state.properties["temperature"].value == 21.5

    def test_unreported_numeric_attribute_yields_none_not_zero(self) -> None:
        entity = _entity("sensor.x", "sensor", attributes={"temperature": "unavailable"})
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        assert state.properties["temperature"].value is None

    def test_agent_view_carries_normalized_properties(self) -> None:
        entity = _entity("light.a", "light", attributes={"brightness": 42})
        desc = describe_entity(entity, provider_id="p1")
        state = describe_state(entity, provider_id="p1", adapter_kind="home_assistant")
        view = agent_view(desc, state)
        assert view["properties"]["brightness"]["value"] == 42.0
        assert view["properties"]["brightness"]["writable"] is True
        assert view["risk"] == RISK_LOW
        assert view["capabilities"] == ["light", "brightness"]
