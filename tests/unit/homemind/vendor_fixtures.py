"""Xiaomi and Huawei device-mapping fixtures (plan phases 7–8).

**No vendor integration happens here.** The plan's first delivery tier for both
brands is the same: the household brings the devices into Home Assistant, and
HomeMind reads them over the existing adapter. This file therefore holds the
*shapes those devices produce once Home Assistant has them* — a Xiaomi lamp
surfaced by the Xiaomi Home integration, a HarmonyOS device surfaced by a
Matter or vendor bridge.

Two rules these fixtures exist to enforce:

* **Mapping is by domain and attributes, never by marketing name.** A
  ``xiaomi`` or ``huawei`` string may appear as ``manufacturer`` because that
  is what the device really says, but nothing downstream branches on it. A
  vendor's next product name changes nothing about what the device can do.
* **Everything here is invented.** No model numbers, MAC addresses,
  household identifiers, coordinates, or account identifiers — only the
  structure of Home Assistant's state payloads.
"""

from __future__ import annotations

from typing import Any

#: Integration identifiers Home Assistant reports. Recorded here so the
#: provenance test can assert that an unmodelled vendor routes through the
#: normal Home Assistant path instead of a vendor-specific branch.
XIAOMI_INTEGRATION = "xiaomi_miio"
HUAWEI_HARMONYOS_INTEGRATION = "huawei_harmonyos"
MATTER_INTEGRATION = "matter"

#: Manufacturer strings as devices actually report them. Display only.
XIAOMI_MANUFACTURER = "Xiaomi"
XIAOMI_ROBOROCK_MANUFACTURER = "Roborock"
HUAWEI_MANUFACTURER = "Huawei"
HUAWEI_HARMONYOS_MANUFACTURER = "HUAWEI"


def _state(
    entity_id: str,
    state: str,
    attributes: dict[str, Any],
    *,
    updated: str = "2026-03-02T09:00:00+00:00",
) -> dict[str, Any]:
    return {
        "entity_id": entity_id,
        "state": state,
        "attributes": {
            "friendly_name": entity_id.split(".", 1)[1].replace("_", " ").title(),
            **attributes,
        },
        "last_changed": updated,
        "last_updated": updated,
    }


#: Xiaomi surfaces through the Xiaomi Home integration as ordinary HA
#: domains. The plan asks for lamp, outlet, air purifier, fan, vacuum,
#: curtain and sensor coverage.
XIAOMI_STATES: tuple[dict[str, Any], ...] = (
    _state(
        "light.xiaomi_ceiling_light",
        "on",
        {
            "device_id": "xm_dev_light_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
            "model_name": "MJJCQ01ZM",
            "brightness": 200,
            "brightness_range_max": 255,
            "color_temp_kelvin": 4000,
            "min_color_temp_kelvin": 2200,
            "max_color_temp_kelvin": 6500,
            "area": "Living Room",
        },
    ),
    _state(
        "switch.xiaomi_plug",
        "off",
        {
            "device_id": "xm_dev_plug_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
            "model_name": "ZNCZ02LM",
        },
    ),
    _state(
        "fan.xiaomi_air_purifier",
        "on",
        {
            "device_id": "xm_dev_purifier_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
            "model_name": "AC-M15-SC",
            "percentage": 50,
            "percentage_step": 33.3,
            "area": "Bedroom",
        },
    ),
    _state(
        "fan.xiaomi_evaporator",
        "on",
        {
            "device_id": "xm_dev_evap_1",
            "integration": XIAOMI_INTEGRATION,
            "percentage": 33,
        },
    ),
    _state(
        "vacuum.roborock_s5",
        "cleaning",
        {
            "device_id": "xm_dev_vacuum_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_ROBOROCK_MANUFACTURER,
            "model_name": "S5 Max",
            "battery_level": 62,
            "area": "Hallway",
        },
    ),
    _state(
        "cover.xiaomi_curtain",
        "open",
        {
            "device_id": "xm_dev_curtain_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
            "current_position": 70,
            "area": "Living Room",
        },
    ),
    _state(
        "sensor.xiaomi_air_quality",
        "42",
        {
            "device_id": "xm_dev_purifier_1",
            "integration": XIAOMI_INTEGRATION,
            "device_class": "aqi",
            "unit_of_measurement": "µg/m³",
        },
    ),
    # A lock: present so the refusal path has a realistic Xiaomi case. It must
    # remain blocked; vendor relevance is not a risk signal.
    _state(
        "lock.xiaomi_front_door",
        "locked",
        {
            "device_id": "xm_dev_lock_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
        },
    ),
    # A camera: also out of scope this round.
    _state(
        "camera.xiaomi_doorbell",
        "idle",
        {
            "device_id": "xm_dev_cam_1",
            "integration": XIAOMI_INTEGRATION,
            "manufacturer": XIAOMI_MANUFACTURER,
        },
    ),
)

#: Huawei/HarmonyOS devices reach Home Assistant through Matter or a vendor
#: bridge. The plan asks for alias data for display without hard-coding any
#: command against a marketing name.
HUAWEI_STATES: tuple[dict[str, Any], ...] = (
    _state(
        "light.huawei_living_room",
        "off",
        {
            "device_id": "hw_dev_light_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_MANUFACTURER,
            "model_name": "CEE-WL02",
            "brightness": 0,
            "brightness_range_max": 254,
            "color_temp_kelvin": 4000,
            "area": "Living Room",
        },
    ),
    _state(
        "switch.huawei_socket",
        "on",
        {
            "device_id": "hw_dev_plug_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_MANUFACTURER,
            "model_name": "CBP3-TR",
        },
    ),
    _state(
        "sensor.huawei_climate_sensor",
        "21.5",
        {
            "device_id": "hw_dev_sensor_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_HARMONYOS_MANUFACTURER,
            "device_class": "temperature",
            "unit_of_measurement": "°C",
        },
    ),
    _state(
        "climate.huawei_thermostat",
        "heat",
        {
            "device_id": "hw_dev_thermostat_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_MANUFACTURER,
            "current_temperature": 19.0,
            "temperature": 21.0,
            "min_temp": 5,
            "max_temp": 35,
        },
    ),
    # Lock, camera and alarm: blocked in this release regardless of brand.
    _state(
        "lock.huawei_front_door",
        "locked",
        {
            "device_id": "hw_dev_lock_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_HARMONYOS_MANUFACTURER,
        },
    ),
    _state(
        "camera.huawei_indoor",
        "idle",
        {
            "device_id": "hw_dev_cam_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_HARMONYOS_MANUFACTURER,
        },
    ),
    _state(
        "alarm_control_panel.huawei_alarm",
        "disarmed",
        {
            "device_id": "hw_dev_alarm_1",
            "integration": MATTER_INTEGRATION,
            "manufacturer": HUAWEI_HARMONYOS_MANUFACTURER,
        },
    ),
)

ALL_VENDOR_STATES: tuple[dict[str, Any], ...] = XIAOMI_STATES + HUAWEI_STATES


def states_for(integration: str) -> list[dict[str, Any]]:
    return [dict(s) for s in ALL_VENDOR_STATES if s["attributes"].get("integration") == integration]


def entity(states: tuple[dict[str, Any], ...], entity_id: str) -> dict[str, Any]:
    return next(dict(s) for s in states if s["entity_id"] == entity_id)


__all__ = [
    "ALL_VENDOR_STATES",
    "HUAWEI_STATES",
    "MATTER_INTEGRATION",
    "XIAOMI_INTEGRATION",
    "XIAOMI_STATES",
    "entity",
    "states_for",
]
