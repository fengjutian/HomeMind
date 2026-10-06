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


# Actions that always require confirmation, even when an explicit
# permission row grants ALLOW. This preserves the existing product rule
# that destructive filesystem operations and durable-memory writes cannot
# proceed without an additional human approval step.
_FORCE_CONFIRMATION_ACTIONS: frozenset[str] = frozenset(
    {
        "filesystem.copy",
        "filesystem.move",
        "filesystem.rename",
        "filesystem.delete",
        "event.create",
        "memory.create",
        "family.delete",
    }
)


def _apply_force_confirmation(
    effect: PermissionEffect, action: str
) -> PermissionEffect:
    """Bump ALLOW → REQUIRE_CONFIRMATION for destructive actions.

    DENY is never overridden: a destructive action that is explicitly denied
    must stay denied. family.delete is force-confirmed regardless of role.
    """
    if effect is PermissionEffect.ALLOW and action in _FORCE_CONFIRMATION_ACTIONS:
        return PermissionEffect.REQUIRE_CONFIRMATION
    return effect


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
    ("memory.read", PermissionEffect.ALLOW),
    ("photo.read", PermissionEffect.ALLOW),
    ("event.read", PermissionEffect.ALLOW),
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
    """Minimal asset / memory shape required for visibility and
    private-space pre-checks. Either attribute may be missing for callers
    that pass memory-like objects (no space) or asset-like objects
    (no visibility at the protocol level)."""

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
        # ``family.*`` administrative action EXCEPT family.delete, which
        # still needs confirmation. Asset reads/mutations resolve through
        # the normal rule pipeline below.
        if is_admin_manager and action.startswith("family.") and action != "family.delete":
            return PermissionDecision(
                effect=_apply_force_confirmation(PermissionEffect.ALLOW, action),
                action=action,
                family_id=family_id,
                member_id=member_id,
                space_id=space_id,
                reason="family_manager_scope",
            )

        # Step 4: guard assets that live in another member's private space.
        asset_space_id = getattr(asset, "space_id", None) if asset is not None else None
        asset_visibility = (
            getattr(asset, "visibility", None) if asset is not None else None
        )
        # When ``space_id`` itself points at a private space the current
        # member doesn't own, deny up front. This lets callers pass the
        # space id directly (e.g. "filesystem.read in space X") without
        # also having to materialize the asset object.
        if space_id is not None:
            space = self.repo.get_space(space_id)
            if (
                space is not None
                and space.space_type == "PRIVATE"
                and space.owner_member_id != member_id
            ):
                return PermissionDecision(
                    effect=PermissionEffect.DENY,
                    action=action,
                    family_id=family_id,
                    member_id=member_id,
                    space_id=space_id,
                    reason="private_space_not_owned",
                )
        if asset_space_id is not None and asset_space_id != space_id:
            space = self.repo.get_space(asset_space_id)
            if (
                space is not None
                and space.space_type == "PRIVATE"
                and space.owner_member_id != member_id
            ):
                return PermissionDecision(
                    effect=PermissionEffect.DENY,
                    action=action,
                    family_id=family_id,
                    member_id=member_id,
                    space_id=asset_space_id,
                    reason="private_space_not_owned",
                )

        # Step 5: PRIVATE asset in a space the current member owns is allowed.
        if asset_visibility == "PRIVATE":
            space = self.repo.get_space(asset_space_id) if asset_space_id else None
            if space is not None and space.owner_member_id == member_id:
                return PermissionDecision(
                    effect=PermissionEffect.ALLOW,
                    action=action,
                    family_id=family_id,
                    member_id=member_id,
                    space_id=asset_space_id,
                    reason="private_space_owner",
                )

        # Step 6: PUBLIC and FAMILY-visibility assets are open to any family member.
        if asset_visibility in {"PUBLIC", "FAMILY"}:
            return PermissionDecision(
                effect=PermissionEffect.ALLOW,
                action=action,
                family_id=family_id,
                member_id=member_id,
                space_id=asset_space_id,
                reason="family_visibility",
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
        default = _apply_force_confirmation(default_effect_for(action), action)
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
        effect = _apply_force_confirmation(PermissionEffect(winner.effect), action)
        return PermissionDecision(
            effect=effect,
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

    def can_see_candidate(
        self,
        family_id: str,
        user: "User",
        candidate: Any,
    ) -> bool:
        """Visibility filter for ``MemoryCandidateRow``.

        Candidates about a ``MEMBER`` whose private space the caller
        does not own are hidden. ``FAMILY``-scoped candidates are
        visible to any family member. ``USER``-scoped candidates (the
        user themselves) follow ``memory.read`` for the user's own
        private space.
        """
        from homemind.infra.db.repos.memory_candidates import (
            MemoryCandidateRow,
        )

        if not isinstance(candidate, MemoryCandidateRow):
            return True

        decision = self.evaluate(
            family_id=family_id,
            user=user,
            action="memory.read",
            space_id=_candidate_space_id(self.repo, candidate),
        )
        return decision.effect is PermissionEffect.ALLOW


def _candidate_space_id(repo: Any, candidate: Any) -> str | None:
    """Return the private space id that owns ``candidate.subject`` when one exists.

    ``MemoryCandidateRow`` carries ``subject_type`` + ``subject_id``;
    for ``MEMBER`` we look up the member's private space, for
    ``USER`` the user's own private space, otherwise ``None``.
    """
    subject_type = getattr(candidate, "subject_type", "")
    subject_id = getattr(candidate, "subject_id", None)
    if not subject_id:
        return None
    if subject_type == "MEMBER":
        member = repo.get_member(str(subject_id))
        if member is None:
            return None
        spaces = repo.list_spaces(member.family_id)
        for space in spaces:
            if space.space_type == "PRIVATE" and space.owner_member_id == member.id:
                return space.id
        return None
    return None


__all__ = [
    "DEFAULT_ACTION_EFFECTS",
    "FamilyPermissionEvaluator",
    "PermissionDecision",
    "PermissionEffect",
    "default_effect_for",
]