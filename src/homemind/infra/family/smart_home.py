"""Smart-home adapters (Stage 6).

HomeMind does not want to own the house. A family already has Home
Assistant, or an MQTT broker; this stage reads state from the system
that already is and writes back **only through a reviewed Family
Transaction**. That framing decides everything below.

**The adapter surface is deliberately narrow.** ``probe``,
``list_entities``, ``get_state``, ``preview_command``, ``execute_command``,
``verify_command``. There is no "run arbitrary service call" and no
"publish to arbitrary topic", because those two methods are what would
let an agent drive a house with no catalogue and no approval. An agent
picks a *named command from a catalog*; the adapter maps it.

**Risk is a property of the domain, not the payload.** Turning off a
light is low risk. A lock, a camera, a thermostat, a medical device is
not merely higher risk — several are refused outright, because a family
that wires up their front door to an LLM assistant has not made a
reasonable decision.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Domains this stage refuses outright, whatever the family asks for.
# A lock or a camera is a security boundary; an assistant that can open
# one is a liability, not a feature.
BLOCKED_DOMAINS: frozenset[str] = frozenset({"lock", "camera", "alarm_control_panel"})

# Domains allowed only behind an approval.
HIGH_RISK_DOMAINS: frozenset[str] = frozenset({"climate", "cover", "fan", "humidifier"})

# Domains whose writes may proceed without approval, subject to family
# policy.
LOW_RISK_DOMAINS: frozenset[str] = frozenset({"light", "switch", "input_boolean", "scene"})

#: Nothing here may be executed at all. Listed explicitly so adding a
#: domain to the adapter cannot quietly make it writable.
READ_ONLY_DOMAINS: frozenset[str] = frozenset(
    {"sensor", "binary_sensor", "device_tracker", "sun", "weather", "person", "zone"}
)

RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"
RISK_BLOCKED = "BLOCKED"


class SmartHomeError(RuntimeError):
    """The adapter could not complete a request."""


class CommandNotAllowed(SmartHomeError):
    """The requested command is not in the catalog, or is forbidden."""


def risk_for(domain: str) -> str:
    """Risk of writing to ``domain``.

    Derived from the domain alone, never from the request: a payload
    cannot argue its way into a lower risk than the thing it acts on.
    """
    if domain in BLOCKED_DOMAINS:
        return RISK_BLOCKED
    if domain in HIGH_RISK_DOMAINS:
        return RISK_HIGH
    if domain in READ_ONLY_DOMAINS:
        return RISK_BLOCKED
    return RISK_LOW


@dataclass(frozen=True)
class SmartCommand:
    """One entry in an adapter's command catalogue.

    An agent selects a command by name; it never supplies a service
    name, a topic, or a payload shape of its own.
    """

    name: str
    domain: str
    service: str
    description: str = ""
    payload_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SmartCommand:
        return cls(
            name=str(raw.get("name", "")),
            domain=str(raw.get("domain", "")),
            service=str(raw.get("service", "")),
            description=str(raw.get("description", "")),
            payload_schema=dict(raw.get("payload_schema") or {}),
        )


@dataclass(frozen=True)
class SmartEntity:
    """One entity as the external system reports it."""

    external_id: str
    domain: str
    name: str
    state: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def risk(self) -> str:
        return risk_for(self.domain)

    @property
    def is_read_only(self) -> bool:
        return self.domain in READ_ONLY_DOMAINS


@dataclass(frozen=True)
class CommandPreview:
    """A user-readable description of what a command would do."""

    entity_external_id: str
    command_name: str
    domain: str
    risk: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SmartHomeAdapter(Protocol):
    """What a smart-home integration has to be able to do."""

    kind: str

    async def probe(self) -> bool:
        """True when the connection works. Never raises for a plain outage."""
        ...

    async def list_entities(self) -> list[SmartEntity]:
        """Every entity the external system exposes."""
        ...

    async def get_state(self, entity_id: str) -> SmartEntity | None:
        """One entity's current state, or ``None`` if it is gone."""
        ...

    def commands_for(self, domain: str) -> list[SmartCommand]:
        """The catalogue for a domain.

        Returning the catalogue rather than accepting a free-form
        service call is what makes "the agent may only use documented
        commands" enforceable rather than aspirational.
        """
        ...

    async def preview_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> CommandPreview: ...

    async def execute_command(
        self,
        entity_id: str,
        command_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Perform the command.

        ``idempotency_key`` lets the adapter drop a duplicate submit
        after a retry, so a retried approval does not toggle a switch
        twice.
        """
        ...

    async def verify_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-read the entity and confirm the effect landed."""
        ...


class BaseSmartHomeAdapter:
    """Shared catalogue and safety checks.

    Subclasses supply the transport; everything that decides *whether*
    a command may run lives here so two adapters cannot disagree about
    what is allowed.
    """

    kind = "base"

    #: Commands this adapter understands. Populated by the subclass.
    commands: tuple[SmartCommand, ...] = ()

    def commands_for(self, domain: str) -> list[SmartCommand]:
        return [command for command in self.commands if command.domain == domain]

    def _resolve_command(self, domain: str, command_name: str) -> SmartCommand:
        """Find a command, refusing anything not in the catalogue."""
        for command in self.commands_for(domain):
            if command.name == command_name:
                return command
        allowed = ", ".join(sorted(c.name for c in self.commands_for(domain)))
        raise CommandNotAllowed(
            f"{command_name!r} is not a documented command for {domain}; "
            f"available: {allowed or 'none'}"
        )

    def _check_allowed(self, domain: str) -> str:
        """Return the risk, or refuse outright.

        Refusal here rather than at approval time: a blocked domain is
        blocked whether or not somebody approves it.
        """
        risk = risk_for(domain)
        if risk == RISK_BLOCKED:
            reason = (
                "read-only domain"
                if domain in READ_ONLY_DOMAINS
                else "domain is not enabled for assistant control in this release"
            )
            raise CommandNotAllowed(f"{domain} is not writable: {reason}")
        return risk

    @staticmethod
    def validate_payload(command: SmartCommand, payload: dict[str, Any]) -> dict[str, Any]:
        """Reject a payload that does not match the command's schema.

        Deliberately shallow — required keys and primitive types. The
        point is to stop an agent inventing fields, not to become a JSON
        Schema engine.
        """
        schema = command.payload_schema or {}
        required = schema.get("required") or []
        for key in required:
            if key not in payload:
                raise CommandNotAllowed(f"{command.name} requires {key!r}")
        properties = schema.get("properties") or {}
        for key, value in payload.items():
            expected = properties.get(key, {}).get("type")
            if expected == "boolean" and not isinstance(value, bool):
                raise CommandNotAllowed(f"{command.name}: {key!r} must be a boolean")
            if expected == "number" and not isinstance(value, (int, float)):
                raise CommandNotAllowed(f"{command.name}: {key!r} must be a number")
        return payload


# ------------------------------------------------------------- mqtt safety


#: Refuse an absurd payload before it reaches the broker. A light command
#: is tens of bytes; megabytes means something is wrong.
MAX_MQTT_PAYLOAD_BYTES = 8 * 1024

#: Wildcards are how "publish to anything" happens by accident.
MQTT_FORBIDDEN_TOPIC_CHARS = ("#", "+")


def validate_mqtt_topic(topic: str, allowlist: list[str]) -> str:
    """Check a topic against the family's allow-list.

    A topic containing ``#`` or ``+`` is refused outright even if the
    allow-list would match it: a wildcard publish is never what a family
    meant, and allowing it would defeat the allow-list entirely.
    """
    if any(token in topic for token in MQTT_FORBIDDEN_TOPIC_CHARS):
        raise CommandNotAllowed(f"wildcard characters are not allowed in a topic: {topic!r}")
    if not topic or len(topic.encode("utf-8")) > MAX_MQTT_PAYLOAD_BYTES:
        raise CommandNotAllowed("topic is empty or implausibly long")
    if not allowlist:
        raise CommandNotAllowed("no topic allow-list is configured for this provider")
    for pattern in allowlist:
        if _topic_matches(pattern, topic):
            return topic
    raise CommandNotAllowed(f"topic {topic!r} is not on the allow-list")


def _topic_matches(pattern: str, topic: str) -> bool:
    """Match one MQTT level, honouring ``+`` inside an allow-list entry.

    The *allow-list* may use ``+`` to mean "one level"; the published
    topic may not. That asymmetry is the point: the family writes the
    pattern, the agent never does.
    """
    pattern_parts = pattern.split("/")
    topic_parts = topic.split("/")
    if len(pattern_parts) != len(topic_parts):
        return False
    for expected, actual in zip(pattern_parts, topic_parts, strict=True):
        if expected == "+":
            if actual == "":
                return False
            continue
        if expected != actual:
            return False
    return True


def validate_mqtt_payload(payload: bytes) -> bytes:
    if len(payload) > MAX_MQTT_PAYLOAD_BYTES:
        raise CommandNotAllowed(
            f"payload is {len(payload)} bytes, over the {MAX_MQTT_PAYLOAD_BYTES} limit"
        )
    return payload


def redact_for_log(value: str, *, keep: int = 4) -> str:
    """A log-safe form of a credential or payload.

    Only the first few characters survive, which is enough to tell two
    tokens apart in a log line and not enough to use one.
    """
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}…({len(value)} chars)"


def dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


async def gather_entities(adapters: list[SmartHomeAdapter]) -> list[SmartEntity]:
    """Read every adapter concurrently.

    One unreachable broker must not hide the state of the others, so a
    failing adapter contributes nothing rather than failing the read.
    """
    results = await asyncio.gather(
        *(adapter.list_entities() for adapter in adapters),
        return_exceptions=True,
    )
    entities: list[SmartEntity] = []
    for adapter, result in zip(adapters, results, strict=True):
        if isinstance(result, BaseException):
            logger.warning("SmartHome: %s could not list entities: %s", adapter.kind, result)
            continue
        entities.extend(result)
    return entities


__all__ = [
    "BLOCKED_DOMAINS",
    "HIGH_RISK_DOMAINS",
    "LOW_RISK_DOMAINS",
    "MAX_MQTT_PAYLOAD_BYTES",
    "MQTT_FORBIDDEN_TOPIC_CHARS",
    "READ_ONLY_DOMAINS",
    "RISK_BLOCKED",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "BaseSmartHomeAdapter",
    "CommandNotAllowed",
    "CommandPreview",
    "SmartCommand",
    "SmartEntity",
    "SmartHomeAdapter",
    "SmartHomeError",
    "dumps",
    "gather_entities",
    "redact_for_log",
    "risk_for",
    "validate_mqtt_payload",
    "validate_mqtt_topic",
]
