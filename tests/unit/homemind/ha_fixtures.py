"""Sanitized Home Assistant fixtures (plan phase 6).

CI must never reach a real household network, and a hand-written
``httpx`` stub cannot express the cases that actually bite: an entity being
renamed, going offline, disappearing, or emitting the same change twice. These
fixtures are recorded-and-scrubbed in shape — no host, no token, no person, no
geo — so they are safe to commit and still exercise the real adapter.

Every value here is invented. The *structure* mirrors Home Assistant's
``/api/states`` and ``state_changed`` payloads; the contents do not.
"""

from __future__ import annotations

from typing import Any

#: Scrubbed base URL. RFC 5737 / RFC 2606 reserved names, never routable.
FAKE_BASE_URL = "http://ha.invalid:8123"

#: Placeholder token. Not a valid HA long-lived token shape on purpose.
FAKE_TOKEN = "not-a-real-token-0000000000000000"

#: A small house covering every domain the plan lists, including the ones that
#: must degrade to read-only. Entity ids are the "before rename" set.
STATES: tuple[dict[str, Any], ...] = (
    {
        "entity_id": "light.living_room_ceiling",
        "state": "on",
        "attributes": {
            "friendly_name": "Living Room Ceiling",
            "device_id": "dev_lamp_ceiling",
            "brightness": 180,
            "brightness_range_max": 255,
            "supported_color_modes": ["color_temp", "hs"],
            "color_temp_kelvin": 3200,
            "min_color_temp_kelvin": 2000,
            "max_color_temp_kelvin": 6500,
            "manufacturer": "Example Lighting Co",
            "model_name": "L-1000",
            "sw_version": "2.1.0",
            "area": "Living Room",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        # Same physical device as the ceiling light's power meter: proves the
        # device_key aggregation works on a real-shaped payload.
        "entity_id": "sensor.living_room_ceiling_power",
        "state": "12.4",
        "attributes": {
            "friendly_name": "Living Room Ceiling Power",
            "device_id": "dev_lamp_ceiling",
            "device_class": "power",
            "unit_of_measurement": "W",
            "state_class": "measurement",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "switch.robotic_arm_outlet",
        "state": "off",
        "attributes": {
            "friendly_name": "Robotic Arm Outlet",
            "device_id": "dev_outlet_1",
            "manufacturer": "Example Home",
            "model_name": "S-10",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "fan.air_purifier_bedroom",
        "state": "on",
        "attributes": {
            "friendly_name": "Bedroom Air Purifier",
            "device_id": "dev_purifier_1",
            "percentage": 66,
            "percentage_step": 33.3,
            "manufacturer": "Example Air",
            "model_name": "AP-3",
            "area": "Bedroom",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "vacuum.robot_vacuum",
        "state": "docked",
        "attributes": {
            "friendly_name": "Robot Vacuum",
            "device_id": "dev_vacuum_1",
            "battery_level": 87,
            "manufacturer": "Example Robotics",
            "model_name": "RV-5",
            "area": "Hallway",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "cover.living_room_curtain",
        "state": "open",
        "attributes": {
            "friendly_name": "Living Room Curtain",
            "device_id": "dev_curtain_1",
            "current_position": 100,
            "manufacturer": "Example Textiles",
            "area": "Living Room",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "climate.thermostat_hall",
        "state": "heat",
        "attributes": {
            "friendly_name": "Hall Thermostat",
            "device_id": "dev_thermostat_1",
            "current_temperature": 19.5,
            "temperature": 21.0,
            "min_temp": 7,
            "max_temp": 35,
            "hvac_modes": ["off", "heat", "cool", "heat_cool", "auto"],
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "binary_sensor.front_door_contact",
        "state": "off",
        "attributes": {
            "friendly_name": "Front Door Contact",
            "device_id": "dev_door_1",
            "device_class": "door",
        },
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        "entity_id": "scene.movie_time",
        "state": "unknown",
        "attributes": {"friendly_name": "Movie Time", "supported_features": 0},
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        # A domain this build has never seen. Must degrade to a read-only
        # entity rather than become writable by default.
        "entity_id": "hologram.projector_x",
        "state": "on",
        "attributes": {"friendly_name": "Projector X", "device_id": "dev_future_1"},
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
    {
        # A lock: blocked outright, present so the refusal has something to hit.
        "entity_id": "lock.front_door",
        "state": "locked",
        "attributes": {"friendly_name": "Front Door", "device_id": "dev_lock_1"},
        "last_changed": "2026-03-01T10:00:00+00:00",
        "last_updated": "2026-03-01T10:00:00+00:00",
    },
)


def states_without(*entity_ids: str) -> list[dict[str, Any]]:
    """The snapshot minus *entity_ids* — the "a device was removed" case."""
    dropped = set(entity_ids)
    return [dict(s) for s in STATES if s["entity_id"] not in dropped]


def renamed(entity_id: str, new_name: str) -> list[dict[str, Any]]:
    """The snapshot with one entity's display name changed, id untouched.

    Home Assistant keeps ``entity_id`` stable across a friendly-name edit, which
    is exactly the case that must not fork a second device row.
    """
    out: list[dict[str, Any]] = []
    for state in STATES:
        copy = {**state, "attributes": dict(state["attributes"])}
        if state["entity_id"] == entity_id:
            copy["attributes"]["friendly_name"] = new_name
        out.append(copy)
    return out


def went_unavailable(entity_id: str) -> list[dict[str, Any]]:
    """The snapshot with one entity marked ``unavailable`` — the offline case."""
    out: list[dict[str, Any]] = []
    for state in STATES:
        copy = {**state, "attributes": dict(state["attributes"])}
        if state["entity_id"] == entity_id:
            copy["state"] = "unavailable"
        out.append(copy)
    return out


def state_changed_event(
    entity_id: str = "light.living_room_ceiling",
    new_state: str = "off",
) -> dict[str, Any]:
    """A scrubbed ``state_changed`` WebSocket event."""
    base = next(s for s in STATES if s["entity_id"] == entity_id)
    return {
        "event": {
            "event_type": "state_changed",
            "data": {
                "entity_id": entity_id,
                "new_state": {
                    "state": new_state,
                    "attributes": dict(base["attributes"]),
                    "last_changed": "2026-03-01T10:05:00+00:00",
                    "last_updated": "2026-03-01T10:05:00+00:00",
                },
                "old_state": {
                    "state": base["state"],
                    "attributes": dict(base["attributes"]),
                    "last_changed": base["last_changed"],
                    "last_updated": base["last_updated"],
                },
            },
        },
        "id": 1,
        "type": "event",
    }


def api_root() -> dict[str, Any]:
    """The ``GET /api/`` payload the adapter probes with."""
    return {"message": "API running."}
