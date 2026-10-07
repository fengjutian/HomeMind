"""Family-domain validation and authorization."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from enum import StrEnum
from typing import TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    from psycopg import errors as pg_errors
except ImportError:  # pragma: no cover - optional PostgreSQL driver
    pg_errors = None  # type: ignore[assignment]

from homemind.infra.db.repos.families import (
    FamilyMemberRow,
    FamilyPermissionRow,
    FamilyRelationshipRow,
    FamilyRepo,
    FamilyRow,
    FamilySpaceRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionDecision,
    PermissionEffect,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


class MemberRole(StrEnum):
    OWNER = "OWNER"
    ADMIN = "ADMIN"
    MEMBER = "MEMBER"
    CHILD = "CHILD"
    GUEST = "GUEST"


class RelationshipType(StrEnum):
    SPOUSE = "SPOUSE"
    PARENT = "PARENT"
    CHILD = "CHILD"
    SIBLING = "SIBLING"
    GRANDPARENT = "GRANDPARENT"
    GRANDCHILD = "GRANDCHILD"
    OTHER = "OTHER"


class SpaceType(StrEnum):
    SHARED = "SHARED"
    PRIVATE = "PRIVATE"
    ARCHIVE = "ARCHIVE"


# ``PermissionEffect`` now lives in ``homemind.infra.family.permissions``.
# Re-exported here for backward compatibility with existing callers.
__all__ = [
    "FamilyManager",
    "FamilyPermissionEvaluator",
    "PermissionDecision",
    "PermissionEffect",
]


T = TypeVar("T")


def _write(call: Callable[[], T]) -> T:
    try:
        return call()
    except Exception as exc:
        is_unique = isinstance(exc, sqlite3.IntegrityError) and "unique" in str(exc).lower()
        if pg_errors is not None and isinstance(exc, pg_errors.UniqueViolation):
            is_unique = True
        if is_unique:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT, "family resource already exists"
            ) from exc
        raise


def _reject_null(changes: dict[str, object], fields: set[str]) -> None:
    if any(field in changes and changes[field] is None for field in fields):
        raise HomeMindError(
            HomeMindErrorCode.FAMILY_INVALID, "required family field cannot be null"
        )


def _validate_timezone(value: str) -> str:
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise HomeMindError(HomeMindErrorCode.FAMILY_INVALID, "invalid family timezone") from exc
    return value


class FamilyManager:
    def __init__(self, repo: FamilyRepo) -> None:
        self.repo = repo

    def create_family(
        self,
        user: User,
        *,
        name: str,
        timezone: str,
        locale: str,
        avatar: str | None = None,
    ) -> FamilyRow:
        return _write(
            lambda: self.repo.create_family(
                owner_user_id=user.id,
                owner_display_name=user.label,
                name=name.strip(),
                timezone=_validate_timezone(timezone),
                locale=locale,
                avatar=avatar,
            )
        )

    def list_families(self, user: User) -> list[FamilyRow]:
        return self.repo.list_for_user(user.id)

    def require_access(self, family_id: str, user: User) -> FamilyRow:
        family = self.repo.get_family(family_id)
        if family is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family not found")
        if user.is_admin or self.repo.get_membership(family_id, user.id) is not None:
            return family
        raise OctopError(ErrorCode.FORBIDDEN, "family access denied")

    def require_manager(self, family_id: str, user: User) -> FamilyRow:
        family = self.require_access(family_id, user)
        membership = self.repo.get_membership(family_id, user.id)
        if user.is_admin or (
            membership is not None and str(membership["role"]) in {"OWNER", "ADMIN"}
        ):
            return family
        raise OctopError(ErrorCode.FORBIDDEN, "family manager required")

    def update_family(
        self, family_id: str, user: User, changes: dict[str, object]
    ) -> FamilyRow:
        self.require_manager(family_id, user)
        _reject_null(changes, {"name", "timezone", "locale"})
        if "timezone" in changes:
            changes["timezone"] = _validate_timezone(str(changes["timezone"]))
        if "name" in changes:
            changes["name"] = str(changes["name"]).strip()
        row = _write(lambda: self.repo.update_family(family_id, **changes))
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family not found")
        return row

    def delete_family(self, family_id: str, user: User) -> None:
        family = self.require_access(family_id, user)
        if not user.is_admin and family.owner_user_id != user.id:
            raise OctopError(ErrorCode.FORBIDDEN, "family owner required")
        self.repo.delete_family(family_id)

    def create_member(
        self,
        family_id: str,
        user: User,
        *,
        display_name: str,
        role: MemberRole,
        user_id: int | None = None,
        avatar: str | None = None,
        birthday: str | None = None,
    ) -> FamilyMemberRow:
        self.require_manager(family_id, user)
        if role is MemberRole.OWNER:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family can have only one owner"
            )
        if user_id is not None and not self.repo.user_exists(user_id):
            raise OctopError(ErrorCode.NOT_FOUND, "linked user not found")
        return _write(
            lambda: self.repo.create_member(
                family_id,
                display_name=display_name.strip(),
                role=role.value,
                user_id=user_id,
                avatar=avatar,
                birthday=birthday,
            )
        )

    def create_member_for_invite(
        self,
        family_id: str,
        *,
        display_name: str,
        role: MemberRole,
        user_id: int,
    ) -> FamilyMemberRow:
        """Like :meth:`create_member` but bypasses ``require_manager``.

        Stage 10 invite redemption uses this so a brand-new redeemer
        can join without already being a member. The invite itself is
        the authorization; the manager-only path is reserved for
        in-app member creation.
        """
        if role is MemberRole.OWNER:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family can have only one owner"
            )
        if not self.repo.user_exists(user_id):
            raise OctopError(ErrorCode.NOT_FOUND, "linked user not found")
        return _write(
            lambda: self.repo.create_member(
                family_id,
                display_name=display_name.strip(),
                role=role.value,
                user_id=user_id,
                avatar=None,
                birthday=None,
            )
        )

    def update_member(
        self, family_id: str, member_id: str, user: User, changes: dict[str, object]
    ) -> FamilyMemberRow:
        self.require_manager(family_id, user)
        member = self._require_member(family_id, member_id)
        _reject_null(changes, {"display_name", "role", "status"})
        if member.role == MemberRole.OWNER:
            raise OctopError(ErrorCode.FORBIDDEN, "family owner cannot be changed")
        if changes.get("role") == MemberRole.OWNER:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "family can have only one owner"
            )
        if isinstance(changes.get("role"), MemberRole):
            changes["role"] = changes["role"].value
        if "display_name" in changes:
            changes["display_name"] = str(changes["display_name"]).strip()
        row = _write(lambda: self.repo.update_member(member_id, **changes))
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        return row

    def delete_member(self, family_id: str, member_id: str, user: User) -> None:
        self.require_manager(family_id, user)
        member = self._require_member(family_id, member_id)
        if member.role == MemberRole.OWNER:
            raise OctopError(ErrorCode.FORBIDDEN, "family owner cannot be deleted")
        if self._is_last_manager(family_id, member_id):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "the last family manager cannot be removed",
            )
        if any(
            space.space_type == SpaceType.PRIVATE and space.owner_member_id == member_id
            for space in self.repo.list_spaces(family_id)
        ):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "private spaces must be reassigned before deleting their owner",
            )
        self.repo.delete_member(member_id)

    # --------------------------------------------------- ownership transfer

    def transfer_ownership(
        self, family_id: str, user: User, *, to_member_id: str
    ) -> FamilyMemberRow:
        """Hand the family over to another member.

        The current owner and the target swap roles in one write so the
        family is never briefly left with two owners (or none). Only the
        owner may transfer — an admin cannot seize the family.
        """
        family = self.require_access(family_id, user)
        membership = self.repo.get_membership(family_id, user.id)
        if membership is None or str(membership["role"]) != MemberRole.OWNER.value:
            raise OctopError(ErrorCode.FORBIDDEN, "only the family owner can transfer ownership")
        target = self._require_member(family_id, to_member_id)
        if target.id == str(membership["member_id"]):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "member already owns this family"
            )
        if target.role not in {MemberRole.OWNER, MemberRole.ADMIN, MemberRole.MEMBER}:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "ownership cannot be transferred to a guest or child member",
            )
        if target.user_id is None:
            # ``homemind_families.owner_user_id`` is a NOT NULL foreign
            # key, so ownership has to land on a member that is actually
            # bound to an account. An unbound member is a placeholder, not
            # a person who can sign in and administer the family.
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "bind a platform account to the member before transferring ownership",
            )
        _write(
            lambda: self.repo.update_member(
                str(membership["member_id"]), role=MemberRole.ADMIN.value,
            )
        )
        # ``update_member`` cascades the role onto the membership row,
        # so both members now carry the right role and only the family's
        # owner pointer still needs moving.
        self.repo.update_member(to_member_id, role=MemberRole.OWNER.value)
        if not self.repo.update_family_owner(family.id, user.id, target.user_id):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "family ownership changed concurrently; retry the transfer",
            )
        return self._require_member(family_id, to_member_id)

    def leave_family(self, family_id: str, user: User) -> None:
        """A member voluntarily leaves.

        Two invariants block an exit that would strand the family:
        the owner must transfer first, and the last manager cannot walk
        away and leave nobody able to approve anything.
        """
        self.require_access(family_id, user)
        membership = self.repo.get_membership(family_id, user.id)
        if membership is None:
            return
        member_id = str(membership["member_id"])
        role = str(membership["role"])
        if role == MemberRole.OWNER.value:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "transfer ownership before leaving the family",
            )
        if role == MemberRole.ADMIN.value and self._is_last_manager(family_id, member_id):
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "the last family manager cannot leave",
            )
        self.repo.delete_member(member_id)

    # ------------------------------------------------------- user binding

    def bind_user(
        self, family_id: str, member_id: str, user: User, *, target_user_id: int
    ) -> FamilyMemberRow:
        """Link a member to a platform account.

        A member may be bound to at most one user, and a user may not
        hold two member rows in the same family — otherwise one login
        would see two identities and permission checks would become
        ambiguous.
        """
        self.require_manager(family_id, user)
        member = self._require_member(family_id, member_id)
        if not self.repo.user_exists(target_user_id):
            raise OctopError(ErrorCode.NOT_FOUND, "linked user not found")
        existing_member = self.repo.find_member_by_user(family_id, target_user_id)
        if existing_member is not None and existing_member.id != member.id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "that user is already bound to another member in this family",
            )
        if member.user_id is not None and member.user_id != target_user_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_CONFLICT,
                "member is already bound to a different user",
            )
        row = _write(
            lambda: self.repo.update_member(member.id, user_id=target_user_id)
        )
        return self._require_member(family_id, row.id if row else member.id)

    def unbind_user(
        self, family_id: str, member_id: str, user: User
    ) -> FamilyMemberRow:
        """Detach a member from its platform account."""
        self.require_manager(family_id, user)
        self._require_member(family_id, member_id)
        _write(lambda: self.repo.update_member(member_id, user_id=None))
        return self._require_member(family_id, member_id)

    def _is_last_manager(self, family_id: str, member_id: str) -> bool:
        """True when removing ``member_id`` would leave no owner/admin."""
        managers = [
            member
            for member in self.repo.list_members(family_id)
            if member.role in {MemberRole.OWNER, MemberRole.ADMIN}
            and member.id != member_id
        ]
        return not managers

    def create_relationship(
        self,
        family_id: str,
        user: User,
        *,
        from_member_id: str,
        to_member_id: str,
        relationship_type: RelationshipType,
    ) -> FamilyRelationshipRow:
        self.require_manager(family_id, user)
        if from_member_id == to_member_id:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "relationship members must differ"
            )
        self._require_member(family_id, from_member_id)
        self._require_member(family_id, to_member_id)
        return _write(
            lambda: self.repo.create_relationship(
                family_id,
                from_member_id=from_member_id,
                to_member_id=to_member_id,
                relationship_type=relationship_type.value,
            )
        )

    def delete_relationship(self, family_id: str, relationship_id: str, user: User) -> None:
        self.require_manager(family_id, user)
        relationship = self.repo.get_relationship(relationship_id)
        if relationship is None or relationship.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family relationship not found")
        self.repo.delete_relationship(relationship_id)

    def create_space(
        self,
        family_id: str,
        user: User,
        *,
        name: str,
        space_type: SpaceType,
        owner_member_id: str | None = None,
    ) -> FamilySpaceRow:
        self.require_manager(family_id, user)
        if owner_member_id is not None:
            self._require_member(family_id, owner_member_id)
        if space_type is SpaceType.PRIVATE and owner_member_id is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "private space requires an owner"
            )
        return _write(
            lambda: self.repo.create_space(
                family_id,
                name=name.strip(),
                space_type=space_type.value,
                owner_member_id=owner_member_id,
            )
        )

    def update_space(
        self, family_id: str, space_id: str, user: User, changes: dict[str, object]
    ) -> FamilySpaceRow:
        self.require_manager(family_id, user)
        space = self.repo.get_space(space_id)
        if space is None or space.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
        _reject_null(changes, {"name", "space_type"})
        owner_member_id = changes.get("owner_member_id", space.owner_member_id)
        space_type = changes.get("space_type", space.space_type)
        if isinstance(space_type, SpaceType):
            space_type = space_type.value
            changes["space_type"] = space_type
        if owner_member_id is not None:
            self._require_member(family_id, str(owner_member_id))
        if space_type == SpaceType.PRIVATE and owner_member_id is None:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "private space requires an owner"
            )
        if "name" in changes:
            changes["name"] = str(changes["name"]).strip()
        row = _write(lambda: self.repo.update_space(space_id, **changes))
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
        return row

    def delete_space(self, family_id: str, space_id: str, user: User) -> None:
        self.require_manager(family_id, user)
        space = self.repo.get_space(space_id)
        if space is None or space.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
        self.repo.delete_space(space_id)

    def create_permission(
        self,
        family_id: str,
        user: User,
        *,
        subject_member_id: str | None,
        space_id: str | None,
        action: str,
        effect: PermissionEffect,
        expires_at: int | None,
    ) -> FamilyPermissionRow:
        self.require_manager(family_id, user)
        if subject_member_id is not None:
            self._require_member(family_id, subject_member_id)
        if space_id is not None and all(
            space.id != space_id for space in self.repo.list_spaces(family_id)
        ):
            raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
        return _write(
            lambda: self.repo.create_permission(
                family_id,
                subject_member_id=subject_member_id,
                space_id=space_id,
                action=action.strip(),
                effect=effect.value,
                expires_at=expires_at,
                created_by=user.id,
            )
        )

    def update_permission(
        self, family_id: str, permission_id: str, user: User, changes: dict[str, object]
    ) -> FamilyPermissionRow:
        self.require_manager(family_id, user)
        permission = self.repo.get_permission(permission_id)
        if permission is None or permission.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family permission not found")
        _reject_null(changes, {"action", "effect"})
        subject = changes.get("subject_member_id", permission.subject_member_id)
        space_id = changes.get("space_id", permission.space_id)
        if subject is not None:
            self._require_member(family_id, str(subject))
        if space_id is not None:
            space = self.repo.get_space(str(space_id))
            if space is None or space.family_id != family_id:
                raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
        if isinstance(changes.get("effect"), PermissionEffect):
            changes["effect"] = changes["effect"].value
        if "action" in changes:
            changes["action"] = str(changes["action"]).strip()
        row = _write(lambda: self.repo.update_permission(permission_id, **changes))
        if row is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family permission not found")
        return row

    def delete_permission(self, family_id: str, permission_id: str, user: User) -> None:
        self.require_manager(family_id, user)
        permission = self.repo.get_permission(permission_id)
        if permission is None or permission.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family permission not found")
        self.repo.delete_permission(permission_id)

    def evaluate_permission(
        self,
        family_id: str,
        user: User,
        *,
        subject_member_id: str,
        action: str,
        space_id: str | None = None,
        now: int | None = None,
    ) -> PermissionEffect:
        self.require_access(family_id, user)
        self._require_member(family_id, subject_member_id)
        timestamp = int(time.time()) if now is None else now
        candidates = [
            permission
            for permission in self.repo.list_permissions(family_id)
            if permission.action == action
            and permission.subject_member_id in {None, subject_member_id}
            and permission.space_id in {None, space_id}
            and (permission.expires_at is None or permission.expires_at > timestamp)
        ]
        if not candidates:
            return PermissionEffect.DENY
        priority = {
            PermissionEffect.ALLOW.value: 0,
            PermissionEffect.REQUIRE_CONFIRMATION.value: 1,
            PermissionEffect.DENY.value: 2,
        }
        candidates.sort(
            key=lambda permission: (
                int(permission.subject_member_id is not None)
                + int(permission.space_id is not None),
                priority[permission.effect],
            ),
            reverse=True,
        )
        return PermissionEffect(candidates[0].effect)

    def _require_member(self, family_id: str, member_id: str) -> FamilyMemberRow:
        member = self.repo.get_member(member_id)
        if member is None or member.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family member not found")
        return member
