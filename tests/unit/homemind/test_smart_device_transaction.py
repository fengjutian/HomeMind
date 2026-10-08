"""``smart_device.command`` — a smart-home write must go through review.

The plan forbids any HTTP route that executes a device command without a
transaction, and forbids extending the paired-device ``device.command`` to
cover smart-home entities. These tests pin both halves of that rule.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from homemind.infra.errors import HomeMindError
from homemind.infra.family.transaction_actions.registry import (
    build_default_action_registry,
)
from homemind.infra.family.transaction_actions.smart_device import (
    SmartDeviceCommandHandler,
)


class _Repo:
    def __init__(self, entity: Any = None, provider: Any = None) -> None:
        self._entity = entity
        self._provider = provider

    def get_entity(self, entity_id: str) -> Any:
        return self._entity if self._entity and self._entity.id == entity_id else None

    def get_provider(self, provider_id: str) -> Any:
        return self._provider if self._provider and self._provider.id == provider_id else None


class _Manager:
    def __init__(self, repo: _Repo, catalog: list[Any] | None = None) -> None:
        self.repo = repo
        self._catalog = catalog or []
        self.executed: list[tuple[str, str, str, dict[str, Any]]] = []
        self.verified: list[tuple[str, str]] = []

    def command_catalog(self, provider: Any) -> list[Any]:
        return self._catalog

    def execute_command(
        self,
        provider: Any,
        entity: Any,
        command: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        self.executed.append((entity.id, command, idempotency_key, payload))
        return {"ok": True}

    def verify_command(
        self, provider: Any, entity: Any, command: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        self.verified.append((entity.id, command))
        return {"state": "on"}


def _entity(domain: str = "light", family_id: str = "fam1") -> Any:
    return SimpleNamespace(
        id="ent1",
        pk=1,
        family_id=family_id,
        provider_id="prov1",
        external_entity_id="light.lamp",
        domain=domain,
        name="Lamp",
    )


def _provider(enabled: bool = True) -> Any:
    return SimpleNamespace(id="prov1", family_id="fam1", kind="HOME_ASSISTANT", enabled=enabled)


def _ctx(family_id: str = "fam1") -> Any:
    return SimpleNamespace(
        family_id=family_id,
        requester=SimpleNamespace(id=7),
        transaction=SimpleNamespace(id="txn-1"),
    )


def _handler(
    domain: str = "light",
    *,
    commands: list[Any] | None = None,
    provider_enabled: bool = True,
    family_id: str = "fam1",
) -> tuple[SmartDeviceCommandHandler, _Manager]:
    repo = _Repo(entity=_entity(domain, family_id), provider=_provider(provider_enabled))
    catalog = (
        commands
        if commands is not None
        else [
            SimpleNamespace(name="turn_on", domain="light"),
            SimpleNamespace(name="turn_off", domain="light"),
        ]
    )
    manager = _Manager(repo, catalog)
    return SmartDeviceCommandHandler(manager), manager


class TestRegistration:
    def test_action_is_registered_alongside_device_command(self) -> None:
        registry = build_default_action_registry(
            smart_home=object(),  # type: ignore[arg-type]
        )
        actions = registry.actions()
        assert "smart_device.command" in actions

    def test_it_is_a_separate_action_from_device_command(self) -> None:
        registry = build_default_action_registry(
            devices=object(),  # type: ignore[arg-type]
            smart_home=object(),  # type: ignore[arg-type]
        )
        actions = registry.actions()
        assert "device.command" in actions
        assert "smart_device.command" in actions
        assert actions.count("smart_device.command") == 1

    def test_absent_when_no_smart_home_manager(self) -> None:
        registry = build_default_action_registry()
        assert "smart_device.command" not in registry.actions()


class TestValidate:
    def test_accepts_a_catalogued_command(self) -> None:
        handler, _ = _handler()
        handler.validate(_ctx(), {"entity_id": "ent1", "command": "turn_on"})

    def test_rejects_an_uncatalogued_command(self) -> None:
        handler, _ = _handler()
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx(), {"entity_id": "ent1", "command": "toggle_all"})
        assert "not a documented command" in str(excinfo.value)

    def test_rejects_an_unknown_entity(self) -> None:
        handler, _ = _handler()
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx(), {"entity_id": "nope", "command": "turn_on"})
        assert "not found" in str(excinfo.value)

    def test_rejects_a_cross_family_entity(self) -> None:
        handler, _ = _handler(family_id="fam2")
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx("fam1"), {"entity_id": "ent1", "command": "turn_on"})
        assert "not found" in str(excinfo.value)

    @pytest.mark.parametrize("domain", ["lock", "camera", "alarm_control_panel", "sensor"])
    def test_rejects_blocked_domains(self, domain: str) -> None:
        handler, _ = _handler(domain=domain)
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert "not writable" in str(excinfo.value)

    def test_rejects_an_unclassified_domain(self) -> None:
        """A domain nobody has classified must not become writable."""
        handler, _ = _handler(domain="some_new_domain")
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert "not writable" in str(excinfo.value)

    def test_rejects_a_disabled_provider(self) -> None:
        handler, _ = _handler(provider_enabled=False)
        with pytest.raises(HomeMindError) as excinfo:
            handler.validate(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert "disabled" in str(excinfo.value)

    def test_rejects_a_non_object_payload(self) -> None:
        handler, _ = _handler()
        with pytest.raises(HomeMindError):
            handler.validate(_ctx(), {"entity_id": "ent1", "command": "turn_on", "payload": ["x"]})


class TestExecuteAndVerify:
    def test_execute_passes_the_transaction_id_as_idempotency_key(self) -> None:
        handler, manager = _handler()
        out = handler.execute(
            _ctx(), {"entity_id": "ent1", "command": "turn_on", "payload": {"x": 1}}
        )
        assert manager.executed == [("ent1", "turn_on", "txn-1", {"x": 1})]
        assert out["result"] == {"ok": True}

    def test_verify_re_reads_the_entity(self) -> None:
        handler, manager = _handler()
        out = handler.verify(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert manager.verified == [("ent1", "turn_on")]
        assert out["confirmed"] == {"state": "on"}

    def test_preview_reports_risk_and_target(self) -> None:
        handler, _ = _handler()
        out = handler.preview(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert out["entity_name"] == "Lamp"
        assert out["domain"] == "light"
        assert out["risk"] == "LOW"

    def test_preview_marks_a_high_risk_domain(self) -> None:
        handler, _ = _handler(domain="cover")
        out = handler.preview(_ctx(), {"entity_id": "ent1", "command": "turn_on"})
        assert out["risk"] == "HIGH"
