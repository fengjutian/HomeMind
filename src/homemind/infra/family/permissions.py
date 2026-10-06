"""Centralized family permission evaluator.

This module owns the single source of truth for whether a user may execute a
family-scoped action against a space, asset, or memory. Domain code (assets,
filesystem, context, transactions, tools) must delegate to
``FamilyPermissionEvaluator`` rather than reimplementing ad-hoc checks.

Evaluation order (most specific wins; ties broken by
``DENY > REQUIRE_CONFIRMATION > ALLOW``):

1. Non-family member → ``DENY`` (admins bypass).
2. Platform admin → ``ALLOW`` (admins keep their existing bypass).
3. Family Owner / Admin: any ``family.*`` management action is allowed except
   ``family.delete`` which still requires confirmation.
4. Asset lives in another member's private space → ``DENY``.
5. Exact member + exact space rule (or exact member + global, global + exact,
   global + global), sorted by specificity and effect priority.
7. Action default risk:

   - ``family.read.*`` / ``family.search.*`` → ``ALLOW``
   - ``filesystem.read`` → ``ALLOW``
   - ``task.create`` → ``ALLOW``
   - ``event.create`` / ``memory.create`` → ``REQUIRE_CONFIRMATION``
   - ``filesystem.copy`` / ``move`` / ``rename`` / ``delete`` → ``REQUIRE_CONFIRMATION``
   - ``family.delete`` → ``REQUIRE_CONFIRMATION``
   - ``family.permission.*`` → ``DENY`` (admins only)
   - everything else → ``DENY``
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from homemind.infra.db.repos.families import (
    FamilyPermissionRow,
    FamilyRepo,
)

if TYPE_CHECKING:
    from octop.infra.users.identity import User


class PermissionEffect(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


# Conflict-resolution weight; higher value wins when multiple rules match.
_EFFECT_PRIORITY: dict[str, int] = {
    PermissionEffect.ALLOW.value: 0,
    PermissionEffect.REQUIRE_CONFIRMATION.value: 1,
    PermissionEffect.DENY.value: 2,
}


# Default risk per action when no permission row matches.
# Order matters: longest / most specific prefix first so that
# ``family.read.*`` wins over the catch-all deny at the end.
DEFAULT_ACTION_EFFECTS: tuple[tuple[str, PermissionEffect], ...] = (
    ("family.read.", PermissionEffect.ALLOW),
    ("family.search.", PermissionEffect.ALLOW),
    ("filesystem.read", PermissionEffect.ALLOW),
    ("task.create", PermissionEffect.ALLOW),
    ("event.create", PermissionEffect.REQUIRE_CONFIRMATION),
    ("memory.create", PermissionEffect.REQUIRE_CONFIRMATION),
    ("filesystem.copy", PermissionEffect.REQUIRE_CONFIRMATION),
    ("filesystem.move", PermissionEffect.REQUIRE_CONFIRMATION),
    ("filesystem.rename", PermissionEffect.REQUIRE_CONFIRMATION),
    ("filesystem.delete", PermissionEffect.REQUIRE_CONFIRMATION),
    ("family.delete", PermissionEffect.REQUIRE_CONFIRMATION),
    ("family.permission.", PermissionEffect.DENY),
    # Anything not listed above (including unknown actions) defaults to DENY.
)


def default_effect_for(action: str) -> PermissionEffect:
    """Return the built-in default effect for ``action`` when no rule matches."""
    for prefix, effect in DEFAULT_ACTION_EFFECTS:
        if action.startswith(prefix):
            return effect
    return PermissionEffect.DENY


@dataclass(frozen=True)
class PermissionDecision:
    effect: PermissionEffect
    action: str
    family_id: str
    member_id: str | None
    space_id: str | None
    matched_permission_ids: list[str] = field(default_factory=list)
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.effect is PermissionEffect.ALLOW

    @property
    def requires_confirmation(self) -> bool:
        return self.effect is PermissionEffect.REQUIRE_CONFIRMATION


class _AssetLike(Protocol):
    """Minimal asset shape required for private-space pre-checks."""

    space_id: str | None
    visibility: str | None


class FamilyPermissionEvaluator:
    """Resolve a single, deterministic permission decision for a family action.

    Parameters
    ----------
    repo:
        Family repository providing members, permissions, spaces, and
        family metadata.
    """

    def __init__(self, repo: FamilyRepo) -> None:
        self.repo = repo

    def evaluate(
        self,
        *,
        family_id: str,
        user: "User",
        action: str,
        space_id: str | None = None,
        asset: _AssetLike | None = None,
        now: int | None = None,
    ) -> PermissionDecision:
        timestamp = int(time.time()) if now is None else now

        family = self.repo.get_family(family_id)
        if family is None:
            return PermissionDecision(
                effect=PermissionEffect.DENY,
                action=action,
                family_id=family_id,
                member_id=None,
                space_id=space_id,
                reason="family_not_found",
            )

        membership = self.repo.get_membership(family_id, user.id)
        member_id = str(membership["member_id"]) if membership is not None else None
        is_owner = bool(membership and str(membership["role"]) == "OWNER")
        is_admin_manager = bool(
            membership and str(membership["role"]) in {"OWNER", "ADMIN"}
        )

        # Step 1: non-members (non-admins) are denied outright.
        if membership is None and not user.is_admin:
            return PermissionDecision(
                effect=PermissionEffect.DENY,
                action=action,
                family_id=family_id,
                member_id=None,
                space_id=space_id,
                reason="not_family_member",
            )

        # Step 2: platform admins keep their existing bypass.
        if user.is_admin:
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                action=action,
                family_id=family_id,
                member_id=member_id,
                space_id=space_id,
                reason="admin_bypass",
            )

        # Step 3: family manager scope. Owners/Admins may run any
        # family.* management action EXCEPT family.delete, which still
        # needs confirmation.
        if is_admin_manager and action.startswith("family.") and action != "family.delete":
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                action=action,
                family_id=family_id,
                member_id=member_id,
                space_id=space_id,
                reason="family_manager_scope",
            )

        # Step 4: guard assets that live in another member's private space.
        if asset is not None and asset.space_id is not None and asset.space_id != space_id:
            space = self.repo.get_space(asset.space_id)
            if (
                space is not None
                and space.space_type == "PRIVATE"
                and space.owner_member_id != member_id
                and not is_owner
            ):
                return PermissionDecision(
                    effect=PermissionEffect.DENY,
                    action=action,
                    family_id=family_id,
                    member_id=member_id,
                    space_id=asset.space_id,
                    reason="private_space_not_owned",
                )

        # Step 5: rule lookup with explicit specificity priority.
        decision = self._match_rule(
            family_id=family_id,
            member_id=member_id,
            action=action,
            space_id=space_id,
            timestamp=timestamp,
        )
        if decision is not None:
            return decision

        # Final fallback: action default risk.
        default = default_effect_for(action)
        # Owner still needs confirmation for family.delete.
        if is_owner and action == "family.delete":
            default = PermissionEffect.REQUIRE_CONFIRMATION
        return PermissionDecision(
            effect=default,
            action=action,
            family_id=family_id,
            member_id=member_id,
            space_id=space_id,
            reason="default_risk",
        )

    # ------------------------------------------------------------------ helpers

    def _match_rule(
        self,
        *,
        family_id: str,
        member_id: str | None,
        action: str,
        space_id: str | None,
        timestamp: int,
    ) -> PermissionDecision | None:
        rules = [
            permission
            for permission in self.repo.list_permissions(family_id)
            if permission.action == action
            and (permission.expires_at is None or permission.expires_at > timestamp)
            and self._scope_matches(permission, member_id, space_id)
        ]
        if not rules:
            return None

        def _sort_key(rule: FamilyPermissionRow) -> tuple[int, int]:
            specificity = (
                0 if rule.subject_member_id is not None else 1,
                0 if rule.space_id is not None else 1,
            )
            return (
                specificity,
                -_EFFECT_PRIORITY.get(rule.effect, 0),
            )

        rules.sort(key=_sort_key)
        winner = rules[0]
        return PermissionDecision(
            effect=PermissionEffect(winner.effect),
            action=action,
            family_id=family_id,
            member_id=member_id,
            space_id=space_id,
            matched_permission_ids=[winner.id],
            reason="matched_rule",
        )

    @staticmethod
    def _scope_matches(
        permission: FamilyPermissionRow,
        member_id: str | None,
        space_id: str | None,
    ) -> bool:
        if (
            permission.subject_member_id is not None
            and permission.subject_member_id != member_id
        ):
            return False
        if permission.space_id is not None and permission.space_id != space_id:
            return False
        return True


__all__ = [
    "DEFAULT_ACTION_EFFECTS",
    "FamilyPermissionEvaluator",
    "PermissionDecision",
    "PermissionEffect",
    "default_effect_for",
]