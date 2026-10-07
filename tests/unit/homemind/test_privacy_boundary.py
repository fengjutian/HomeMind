"""Stage 12 acceptance tests for the external provider boundary.

Covers the spec's rules:

* ``SENSITIVE`` assets are not sent externally by default,
* ``LOCAL_ONLY`` blocks every outbound call,
* ``ASK_EACH_TIME`` creates a pending request instead of sending,
* a provider outside the allow-list is refused,
* an empty allow-list means *none*, not *all*,
* the audit row records no key and no original image bytes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.manager import FamilyManager, MemberRole
from homemind.infra.family.privacy import (
    DataCategory,
    ExternalOperation,
    ExternalProcessingGuard,
    ProcessingMode,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def _bootstrap(tmp_path: Path):
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1)"
        )
    owner = User(id=1, username="papa", role=Role.USER, display_name="爸爸")
    other = User(id=2, username="mama", role=Role.USER, display_name="妈妈")
    family_repo = FamilyRepo(pool)
    manager = FamilyManager(family_repo)
    family = manager.create_family(
        owner, name="Happy", timezone="Asia/Shanghai", locale="zh",
    )
    services = HomeMindServices.from_pool(pool)
    return pool, ExternalProcessingGuard(services), manager, owner, other, family.id


# ------------------------------------------------------------------ defaults


def test_default_settings_are_cautious(tmp_path: Path) -> None:
    """A family that never configured a policy must start locked
    down, not open."""

    pool, guard, _manager, _owner, _other, family_id = _bootstrap(tmp_path)
    settings = guard.get_settings(family_id)
    assert settings.processing_mode is ProcessingMode.ASK_EACH_TIME
    assert settings.allow_external_vision is False
    assert settings.allow_sensitive_external is False
    assert settings.allowed_provider_ids == frozenset()
    pool.close()


# ------------------------------------------------------------- local only


def test_local_only_blocks_everything(tmp_path: Path) -> None:
    pool, guard, manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allow_external_embedding=True,
        allow_external_geocoding=True,
        allowed_provider_ids=["openai"],
    )
    # Prove it is allowed before flipping the switch.
    allowed = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
    )
    assert allowed.allowed

    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.LOCAL_ONLY,
        allow_external_vision=True,
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
    )
    assert denied.allowed is False
    assert "LOCAL_ONLY" in denied.reason
    pool.close()


def test_local_only_clears_the_provider_allow_list(tmp_path: Path) -> None:
    pool, guard, manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=["openai"],
    )
    guard.update_settings(
        family_id, owner, processing_mode=ProcessingMode.LOCAL_ONLY,
    )
    settings = guard.get_settings(family_id)
    assert settings.allowed_provider_ids == frozenset(), (
        "a stale provider entry must not survive a LOCAL_ONLY switch"
    )
    pool.close()


# ------------------------------------------------------------ allow-list


def test_provider_outside_allow_list_is_refused(tmp_path: Path) -> None:
    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=["openai"],
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="rogue-provider",
    )
    assert denied.allowed is False
    assert "allow-list" in denied.reason
    pool.close()


def test_empty_allow_list_means_none(tmp_path: Path) -> None:
    """An empty list must fail closed, not behave as a wildcard."""

    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allowed_provider_ids=[],
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
    )
    assert denied.allowed is False
    pool.close()


def test_disabled_operation_is_refused(tmp_path: Path) -> None:
    """Vision off, embedding on — a vision call must still fail."""

    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=False,
        allow_external_embedding=True,
        allowed_provider_ids=["openai"],
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
    )
    assert denied.allowed is False
    assert "VISION" in denied.reason

    ok = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.EMBEDDING,
        provider_id="openai",
    )
    assert ok.allowed
    pool.close()


# -------------------------------------------------------------- sensitivity


def test_sensitive_data_needs_explicit_opt_in(tmp_path: Path) -> None:
    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_vision=True,
        allow_sensitive_external=False,
        allowed_provider_ids=["openai"],
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
        data_categories=frozenset({DataCategory.HEALTH.value}),
    )
    assert denied.allowed is False
    assert "sensitive" in denied.reason

    guard.update_settings(family_id, owner, allow_sensitive_external=True)
    allowed = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
        data_categories=frozenset({DataCategory.HEALTH.value}),
    )
    assert allowed.allowed
    pool.close()


def test_minor_data_is_treated_as_sensitive(tmp_path: Path) -> None:
    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ALLOW_EXTERNAL,
        allow_external_embedding=True,
        allowed_provider_ids=["openai"],
    )
    denied = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.EMBEDDING,
        provider_id="openai",
        data_categories=frozenset({DataCategory.MINOR.value}),
    )
    assert denied.allowed is False
    pool.close()


# ------------------------------------------------------------ ask each time


def test_ask_each_time_creates_a_pending_request(tmp_path: Path) -> None:
    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ASK_EACH_TIME,
        allow_external_vision=True,
        allowed_provider_ids=["openai"],
    )
    decision = guard.authorize_external_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
    )
    assert decision.allowed is True
    assert decision.needs_approval is True
    assert decision.request_id

    pending = guard.list_requests(family_id, owner, status="PENDING")
    assert len(pending) == 1
    assert pending[0]["request_id"] == decision.request_id

    decided = guard.decide_request(
        family_id, decision.request_id, owner, approve=True,
    )
    assert decided["status"] == "APPROVED"
    pool.close()


def test_non_manager_cannot_decide_a_request(tmp_path: Path) -> None:
    from octop.infra.errors import OctopError  # noqa: PLC0415

    pool, guard, manager, owner, other, family_id = _bootstrap(tmp_path)
    manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.MEMBER, user_id=2,
    )
    guard.update_settings(
        family_id, owner,
        processing_mode=ProcessingMode.ASK_EACH_TIME,
        allow_external_vision=True,
        allowed_provider_ids=["openai"],
    )
    decision = guard.authorize_external_call(
        family_id, owner, operation=ExternalOperation.VISION, provider_id="openai",
    )
    assert decision.request_id is not None
    with pytest.raises(OctopError):
        guard.decide_request(
            family_id, decision.request_id, other, approve=True,
        )
    pool.close()


def test_non_manager_cannot_change_settings(tmp_path: Path) -> None:
    from octop.infra.errors import OctopError  # noqa: PLC0415

    pool, guard, manager, owner, other, family_id = _bootstrap(tmp_path)
    manager.create_member(
        family_id, owner, display_name="妈妈", role=MemberRole.MEMBER, user_id=2,
    )
    with pytest.raises(OctopError):
        guard.update_settings(
            family_id, other, allow_external_vision=True,
        )
    pool.close()


# -------------------------------------------------------------------- audit


def test_audit_records_no_key_or_image_data(tmp_path: Path) -> None:
    pool, guard, _manager, owner, _other, family_id = _bootstrap(tmp_path)
    guard.audit_call(
        family_id, owner,
        operation=ExternalOperation.VISION,
        provider_id="openai",
        asset_id="asset_1",
        data_categories=frozenset({DataCategory.PHOTO.value}),
        model="gpt-4o",
        result={"ok": True, "description": "a family photo"},
    )
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM homemind_external_processing_audit"
        ).fetchall()
    assert len(rows) == 1
    blob = str(dict(rows[0]))
    assert "sk-" not in blob
    assert "api_key" not in blob.lower()
    assert "authorization" not in blob.lower()
    assert "a family photo" in blob  # the outcome is recorded
    pool.close()
