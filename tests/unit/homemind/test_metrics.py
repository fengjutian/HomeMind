"""Stage 12: tests for the in-process HomeMind metrics."""

from __future__ import annotations

from pathlib import Path

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.family_invites import FamilyInviteRepo
from homemind.infra.family.invites import FamilyInviteManager
from homemind.infra.family.manager import FamilyManager
from homemind.infra.metrics import HomeMindMetrics, METRICS
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.users.identity import Role, User


def test_inc_increments_counter() -> None:
    metrics = HomeMindMetrics()
    metrics.inc("permission_allow_total")
    metrics.inc("permission_allow_total", 4)
    assert metrics.permission_allow_total == 5


def test_inc_rejects_unknown_name() -> None:
    import pytest

    metrics = HomeMindMetrics()
    with pytest.raises(AttributeError):
        metrics.inc("not_a_real_counter")


def test_snapshot_returns_all_counters() -> None:
    snapshot = METRICS.snapshot()
    assert "permission_allow_total" in snapshot
    assert "transaction_plan_total" in snapshot
    assert "memory_candidate_create_total" in snapshot
    assert "asset_scan_sweep_total" in snapshot
    assert "device_heartbeat_total" in snapshot
    assert "invite_mint_total" in snapshot
    assert snapshot["permission_allow_total"] >= 0


def test_reset_clears_all_counters() -> None:
    metrics = HomeMindMetrics()
    metrics.inc("permission_allow_total", 7)
    metrics.reset()
    assert metrics.permission_allow_total == 0
    assert metrics.transaction_plan_total == 0


def test_invite_metrics_track_lifecycle(tmp_path: Path) -> None:
    from homemind.infra.db.repos.families import FamilyRepo

    METRICS.reset()

    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'owner', 'x', 'user', 0, 'zh', 1), "
            "(2, 'redeemer', 'x', 'user', 0, 'zh', 1)"
        )
    family_manager = FamilyManager(FamilyRepo(pool))
    invite_manager = FamilyInviteManager(family_manager, FamilyInviteRepo(pool))
    owner = User(id=1, username="owner", role=Role.USER, display_name="Owner")
    redeemer = User(id=2, username="redeemer", role=Role.USER, display_name="R")
    family = family_manager.create_family(
        owner, name="M", timezone="Asia/Shanghai", locale="zh",
    )

    token = invite_manager.create_invite(
        family.id, owner, display_name="Child",
    )
    assert METRICS.invite_mint_total == 1

    invite_manager.redeem(token.token, redeemer)
    assert METRICS.invite_redeem_total == 1
    assert METRICS.invite_rejected_total == 0

    # Bad token increments rejected counter.
    import pytest
    from homemind.infra.errors import HomeMindError

    with pytest.raises(HomeMindError):
        invite_manager.redeem("nope", redeemer)
    assert METRICS.invite_rejected_total == 1
    pool.close()
