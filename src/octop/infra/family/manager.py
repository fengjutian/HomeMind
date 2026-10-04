"""Family-domain validation and authorization."""

from __future__ import annotations

import time
from enum import StrEnum

from octop.infra.db.repos.families import (
    FamilyMemberRow,
    FamilyPermissionRow,
    FamilyRelationshipRow,
    FamilyRepo,
    FamilyRow,
    FamilySpaceRow,
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


class PermissionEffect(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REQUIRE_CONFIRMATION = "REQUIRE_CONFIRMATION"


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
        return self.repo.create_family(
            owner_user_id=user.id,
            owner_display_name=user.label,
            name=name.strip(),
            timezone=timezone,
            locale=locale,
            avatar=avatar,
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
        return self.repo.create_member(
            family_id,
            display_name=display_name.strip(),
            role=role.value,
            user_id=user_id,
            avatar=avatar,
            birthday=birthday,
        )

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
            raise OctopError(ErrorCode.FAMILY_INVALID, "relationship members must differ")
        self._require_member(family_id, from_member_id)
        self._require_member(family_id, to_member_id)
        return self.repo.create_relationship(
            family_id,
            from_member_id=from_member_id,
            to_member_id=to_member_id,
            relationship_type=relationship_type.value,
        )

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
            raise OctopError(ErrorCode.FAMILY_INVALID, "private space requires an owner")
        return self.repo.create_space(
            family_id,
            name=name.strip(),
            space_type=space_type.value,
            owner_member_id=owner_member_id,
        )

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
        return self.repo.create_permission(
            family_id,
            subject_member_id=subject_member_id,
            space_id=space_id,
            action=action.strip(),
            effect=effect.value,
            expires_at=expires_at,
            created_by=user.id,
        )

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
