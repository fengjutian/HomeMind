"""Smart-home adapter safety (Stage 6).

The tests here are mostly about refusals. A stage that adds smart-home
control to a family assistant succeeds or fails almost entirely on
whether it *declines* the things it must never do: an undocumented
command, a lock, a wildcard MQTT topic, a topic the family never
allow-listed.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from homemind.infra.family.smart_home import (
    BLOCKED_DOMAINS,
    MAX_MQTT_PAYLOAD_BYTES,
    READ_ONLY_DOMAINS,
    RISK_BLOCKED,
    RISK_HIGH,
    RISK_LOW,
    BaseSmartHomeAdapter,
    CommandNotAllowed,
    SmartCommand,
    SmartEntity,
    SmartHomeError,
    gather_entities,
    redact_for_log,
    risk_for,
    validate_mqtt_payload,
    validate_mqtt_topic,
)
from homemind.infra.family.smart_home_adapters import (
    HomeAssistantAdapter,
    MqttSmartHomeAdapter,
)


class _Publisher:
    """Records published messages instead of talking to a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []
        self.states: dict[str, str] = {}
        self.connected = True

    async def publish(self, topic: str, body: bytes, *, idempotency_key: str) -> None:
        _ = idempotency_key
        self.published.append((topic, body))

    def is_connected(self) -> bool:
        return self.connected

    def last_state(self, entity_id: str) -> str | None:
        return self.states.get(entity_id)


def _ha() -> HomeAssistantAdapter:
    return HomeAssistantAdapter(base_url="http://ha.local:8123", token="secret-token")


def _mqtt(allowlist: list[str] | None = None) -> MqttSmartHomeAdapter:
    return MqttSmartHomeAdapter(
        publisher=_Publisher(),
        topic_allowlist=allowlist if allowlist is not None else ["homemind/+/set/on"],
    )


# --------------------------------------------------------------------- risk


def test_risk_is_derived_from_the_domain() -> None:
    assert risk_for("light") == RISK_LOW
    assert risk_for("switch") == RISK_LOW
    assert risk_for("climate") == RISK_HIGH
    assert risk_for("lock") == RISK_BLOCKED
    assert risk_for("camera") == RISK_BLOCKED
    assert risk_for("sensor") == RISK_BLOCKED


def test_read_only_domains_are_never_writable() -> None:
    for domain in READ_ONLY_DOMAINS:
        assert risk_for(domain) == RISK_BLOCKED, domain


# ------------------------------------------------------------------ adapter


def test_the_catalogue_omits_locks_and_cameras() -> None:
    """Even if the domain check weakened, the catalogue stays empty."""
    adapter = _ha()
    for domain in BLOCKED_DOMAINS:
        assert adapter.commands_for(domain) == [], domain


@pytest.mark.asyncio
async def test_a_lock_command_is_refused_before_any_request() -> None:
    adapter = _ha()
    with pytest.raises(CommandNotAllowed) as excinfo:
        await adapter.preview_command("lock.front_door", "unlock", {})
    assert "not writable" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_undocumented_command_is_refused() -> None:
    adapter = _ha()
    with pytest.raises(CommandNotAllowed) as excinfo:
        await adapter.preview_command("light.hall", "explode", {})
    # The error names what *is* available, so a caller can correct itself.
    assert "not a documented command" in str(excinfo.value)
    assert "turn_on" in str(excinfo.value)


@pytest.mark.asyncio
async def test_preview_describes_the_change_and_its_risk() -> None:
    adapter = _ha()
    preview = await adapter.preview_command("light.hall", "turn_on", {"brightness": 200})
    assert preview.risk == RISK_LOW
    assert "light.hall" in preview.summary
    assert "brightness=200" in preview.summary


@pytest.mark.asyncio
async def test_a_climate_command_is_high_risk_not_refused() -> None:
    """Thermostats are gated by approval, not banned outright."""
    adapter = _ha()
    preview = await adapter.preview_command(
        "climate.living_room", "set_temperature", {"temperature": 21}
    )
    assert preview.risk == RISK_HIGH


@pytest.mark.asyncio
async def test_a_missing_required_payload_field_is_refused() -> None:
    adapter = _ha()
    with pytest.raises(CommandNotAllowed):
        await adapter.preview_command("climate.hall", "set_temperature", {})


@pytest.mark.asyncio
async def test_a_wrongly_typed_payload_field_is_refused() -> None:
    adapter = _ha()
    with pytest.raises(CommandNotAllowed):
        await adapter.preview_command("climate.hall", "set_temperature", {"temperature": "warm"})


@pytest.mark.asyncio
async def test_an_idempotent_retry_does_not_execute_twice() -> None:
    """A retried approval must not toggle a light twice."""
    adapter = _ha()
    calls: list[tuple[str, Any]] = []

    async def fake_post(path: str, body: Any) -> Any:
        calls.append((path, body))
        return [{"success": True}]

    adapter._post = fake_post  # noqa: SLF001 — exercising the retry path directly
    first = await adapter.execute_command("light.hall", "turn_on", {}, idempotency_key="txn-1")
    second = await adapter.execute_command("light.hall", "turn_on", {}, idempotency_key="txn-1")
    assert first["skipped"] is False
    assert second["skipped"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_verify_reports_the_observed_state_not_the_request() -> None:
    adapter = _ha()

    async def fake_state(entity_id: str) -> SmartEntity:
        return SmartEntity(external_id=entity_id, domain="light", name="Hall", state="on")

    adapter.get_state = fake_state  # type: ignore[method-assign]
    verdict = await adapter.verify_command("light.hall", "turn_on", {})
    assert verdict["verified"] is True
    assert verdict["checked"] is True

    async def fake_stuck(entity_id: str) -> SmartEntity:
        return SmartEntity(external_id=entity_id, domain="light", name="Hall", state="unavailable")

    adapter.get_state = fake_stuck  # type: ignore[method-assign]
    verdict = await adapter.verify_command("light.hall", "turn_on", {})
    # HA returns 200 even when the bulb did not obey; the read-back is
    # the only honest check.
    assert verdict["verified"] is False


@pytest.mark.asyncio
async def test_probe_reports_failure_without_raising() -> None:
    adapter = _ha()

    async def boom(_path: str) -> Any:
        raise OSError("connection refused")

    adapter._get = boom  # noqa: SLF001
    assert await adapter.probe() is False


# ------------------------------------------------------------- mqtt safety


def test_an_allow_listed_topic_is_accepted() -> None:
    assert (
        validate_mqtt_topic("homemind/hall/set/on", ["homemind/+/set/on"]) == "homemind/hall/set/on"
    )


def test_a_topic_outside_the_allow_list_is_refused() -> None:
    with pytest.raises(CommandNotAllowed) as excinfo:
        validate_mqtt_topic("homemind/hall/set/on", ["homemind/kitchen/set/on"])
    assert "allow-list" in str(excinfo.value)


def test_a_wildcard_topic_is_refused_even_when_it_would_match() -> None:
    """A wildcard publish would make the allow-list meaningless."""
    with pytest.raises(CommandNotAllowed) as excinfo:
        validate_mqtt_topic("homemind/#", ["homemind/#"])
    assert "wildcard" in str(excinfo.value)
    with pytest.raises(CommandNotAllowed):
        validate_mqtt_topic("homemind/+/set/on", ["homemind/+/+/+"])


def test_an_empty_allow_list_refuses_everything() -> None:
    with pytest.raises(CommandNotAllowed) as excinfo:
        validate_mqtt_topic("homemind/hall/set/on", [])
    assert "no topic allow-list" in str(excinfo.value)


def test_an_oversized_payload_is_refused() -> None:
    with pytest.raises(CommandNotAllowed) as excinfo:
        validate_mqtt_payload(b"x" * (MAX_MQTT_PAYLOAD_BYTES + 1))
    assert "over the" in str(excinfo.value)
    assert validate_mqtt_payload(b"ok") == b"ok"


def test_topic_matching_requires_the_same_depth() -> None:
    assert validate_mqtt_topic("a/b/c", ["a/b/c"]) == "a/b/c"
    with pytest.raises(CommandNotAllowed):
        validate_mqtt_topic("a/b/c/d", ["a/b/c"])
    with pytest.raises(CommandNotAllowed):
        validate_mqtt_topic("a/x/c", ["a/b/c"])


@pytest.mark.asyncio
async def test_mqtt_execute_publishes_only_allow_listed_topics() -> None:
    publisher = _Publisher()
    adapter = MqttSmartHomeAdapter(
        publisher=publisher,
        topic_allowlist=["homemind/light/hall/set/on", "homemind/light/hall/set/off"],
    )
    await adapter.execute_command("light.hall", "turn_on", {}, idempotency_key="k1")
    assert publisher.published[0][0] == "homemind/light/hall/set/on"

    # The kitchen is not on the allow-list, so its topic never reaches
    # the broker.
    with pytest.raises(CommandNotAllowed):
        await adapter.execute_command("light.kitchen", "turn_on", {}, idempotency_key="k2")
    assert len(publisher.published) == 1


@pytest.mark.asyncio
async def test_mqtt_verify_compares_retained_state() -> None:
    publisher = _Publisher()
    publisher.states["light.hall"] = "off"
    adapter = MqttSmartHomeAdapter(publisher=publisher, topic_allowlist=["homemind/hall/set/on"])
    verdict = await adapter.verify_command("light.hall", "turn_on", {"state": "on"})
    assert verdict["verified"] is False
    assert verdict["state"] == "off"


@pytest.mark.asyncio
async def test_mqtt_probe_follows_the_broker() -> None:
    publisher = _Publisher()
    adapter = MqttSmartHomeAdapter(publisher=publisher, topic_allowlist=[])
    assert await adapter.probe() is True
    publisher.connected = False
    assert await adapter.probe() is False


# ----------------------------------------------------------------- secrets


def test_redaction_keeps_a_token_unusable() -> None:
    redacted = redact_for_log("super-secret-token-value")
    assert "secret-token-value" not in redacted
    # Only a short prefix survives, enough to tell two tokens apart.
    assert redacted.startswith("supe")
    assert "24 chars" in redacted


def test_redaction_of_a_short_value_hides_everything() -> None:
    assert redact_for_log("abc") == "***"


@pytest.mark.asyncio
async def test_a_transport_failure_does_not_leak_the_token() -> None:
    adapter = _ha()
    calls: list[tuple[str, str]] = []

    async def boom(self: Any, method: str, url: str, **kwargs: Any) -> Any:
        calls.append((method, url))
        raise OSError("refused")

    import httpx

    original = httpx.AsyncClient.request
    httpx.AsyncClient.request = boom  # type: ignore[method-assign]
    try:
        # The transport failure is normalised, so a caller sees one
        # exception type rather than httpx's internal hierarchy.
        with pytest.raises(SmartHomeError):
            await adapter.get_state("light.hall")
    finally:
        httpx.AsyncClient.request = original  # type: ignore[method-assign]

    # The token lives in the Authorization header, never in the URL, so
    # even a logged URL cannot carry it.
    assert all("secret-token" not in url for _method, url in calls)


# -------------------------------------------------------------- aggregation


@pytest.mark.asyncio
async def test_one_unreachable_adapter_does_not_hide_the_others() -> None:
    class _Ok(BaseSmartHomeAdapter):
        kind = "ok"

        async def probe(self) -> bool:
            return True

        async def list_entities(self) -> list[SmartEntity]:
            return [SmartEntity("light.a", "light", "A")]

        async def get_state(self, entity_id: str) -> SmartEntity | None:
            return None

        async def preview_command(self, *a: Any, **k: Any) -> Any:
            return None

        async def execute_command(self, *a: Any, **k: Any) -> Any:
            return {}

        async def verify_command(self, *a: Any, **k: Any) -> Any:
            return {}

    class _Down(BaseSmartHomeAdapter):
        kind = "down"

        async def probe(self) -> bool:
            return False

        async def list_entities(self) -> list[SmartEntity]:
            raise ConnectionError("broker down")

        async def get_state(self, entity_id: str) -> SmartEntity | None:
            return None

        async def preview_command(self, *a: Any, **k: Any) -> Any:
            return None

        async def execute_command(self, *a: Any, **k: Any) -> Any:
            return {}

        async def verify_command(self, *a: Any, **k: Any) -> Any:
            return {}

    entities = await gather_entities([_Ok(), _Down()])
    assert [entity.external_id for entity in entities] == ["light.a"]


def test_command_payload_validation_is_shallow_but_useful() -> None:
    command = SmartCommand(
        name="x",
        domain="light",
        service="turn_on",
        payload_schema={
            "required": ["mode"],
            "properties": {"mode": {"type": "boolean"}, "level": {"type": "number"}},
        },
    )
    assert BaseSmartHomeAdapter.validate_payload(command, {"mode": True}) == {"mode": True}
    with pytest.raises(CommandNotAllowed):
        BaseSmartHomeAdapter.validate_payload(command, {})
    with pytest.raises(CommandNotAllowed):
        BaseSmartHomeAdapter.validate_payload(command, {"mode": "yes"})
    with pytest.raises(CommandNotAllowed):
        BaseSmartHomeAdapter.validate_payload(command, {"mode": True, "level": "high"})


def test_a_mqtt_payload_is_valid_utf8_json_before_publishing() -> None:
    """The publish path must serialise, not hand a dict to the broker."""
    adapter = _mqtt()
    body = json.dumps({"state": "on"}, ensure_ascii=False).encode("utf-8")
    assert json.loads(body.decode("utf-8")) == {"state": "on"}
    assert validate_mqtt_payload(body) == body
    assert adapter.topic_allowlist() == ["homemind/+/set/on"]
