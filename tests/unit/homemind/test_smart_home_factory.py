"""Adapter assembly: credential resolution must make the link reachable
without ever leaking the credential (plan phase 5)."""

from __future__ import annotations

import pytest

from homemind.infra.db.repos.smart_home import (
    PROVIDER_KIND_HOME_ASSISTANT,
    PROVIDER_KIND_MQTT,
    SmartProviderRow,
)
from homemind.infra.family.smart_home_factory import build_adapter, resolve_secret


class _Store:
    def __init__(self, values: dict[str, bytes] | None = None) -> None:
        self.values = values or {}
        self.seen: list[str] = []

    def get(self, key: str) -> bytes | None:
        self.seen.append(key)
        return self.values.get(key)


def _provider(**kw: object) -> SmartProviderRow:
    base: dict[str, object] = {
        "id": "prov1",
        "pk": 1,
        "family_id": "fam1",
        "kind": PROVIDER_KIND_HOME_ASSISTANT,
        "name": "HA",
        "base_url": "http://ha.local:8123/",
        "secret_ref": "ha-token",
        "topic_allowlist": [],
        "enabled": True,
        "last_seen_at": None,
        "last_error": None,
        "last_sync_at": None,
        "last_success_at": None,
        "last_error_code": None,
        "reauth_required": False,
        "rate_limited_until": None,
        "created_by": 1,
        "created_at": 0,
        "updated_at": 0,
    }
    base.update(kw)
    return SmartProviderRow(**base)  # type: ignore[arg-type]


class TestSecretResolution:
    def test_returns_none_without_a_reference(self) -> None:
        store = _Store()
        assert resolve_secret(store, None) is None
        assert store.seen == [], "must not touch the store when no ref is set"

    def test_resolves_a_stored_reference(self) -> None:
        store = _Store({"ha-token": b"s3cret"})
        assert resolve_secret(store, "ha-token") == "s3cret"

    def test_missing_secret_raises_rather_than_syncing_empty(self) -> None:
        store = _Store({})
        with pytest.raises(LookupError):
            resolve_secret(store, "deleted-ref")

    def test_blank_reference_is_treated_as_absent(self) -> None:
        assert resolve_secret(_Store(), "") is None


class TestBuildAdapter:
    def test_builds_home_assistant_adapter_from_ref(self) -> None:
        store = _Store({"ha-token": b"tok"})
        adapter = build_adapter(_provider(), store)
        assert adapter is not None
        assert adapter.kind == "home_assistant"
        assert store.seen == ["ha-token"]

    def test_credential_is_not_written_into_the_provider_row(self) -> None:
        provider = _provider()
        store = _Store({"ha-token": b"ZZ-raw-credential-value"})
        build_adapter(provider, store)
        assert provider.secret_ref == "ha-token", "row keeps the reference, not the value"
        assert "ZZ-raw-credential-value" not in repr(provider)

    def test_disabled_provider_builds_nothing(self) -> None:
        assert build_adapter(_provider(enabled=False), _Store({"ha-token": b"t"})) is None

    def test_unknown_kind_builds_nothing(self) -> None:
        assert build_adapter(_provider(kind="MATTER"), _Store({"ha-token": b"t"})) is None

    def test_missing_base_url_builds_nothing(self) -> None:
        assert build_adapter(_provider(base_url=None), _Store({"ha-token": b"t"})) is None

    def test_missing_credential_reference_builds_nothing(self) -> None:
        assert build_adapter(_provider(secret_ref=None), _Store()) is None

    def test_dangling_credential_reference_raises(self) -> None:
        with pytest.raises(LookupError):
            build_adapter(_provider(), _Store({}))

    def test_mqtt_without_publisher_builds_nothing(self) -> None:
        provider = _provider(kind=PROVIDER_KIND_MQTT, topic_allowlist=["homemind/light/+/set"])
        assert build_adapter(provider, _Store()) is None

    def test_mqtt_without_allowlist_builds_nothing(self) -> None:
        provider = _provider(kind=PROVIDER_KIND_MQTT, topic_allowlist=[])
        assert build_adapter(provider, _Store(), mqtt_publisher=lambda _p: object()) is None

    def test_mqtt_builds_with_publisher_and_allowlist(self) -> None:
        class _Pub:
            async def publish(self, topic: str, payload: bytes) -> None:
                return None

        provider = _provider(kind=PROVIDER_KIND_MQTT, topic_allowlist=["homemind/light/+/set"])
        adapter = build_adapter(provider, _Store(), mqtt_publisher=lambda _p: _Pub())
        assert adapter is not None
        assert adapter.kind == "mqtt"
        assert adapter.commands_for("light"), "the MQTT catalogue must still apply"
