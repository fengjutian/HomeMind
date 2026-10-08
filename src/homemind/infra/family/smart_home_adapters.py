"""Home Assistant and MQTT adapters (Stage 6).

Two transports, one contract. Everything about *whether* a command may
run lives in :mod:`homemind.infra.family.smart_home`; what is left here
is talking to the wire.

Credentials come from the secret store by reference. No password, token
or username is ever written into a log line, an audit row, or a
notification — :func:`~homemind.infra.family.smart_home.redact_for_log`
is used at every boundary where a value could otherwise escape.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from homemind.infra.family.smart_home import (
    BaseSmartHomeAdapter,
    CommandNotAllowed,
    CommandPreview,
    SmartCommand,
    SmartEntity,
    SmartHomeError,
    redact_for_log,
    validate_mqtt_payload,
    validate_mqtt_topic,
)

logger = logging.getLogger(__name__)

#: Give an outbound request a wall-clock budget. A smart-home call that
#: hangs must not hold a transaction open until its lease expires.
DEFAULT_TIMEOUT_SECONDS = 10.0


#: The documented command catalogue. Locks, cameras and alarm panels are
#: absent on purpose: ``smart_home`` refuses them at the domain level, and
#: leaving them out means they are unreachable even if that check weakens.
HA_COMMANDS: tuple[SmartCommand, ...] = (
    SmartCommand(
        name="turn_on",
        domain="light",
        service="turn_on",
        description="Switch a light on",
        payload_schema={
            "type": "object",
            "properties": {
                "brightness": {"type": "number"},
                "transition": {"type": "number"},
            },
        },
    ),
    SmartCommand(
        name="turn_off",
        domain="light",
        service="turn_off",
        description="Switch a light off",
        payload_schema={
            "type": "object",
            "properties": {"transition": {"type": "number"}},
        },
    ),
    SmartCommand(
        name="toggle",
        domain="light",
        service="toggle",
        description="Flip a light to the opposite state",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_on",
        domain="switch",
        service="turn_on",
        description="Switch a switch on",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_off",
        domain="switch",
        service="turn_off",
        description="Switch a switch off",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="activate",
        domain="scene",
        service="turn_on",
        description="Activate a scene",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="set_temperature",
        domain="climate",
        service="set_temperature",
        description="Set a target temperature (always requires approval)",
        payload_schema={
            "type": "object",
            "required": ["temperature"],
            "properties": {"temperature": {"type": "number"}},
        },
    ),
    SmartCommand(
        name="set_hvac_mode",
        domain="climate",
        service="set_hvac_mode",
        description="Set the HVAC mode (always requires approval)",
        payload_schema={
            "type": "object",
            "required": ["hvac_mode"],
            "properties": {
                "hvac_mode": {
                    "type": "string",
                    "enum": ["heat", "cool", "off", "heat_cool", "auto", "dry", "fan_only"],
                }
            },
        },
    ),
    # --- cover: curtains, blinds, garage doors -------------------------------
    # HIGH risk: a cover opening is a physical act with privacy and
    # wake-up consequences, so it always goes through approval.
    SmartCommand(
        name="open_cover",
        domain="cover",
        service="open_cover",
        description="Open a cover (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="close_cover",
        domain="cover",
        service="close_cover",
        description="Close a cover (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="stop_cover",
        domain="cover",
        service="stop_cover",
        description="Stop a cover mid-travel (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="set_cover_position",
        domain="cover",
        service="set_cover_position",
        description="Move a cover to a percentage (always requires approval)",
        payload_schema={
            "type": "object",
            "required": ["position"],
            "properties": {"position": {"type": "number"}},
        },
    ),
    # --- fan: ceiling fans and air circulators ------------------------------
    SmartCommand(
        name="turn_on",
        domain="fan",
        service="turn_on",
        description="Switch a fan on (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_off",
        domain="fan",
        service="turn_off",
        description="Switch a fan off (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="set_fan_percentage",
        domain="fan",
        service="set_percentage",
        description="Set a fan speed percentage (always requires approval)",
        payload_schema={
            "type": "object",
            "required": ["percentage"],
            "properties": {"percentage": {"type": "number"}},
        },
    ),
    # --- vacuum --------------------------------------------------------------
    # Start / pause / return are the low-to-medium risk commands the plan
    # allows. Map and camera control are deliberately absent: a camera moving
    # through the home is not something an agent gets.
    SmartCommand(
        name="start",
        domain="vacuum",
        service="start",
        description="Start a cleaning cycle (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="pause",
        domain="vacuum",
        service="pause",
        description="Pause a cleaning cycle (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="return_to_base",
        domain="vacuum",
        service="return_to_base",
        description="Send the vacuum back to its dock (always requires approval)",
        payload_schema={"type": "object", "properties": {}},
    ),
    # --- humidifier ----------------------------------------------------------
    SmartCommand(
        name="set_humidity",
        domain="humidifier",
        service="set_humidity",
        description="Set a target relative humidity (always requires approval)",
        payload_schema={
            "type": "object",
            "required": ["humidity"],
            "properties": {"humidity": {"type": "number"}},
        },
    ),
)


class HomeAssistantAdapter(BaseSmartHomeAdapter):
    """REST access to a Home Assistant instance.

    Only the documented command catalogue is exposed. There is no
    ``call_service`` passthrough on purpose: an agent that could name any
    service could unlock a door the catalogue deliberately omits.
    """

    kind = "home_assistant"

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        commands: tuple[SmartCommand, ...] | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self.commands = commands if commands is not None else HA_COMMANDS
        self._timeout = timeout_seconds
        # command_idempotency_key -> (entity, command). Lets a retried
        # approval return the first result instead of toggling twice.
        self._seen: dict[str, tuple[str, str]] = {}

    async def probe(self) -> bool:
        try:
            await self._get("/api/")
        except Exception as exc:  # noqa: BLE001 — a probe reports, never raises
            logger.info(
                "HomeAssistant: probe failed for %s: %s",
                redact_for_log(self._base_url),
                type(exc).__name__,
            )
            return False
        return True

    async def list_entities(self) -> list[SmartEntity]:
        states = await self._get("/api/states")
        entities: list[SmartEntity] = []
        for row in states or []:
            if not isinstance(row, dict) or "entity_id" not in row:
                continue
            entities.append(
                SmartEntity(
                    external_id=str(row["entity_id"]),
                    domain=str(row.get("domain") or str(row["entity_id"]).split(".", 1)[0]),
                    name=str(row.get("attributes", {}).get("friendly_name") or row["entity_id"]),
                    state=str(row.get("state", "")),
                    attributes=dict(row.get("attributes") or {}),
                )
            )
        return entities

    async def get_state(self, entity_id: str) -> SmartEntity | None:
        row = await self._get(f"/api/states/{entity_id}")
        if not isinstance(row, dict):
            return None
        return SmartEntity(
            external_id=entity_id,
            domain=str(row.get("domain") or entity_id.split(".", 1)[0]),
            name=str(row.get("attributes", {}).get("friendly_name") or entity_id),
            state=str(row.get("state", "")),
            attributes=dict(row.get("attributes") or {}),
        )

    async def preview_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> CommandPreview:
        domain = _domain_of(entity_id)
        risk = self._check_allowed(domain)
        command = self._resolve_command(domain, command_name)
        self.validate_payload(command, payload)
        summary = f"{command.name} → {entity_id}"
        if payload:
            summary += " " + ", ".join(f"{key}={value}" for key, value in sorted(payload.items()))
        return CommandPreview(
            entity_external_id=entity_id,
            command_name=command.name,
            domain=domain,
            risk=risk,
            summary=summary,
            payload=dict(payload),
        )

    async def execute_command(
        self,
        entity_id: str,
        command_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        domain = _domain_of(entity_id)
        self._check_allowed(domain)
        command = self._resolve_command(domain, command_name)
        self.validate_payload(command, payload)

        previous = self._seen.get(idempotency_key)
        if previous == (entity_id, command.name):
            # A retry after a lost response must not fire twice.
            logger.info(
                "HomeAssistant: idempotency key %s already applied, not repeating",
                redact_for_log(idempotency_key),
            )
            return {"skipped": True, "reason": "already_applied", "state": None}

        body = {"entity_id": entity_id, **payload}
        result = await self._post(f"/api/services/{domain}/{command.service}", body)
        self._seen[idempotency_key] = (entity_id, command.name)
        return {"skipped": False, "result": result}

    async def verify_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Read the entity back rather than trusting the POST.

        Home Assistant returns a success body whether or not the device
        obeyed, so the only honest verification is the state afterwards.
        """
        domain = _domain_of(entity_id)
        command = self._resolve_command(domain, command_name)
        entity = await self.get_state(entity_id)
        if entity is None:
            return {"verified": False, "reason": "entity_not_found"}
        expected = _expected_state(command, payload)
        if expected is None:
            # No deterministic expectation: report what it is now and
            # let the caller decide.
            return {"verified": True, "state": entity.state, "checked": False}
        return {
            "verified": entity.state.lower() == expected.lower(),
            "state": entity.state,
            "expected": expected,
            "checked": True,
        }

    async def _get(self, path: str) -> Any:
        return await self._request("GET", path, None)

    async def _post(self, path: str, body: Any) -> Any:
        return await self._request("POST", path, body)

    async def _request(self, method: str, path: str, body: Any) -> Any:
        import httpx  # noqa: PLC0415 — keeps import cost off module load

        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._token}"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.request(method, url, headers=headers, json=body)
                response.raise_for_status()
                return response.json()
        except TimeoutError as exc:
            raise SmartHomeError("Home Assistant did not answer in time") from exc
        except Exception as exc:  # noqa: BLE001 — transport detail stays in the log
            # The URL can carry a host name; the token never appears.
            logger.warning(
                "HomeAssistant: %s %s failed: %s",
                method,
                redact_for_log(url, keep=24),
                type(exc).__name__,
            )
            raise SmartHomeError(f"Home Assistant request failed: {type(exc).__name__}") from exc


def _domain_of(entity_id: str) -> str:
    domain, _, _ = entity_id.partition(".")
    if not domain:
        raise CommandNotAllowed(f"{entity_id!r} is not a valid entity id")
    return domain


def _expected_state(command: SmartCommand, payload: dict[str, Any]) -> str | None:
    """What the entity should read after ``command``.

    Only defined for commands whose effect is a single, checkable
    state. Anything else returns ``None`` so verification reports the
    observed state instead of inventing an expectation.
    """
    if command.name == "turn_on":
        return "on"
    if command.name == "turn_off":
        return "off"
    if command.name == "toggle":
        return None
    if "brightness" in payload:
        return str(payload["brightness"])
    return None


#: The documented command catalogue. Locks, cameras and alarm panels are
#: absent on purpose: :mod:`smart_home` refuses them at the domain level,
#: and leaving them out of the catalogue means they are unreachable even
#: if that check is ever weakened.


class MqttSmartHomeAdapter(BaseSmartHomeAdapter):
    """Command a generic MQTT broker through an allow-listed topic set.

    The allow-list belongs to the family, not the agent: an agent may
    only publish to topics an administrator configured. Publishing to a
    wildcard is refused outright, because a wildcard would make the
    allow-list meaningless.
    """

    kind = "mqtt"

    def __init__(
        self,
        *,
        publisher: Any,
        topic_allowlist: list[str],
        commands: tuple[SmartCommand, ...] | None = None,
    ) -> None:
        self._publisher = publisher
        self._topic_allowlist = list(topic_allowlist)
        self.commands = commands if commands is not None else MQTT_COMMANDS

    def topic_allowlist(self) -> list[str]:
        return list(self._topic_allowlist)

    def set_topic_allowlist(self, topics: list[str]) -> None:
        self._topic_allowlist = list(topics)

    async def probe(self) -> bool:
        checker = getattr(self._publisher, "is_connected", None)
        if callable(checker):
            return bool(checker())
        return True

    async def list_entities(self) -> list[SmartEntity]:
        # An MQTT broker has no entity model of its own; entities come
        # from what the family has mapped onto its topics.
        return []

    async def get_state(self, entity_id: str) -> SmartEntity | None:
        getter = getattr(self._publisher, "last_state", None)
        if not callable(getter):
            return None
        payload = getter(entity_id)
        if payload is None:
            return None
        return SmartEntity(
            external_id=entity_id,
            domain=_domain_of(entity_id),
            name=entity_id,
            state=str(payload),
        )

    async def preview_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> CommandPreview:
        domain = _domain_of(entity_id)
        risk = self._check_allowed(domain)
        command = self._resolve_command(domain, command_name)
        self.validate_payload(command, payload)
        topic = self._topic_for(entity_id, command)
        summary = f"publish {topic}"
        if payload:
            summary += " " + ", ".join(f"{key}={value}" for key, value in sorted(payload.items()))
        return CommandPreview(
            entity_external_id=entity_id,
            command_name=command.name,
            domain=domain,
            risk=risk,
            summary=summary,
            payload={"topic": topic, **payload},
        )

    async def execute_command(
        self,
        entity_id: str,
        command_name: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        domain = _domain_of(entity_id)
        self._check_allowed(domain)
        command = self._resolve_command(domain, command_name)
        self.validate_payload(command, payload)
        topic = self._topic_for(entity_id, command)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        validate_mqtt_payload(body)
        await self._publisher.publish(topic, body, idempotency_key=idempotency_key)
        return {"skipped": False, "topic": topic}

    async def verify_command(
        self, entity_id: str, command_name: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Compare the broker's retained state with the intended one.

        MQTT has no acknowledgement that a device acted, so the only
        honest check is what the topic now reads.
        """
        entity = await self.get_state(entity_id)
        if entity is None:
            return {"verified": False, "reason": "no_retained_state"}
        expected = payload.get("state")
        if expected is None:
            return {"verified": True, "state": entity.state, "checked": False}
        return {
            "verified": entity.state.lower() == str(expected).lower(),
            "state": entity.state,
            "expected": str(expected),
            "checked": True,
        }

    def _topic_for(self, entity_id: str, command: SmartCommand) -> str:
        """Build and check the topic for a command.

        Validated *before* anything is published, so a topic outside the
        allow-list can never reach the broker even once.
        """
        leaf = MQTT_LEAVES.get(command.name, command.name)
        # ``homemind/<domain>/<name>/<leaf>``: the prefix is fixed so an
        # agent cannot reach a topic outside the HomeMind namespace by
        # naming an entity differently.
        domain, _, name = entity_id.partition(".")
        topic = f"homemind/{domain}/{name or entity_id}/{leaf}"
        return validate_mqtt_topic(topic, self._topic_allowlist)


MQTT_LEAVES: dict[str, str] = {
    "turn_on": "set/on",
    "turn_off": "set/off",
    "toggle": "set/toggle",
}


MQTT_COMMANDS: tuple[SmartCommand, ...] = (
    SmartCommand(
        name="turn_on",
        domain="light",
        service="publish",
        description="Publish an on command",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_off",
        domain="light",
        service="publish",
        description="Publish an off command",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="toggle",
        domain="light",
        service="publish",
        description="Publish a toggle command",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_on",
        domain="switch",
        service="publish",
        description="Publish an on command",
        payload_schema={"type": "object", "properties": {}},
    ),
    SmartCommand(
        name="turn_off",
        domain="switch",
        service="publish",
        description="Publish an off command",
        payload_schema={"type": "object", "properties": {}},
    ),
)


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "HomeAssistantAdapter", "MqttSmartHomeAdapter"]
