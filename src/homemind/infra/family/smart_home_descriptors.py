"""Unified device capability model (plan phase 5).

One vocabulary for every integration. Adapters describe *what a device is and
what it can do*; nothing downstream — UI, agent tool surface, audit — needs to
know whether a light arrived through Home Assistant, Matter, or a future vendor
direct connection.

Three rules shape this module:

* **Capabilities come from the ecosystem's own semantics, never from a brand
  name.** A Xiaomi lamp and a Philips lamp are both ``light`` because their
  domain and attributes say so. "Huawei" or "Xiaomi" never appears in a mapping
  table, so a marketing rename cannot change what a device can do.
* **Raw adapter payloads are diagnostic data, not agent input.** ``DeviceState``
  keeps them behind ``raw``, and :func:`agent_view` is the only projection an
  agent-facing surface may use.
* **Risk stays a property of the domain.** Descriptors carry the risk that
  :func:`~homemind.infra.family.smart_home.risk_for` already assigned; this
  module never re-derives it from a payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from homemind.infra.family.smart_home import (
    RISK_BLOCKED,
    SmartEntity,
    risk_for,
)

__all__ = [
    "CAPABILITY_BY_DOMAIN",
    "CapabilityKind",
    "CommandDescriptor",
    "DeviceDescriptor",
    "DeviceState",
    "PropertyDescriptor",
    "agent_view",
    "capabilities_for_domain",
    "describe_entity",
    "device_key_for",
]


class CapabilityKind(StrEnum):
    """What a device can *do*, independent of who made it."""

    SWITCH = "switch"
    LIGHT = "light"
    BRIGHTNESS = "brightness"
    COLOR_TEMPERATURE = "color_temperature"
    CLIMATE = "climate"
    SENSOR = "sensor"
    CURTAIN = "curtain"
    VACUUM = "vacuum"
    AIR_PURIFIER = "air_purifier"
    SCENE = "scene"
    LOCK = "lock"
    CAMERA = "camera"
    UNKNOWN = "unknown"


#: Domain → capability. This table is intentionally keyed on the ecosystem's
#: domain vocabulary. There is no brand column anywhere in this module.
CAPABILITY_BY_DOMAIN: dict[str, CapabilityKind] = {
    "light": CapabilityKind.LIGHT,
    "switch": CapabilityKind.SWITCH,
    "input_boolean": CapabilityKind.SWITCH,
    "scene": CapabilityKind.SCENE,
    "climate": CapabilityKind.CLIMATE,
    "cover": CapabilityKind.CURTAIN,
    "curtain": CapabilityKind.CURTAIN,
    "vacuum": CapabilityKind.VACUUM,
    "fan": CapabilityKind.AIR_PURIFIER,
    "humidifier": CapabilityKind.AIR_PURIFIER,
    "air_purifier": CapabilityKind.AIR_PURIFIER,
    "lock": CapabilityKind.LOCK,
    "camera": CapabilityKind.CAMERA,
    "sensor": CapabilityKind.SENSOR,
    "binary_sensor": CapabilityKind.SENSOR,
    "device_tracker": CapabilityKind.SENSOR,
    "weather": CapabilityKind.SENSOR,
}

#: Attributes that promote a capability beyond its domain default. A light that
#: reports brightness is a light *with* brightness, not a different device kind.
_ATTRIBUTE_CAPABILITIES: dict[str, CapabilityKind] = {
    "brightness": CapabilityKind.BRIGHTNESS,
    "color_temp": CapabilityKind.COLOR_TEMPERATURE,
    "color_temperature": CapabilityKind.COLOR_TEMPERATURE,
}


def capabilities_for_domain(
    domain: str, attributes: dict[str, Any] | None = None
) -> list[CapabilityKind]:
    """Capabilities implied by a domain plus the attributes it reports."""
    base = CAPABILITY_BY_DOMAIN.get(domain, CapabilityKind.UNKNOWN)
    out: list[CapabilityKind] = [base]
    for key in attributes or {}:
        extra = _ATTRIBUTE_CAPABILITIES.get(key)
        if extra is not None and extra not in out:
            out.append(extra)
    return out


@dataclass(frozen=True)
class PropertyDescriptor:
    """One normalized value on a device: what it is *and* what it reads right now."""

    name: str
    type: str
    #: Normalized current value. ``None`` when the integration did not report it.
    value: Any = None
    unit: str | None = None
    writable: bool = False
    minimum: float | None = None
    maximum: float | None = None
    values: tuple[str, ...] | None = None
    last_changed_at: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "unit": self.unit,
            "writable": self.writable,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "values": list(self.values) if self.values else None,
            "last_changed_at": self.last_changed_at,
        }


@dataclass(frozen=True)
class CommandDescriptor:
    """A named, schema-checked operation with an explicit risk."""

    name: str
    domain: str
    parameters: dict[str, Any] = field(default_factory=dict)
    risk: str = RISK_BLOCKED
    requires_approval: bool = True
    verification: str = "re_read_state"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "domain": self.domain,
            "parameters": self.parameters,
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "verification": self.verification,
        }


@dataclass(frozen=True)
class DeviceDescriptor:
    """Identity and static facts about one physical device."""

    device_id: str
    name: str
    provider_id: str
    domain: str
    capabilities: tuple[CapabilityKind, ...] = ()
    manufacturer: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    connection: str = "unknown"
    online: bool = True
    room: str | None = None
    entity_ids: tuple[str, ...] = ()

    @property
    def risk(self) -> str:
        return risk_for(self.domain)

    @property
    def is_read_only(self) -> bool:
        """True when the assistant may not write to this device at all."""
        return self.risk == RISK_BLOCKED

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "name": self.name,
            "provider_id": self.provider_id,
            "domain": self.domain,
            "capabilities": [c.value for c in self.capabilities],
            "manufacturer": self.manufacturer,
            "model": self.model,
            "firmware_version": self.firmware_version,
            "connection": self.connection,
            "online": self.online,
            "room": self.room,
            "entity_ids": list(self.entity_ids),
            "risk": self.risk,
            "read_only": self.is_read_only,
        }


@dataclass(frozen=True)
class DeviceState:
    """A device's current values.

    ``raw`` holds the adapter's untouched payload for diagnostics. It is namespaced
    by adapter kind so two adapters cannot be confused, and it is stripped by
    :func:`agent_view`.
    """

    device_id: str
    properties: dict[str, PropertyDescriptor] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    observed_at: int | None = None


def device_key_for(provider_id: str, entity: SmartEntity) -> str:
    """Stable composite key for an entity's *physical* device.

    Uses the integration's own device id when it publishes one, so renaming an
    entity never creates a second device and several entities of one lamp (its
    brightness, its energy sensor) collapse onto one key. Falls back to the
    external entity id only when the integration exposes no device id at all.
    """
    device_id = entity.attributes.get("device_id")
    if isinstance(device_id, str) and device_id.strip():
        return f"{provider_id}:{device_id.strip()}"
    return f"{provider_id}:entity:{entity.external_id}"


def _properties_for(entity: SmartEntity, *, writable: bool) -> dict[str, PropertyDescriptor]:
    """Normalize an entity's state + attributes into typed, valued properties."""
    properties: dict[str, PropertyDescriptor] = {}
    attributes = entity.attributes or {}
    changed = attributes.get("last_changed_ts")

    properties["state"] = PropertyDescriptor(
        name="state",
        type="string",
        value=entity.state or None,
        writable=writable,
        values=_state_enum(entity),
        last_changed_at=changed,
    )

    if "brightness" in attributes:
        properties["brightness"] = PropertyDescriptor(
            name="brightness",
            type="number",
            value=_as_float_or_none(attributes.get("brightness")),
            unit="%",
            writable=writable,
            minimum=0,
            maximum=_as_float(attributes.get("brightness_range_max"), 255),
            last_changed_at=changed,
        )
    if "color_temp" in attributes:
        properties["color_temperature"] = PropertyDescriptor(
            name="color_temperature",
            type="number",
            value=_as_float_or_none(attributes.get("color_temp")),
            unit="K",
            writable=writable,
            minimum=_as_float_or_none(attributes.get("min_color_temp")),
            maximum=_as_float_or_none(attributes.get("max_color_temp")),
            last_changed_at=changed,
        )
    for key, unit in (
        ("current_temperature", "°C"),
        ("temperature", "°C"),
        ("humidity", "%"),
        ("pm25", "µg/m³"),
        ("power", "W"),
    ):
        if key in attributes:
            properties[key] = PropertyDescriptor(
                name=key,
                type="number",
                value=_as_float_or_none(attributes.get(key)),
                unit=unit,
                last_changed_at=changed,
            )
    return properties


def _state_enum(entity: SmartEntity) -> tuple[str, ...] | None:
    domain = entity.domain
    if domain in ("switch", "input_boolean"):
        return ("on", "off", "unavailable", "unknown")
    if domain == "cover":
        return ("open", "closed", "opening", "closing", "unavailable", "unknown")
    if domain == "lock":
        return ("locked", "unlocked", "jammed", "unavailable", "unknown")
    if domain == "climate":
        return ("heat", "cool", "off", "heat_cool", "auto", "dry", "fan_only", "unavailable")
    return None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def describe_entity(
    entity: SmartEntity, *, provider_id: str, online: bool = True
) -> DeviceDescriptor:
    """Build a descriptor from one adapter entity."""
    attributes = entity.attributes or {}
    capabilities = capabilities_for_domain(entity.domain, attributes)
    return DeviceDescriptor(
        device_id=device_key_for(provider_id, entity),
        name=entity.name,
        provider_id=provider_id,
        domain=entity.domain,
        capabilities=tuple(capabilities),
        manufacturer=_clean(attributes.get("manufacturer")),
        model=_clean(attributes.get("model_name") or attributes.get("model")),
        firmware_version=_clean(attributes.get("sw_version")),
        connection=str(attributes.get("connection_type") or "unknown"),
        online=online,
        room=_clean(attributes.get("area") or attributes.get("friendly_name_area")),
        entity_ids=(entity.external_id,),
    )


def describe_state(
    entity: SmartEntity,
    *,
    provider_id: str,
    adapter_kind: str,
    observed_at: int | None = None,
) -> DeviceState:
    """Build a state projection, keeping raw payload namespaced by adapter."""
    writable = risk_for(entity.domain) != RISK_BLOCKED
    return DeviceState(
        device_id=device_key_for(provider_id, entity),
        properties=_properties_for(entity, writable=writable),
        raw={adapter_kind: dict(entity.attributes or {})},
        observed_at=observed_at,
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def agent_view(descriptor: DeviceDescriptor, state: DeviceState | None = None) -> dict[str, Any]:
    """The only projection an agent-facing surface may use.

    Strips the adapter's raw payload: an agent gets normalized, typed values
    and nothing about the vendor's internal representation.
    """
    view = descriptor.as_dict()
    if state is not None:
        view["properties"] = {
            name: prop.as_dict() for name, prop in sorted(state.properties.items())
        }
        view["observed_at"] = state.observed_at
    return view
