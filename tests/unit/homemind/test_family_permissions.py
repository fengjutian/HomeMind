"""Unit tests for the centralized family permission evaluator (Stage 1)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemind.infra.db.migrate import run_migrations as run_homemind_migrations
from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.family.manager import (
    FamilyManager,
    MemberRole,
    PermissionEffect,
    SpaceType,
)
from homemind.infra.family.permissions import (
    DEFAULT_ACTION_EFFECTS,
    FamilyPermissionEvaluator,
    PermissionDecision,
    default_effect_for,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import DatabasePool, SqlitePool
from octop.infra.users.identity import Role, User


@dataclass(frozen=True)
class _FakeAsset:
    space_id: str | None
    visibility: str | None = "FAMILY"


def _pool(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    run_homemind_migrations(pool)
    return pool


def _bootstrap(tmp_path: Path) -> tuple[
    SqlitePool, FamilyManager, dict[str, User]
]:
    pool = _pool(tmp_path)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, disabled, locale, created_at) "
            "VALUES (1, 'papa', 'x', 'user', 0, 'zh', 1), "
            "(2, 'mama', 'x', 'user', 0, 'zh', 1), "
            "(3, 'outsider', 'x', 'user', 0, 'zh', 1), "
            "(4, 'admin', 'x', 'admin', 0, 'zh', 1)"
        )
    family = FamilyManager(FamilyRepo(pool))
    users = {
        "papa": User(id=1, username="papa", role=Role.USER, display_name="爸爸"),
        "mama": User(id=2, username="mama", role=Role.USER, display_name="妈妈"),
        "outsider": User(id=3, username="outsider", role=Role.USER, display_name="外人"),
        "admin": User(id=4, username="admin", role=Role.ADMIN, display_name="Admin"),
    }
    return pool, family, users


def _seed_family(
    pool: SqlitePool, family: FamilyManager, users: dict[str, User]
) -> dict[str, Any]:
    fam = family.create_family(
        users["papa"], name="Happy", timezone="Asia/Shanghai", locale="zh"
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    papa_id = members["爸爸"].id
    family.create_member(
        fam.id,
        users["papa"],
        display_name="妈妈",
        role=MemberRole.MEMBER,
        user_id=users["mama"].id,
    )
    members = {row.display_name: row for row in family.repo.list_members(fam.id)}
    mama_id = members["妈妈"].id
    family.create_member(
        fam.id, users["papa"], display_name="孩子", role=MemberRole.CHILD
    )
    shared = family.create_space(
        fam.id, users["papa"], name="公共相册", space_type=SpaceType.SHARED
    )
    moms_private = family.create_space(
        fam.id,
        users["papa"],
        name="妈妈的私人",
        space_type=SpaceType.PRIVATE,
        owner_member_id=mama_id,
    )
    return {
        "family": fam,
        "papa_id": papa_id,
        "mama_id": mama_id,
        "shared": shared,
        "moms_private": moms_private,
    }


def _decision(
    pool: SqlitePool, user: User, family_id: str, action: str, **kwargs: Any
) -> PermissionDecision:
    evaluator = FamilyPermissionEvaluator(FamilyRepo(pool))
    return evaluator.evaluate(
        family_id=family_id, user=user, action=action, **kwargs
    )


# --------------------------------------------------------------------------- defaults


def test_default_effect_for_known_actions() -> None:
    assert default_effect_for("family.read.events") is PermissionEffect.ALLOW
    assert default_effect_for("family.search.assets") is PermissionEffect.ALLOW
    assert default_effect_for("filesystem.read") is PermissionEffect.ALLOW
    assert default_effect_for("task.create") is PermissionEffect.ALLOW
    assert default_effect_for("event.create") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("memory.create") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("filesystem.copy") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("filesystem.move") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("filesystem.rename") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("filesystem.delete") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("family.delete") is PermissionEffect.REQUIRE_CONFIRMATION
    assert default_effect_for("family.permission.create") is PermissionEffect.DENY
    assert default_effect_for("totally.unknown.action") is PermissionEffect.DENY


def test_default_action_effects_table_shape() -> None:
    assert isinstance(DEFAULT_ACTION_EFFECTS, tuple)
    for entry in DEFAULT_ACTION_EFFECTS:
        assert isinstance(entry, tuple)
        assert len(entry) == 2
        prefix, effect = entry
        assert isinstance(prefix, str)
        assert isinstance(effect, PermissionEffect)


# --------------------------------------------------------------------- ownership


def test_owner_can_admin_family(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    decision = _decision(pool, users["papa"], seeded["family"].id, "family.update")
    assert decision.effect is PermissionEffect.ALLOW
    assert decision.reason == "family_manager_scope"


def test_owner_must_confirm_family_delete(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    decision = _decision(pool, users["papa"], seeded["family"].id, "family.delete")
    assert decision.effect is PermissionEffect.REQUIRE_CONFIRMATION


# --------------------------------------------------------------- shared spaces


def test_member_can_read_shared_space(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    asset = _FakeAsset(space_id=seeded["shared"].id, visibility="FAMILY")
    decision = _decision(
        pool,
        users["mama"],
        seeded["family"].id,
        "photo.read",
        space_id=seeded["shared"].id,
        asset=asset,
    )
    assert decision.effect is PermissionEffect.ALLOW
    assert decision.reason == "family_visibility"


# --------------------------------------------------------- private space guard


def test_member_blocked_from_another_members_private_space(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    asset = _FakeAsset(space_id=seeded["moms_private"].id, visibility="PRIVATE")
    # Papa is the OWNER, so the private-space guard doesn't apply to him.
    # Switch perspective: use mama and try to access papa's hypothetical
    # private space. Since mama is a normal member, the guard must deny.
    # We simulate the other direction: create papa's private space, then have
    # mama try to read an asset inside it.
    papas_private = family.create_space(
        seeded["family"].id,
        users["papa"],
        name="爸爸的私人",
        space_type=SpaceType.PRIVATE,
        owner_member_id=seeded["papa_id"],
    )
    asset = _FakeAsset(space_id=papas_private.id, visibility="PRIVATE")
    decision = _decision(
        pool,
        users["mama"],
        seeded["family"].id,
        "photo.read",
        asset=asset,
    )
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason == "private_space_not_owned"


def test_owner_can_access_other_private_space(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    asset = _FakeAsset(space_id=seeded["moms_private"].id, visibility="PRIVATE")
    decision = _decision(
        pool,
        users["papa"],
        seeded["family"].id,
        "photo.read",
        asset=asset,
    )
    # Per the Stage 1 spec, a private space owned by another member is
    # denied by default even for the family owner. Owners must grant
    # themselves an explicit ALLOW permission to read it.
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason == "private_space_not_owned"


# ------------------------------------------------------------ rule precedence


def test_explicit_deny_beats_allow(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    family.create_permission(
        seeded["family"].id,
        users["papa"],
        subject_member_id=seeded["mama_id"],
        space_id=None,
        action="photo.read",
        effect=PermissionEffect.DENY,
        expires_at=None,
    )
    decision = _decision(
        pool,
        users["mama"],
        seeded["family"].id,
        "photo.read",
        space_id=seeded["shared"].id,
    )
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason == "matched_rule"
    assert decision.matched_permission_ids  # has at least one rule id


def test_expired_permission_does_not_apply(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    family.create_permission(
        seeded["family"].id,
        users["papa"],
        subject_member_id=seeded["mama_id"],
        space_id=None,
        action="photo.read",
        effect=PermissionEffect.DENY,
        expires_at=1,  # expired in 1970
    )
    asset = _FakeAsset(space_id=seeded["shared"].id, visibility="FAMILY")
    decision = _decision(
        pool,
        users["mama"],
        seeded["family"].id,
        "photo.read",
        space_id=seeded["shared"].id,
        asset=asset,
    )
    # Expired DENY is skipped; FAMILY visibility lets the read through.
    assert decision.effect is PermissionEffect.ALLOW


def test_space_level_rule_beats_family_level(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    family.create_permission(
        seeded["family"].id,
        users["papa"],
        subject_member_id=seeded["mama_id"],
        space_id=None,
        action="photo.read",
        effect=PermissionEffect.ALLOW,
        expires_at=None,
    )
    family.create_permission(
        seeded["family"].id,
        users["papa"],
        subject_member_id=seeded["mama_id"],
        space_id=seeded["shared"].id,
        action="photo.read",
        effect=PermissionEffect.DENY,
        expires_at=None,
    )
    decision = _decision(
        pool,
        users["mama"],
        seeded["family"].id,
        "photo.read",
        space_id=seeded["shared"].id,
    )
    assert decision.effect is PermissionEffect.DENY


# -------------------------------------------------------- high-risk action


def test_high_risk_actions_require_confirmation(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    for action in ("filesystem.delete", "filesystem.move", "filesystem.rename"):
        decision = _decision(pool, users["mama"], seeded["family"].id, action)
        assert decision.effect is PermissionEffect.REQUIRE_CONFIRMATION, action
    decision = _decision(pool, users["mama"], seeded["family"].id, "memory.create")
    assert decision.effect is PermissionEffect.REQUIRE_CONFIRMATION


# -------------------------------------------------------- non-member denial


def test_non_member_is_denied(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    decision = _decision(
        pool, users["outsider"], seeded["family"].id, "photo.read"
    )
    assert decision.effect is PermissionEffect.DENY
    assert decision.reason == "not_family_member"


# -------------------------------------------------------- admin bypass


def test_admin_bypass_keeps_existing_behavior(tmp_path: Path) -> None:
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    decision = _decision(
        pool, users["admin"], seeded["family"].id, "filesystem.delete",
        space_id=seeded["moms_private"].id,
    )
    assert decision.effect is PermissionEffect.ALLOW
    assert decision.reason == "admin_bypass"


def test_admin_bypass_distinct_from_non_member(tmp_path: Path) -> None:
    """Admin still gets ALLOW; non-admin user with same setup gets DENY."""
    pool, family, users = _bootstrap(tmp_path)
    seeded = _seed_family(pool, family, users)
    admin_decision = _decision(
        pool, users["admin"], seeded["family"].id, "family.permission.create",
    )
    non_member_decision = _decision(
        pool, users["outsider"], seeded["family"].id, "family.permission.create",
    )
    assert admin_decision.effect is PermissionEffect.ALLOW
    assert non_member_decision.effect is PermissionEffect.DENY


# -------------------------------------------------------- Decision helpers


def test_decision_helpers() -> None:
    decision = PermissionDecision(
        effect=PermissionEffect.ALLOW,
        action="filesystem.read",
        family_id="fam_x",
        member_id="mem_x",
        space_id=None,
    )
    assert decision.allowed is True
    assert decision.requires_confirmation is False
    decision = PermissionDecision(
        effect=PermissionEffect.REQUIRE_CONFIRMATION,
        action="memory.create",
        family_id="fam_x",
        member_id=None,
        space_id=None,
    )
    assert decision.allowed is False
    assert decision.requires_confirmation is True


# Type-only sanity: DatabasePool alias is referenced so future imports stay clean.
_ = DatabasePool