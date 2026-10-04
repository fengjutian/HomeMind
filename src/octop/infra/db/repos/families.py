"""SQL access for the HomeMind family foundation tables."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyRow:
    id: str
    pk: int
    name: str
    avatar: str | None
    owner_user_id: int
    timezone: str
    locale: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyRow:
        return cls(
            id=str(row["family_id"]),
            pk=int(row["id"]),
            name=str(row["name"]),
            avatar=row["avatar"],
            owner_user_id=int(row["owner_user_id"]),
            timezone=str(row["timezone"]),
            locale=str(row["locale"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FamilyMemberRow:
    id: str
    pk: int
    family_id: str
    user_id: int | None
    display_name: str
    role: str
    avatar: str | None
    birthday: str | None
    status: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyMemberRow:
        return cls(
            id=str(row["member_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            user_id=int(row["user_id"]) if row["user_id"] is not None else None,
            display_name=str(row["display_name"]),
            role=str(row["role"]),
            avatar=row["avatar"],
            birthday=row["birthday"],
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FamilyRelationshipRow:
    id: str
    pk: int
    family_id: str
    from_member_id: str
    to_member_id: str
    relationship_type: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyRelationshipRow:
        return cls(
            id=str(row["relationship_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            from_member_id=str(row["from_member_id"]),
            to_member_id=str(row["to_member_id"]),
            relationship_type=str(row["relationship_type"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FamilySpaceRow:
    id: str
    pk: int
    family_id: str
    name: str
    space_type: str
    owner_member_id: str | None
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilySpaceRow:
        return cls(
            id=str(row["space_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            name=str(row["name"]),
            space_type=str(row["space_type"]),
            owner_member_id=row["owner_member_id"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class FamilyPermissionRow:
    id: str
    pk: int
    family_id: str
    subject_member_id: str | None
    space_id: str | None
    action: str
    effect: str
    expires_at: int | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyPermissionRow:
        return cls(
            id=str(row["permission_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            subject_member_id=row["subject_member_id"],
            space_id=row["space_id"],
            action=str(row["action"]),
            effect=str(row["effect"]),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class FamilyRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create_family(
        self,
        *,
        owner_user_id: int,
        owner_display_name: str,
        name: str,
        timezone: str,
        locale: str,
        avatar: str | None = None,
    ) -> FamilyRow:
        family_id = new_ulid()
        member_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO families(family_id, name, avatar, owner_user_id, timezone, "
                "locale, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (family_id, name, avatar, owner_user_id, timezone, locale, ts, ts),
            )
            conn.execute(
                "INSERT INTO family_members(member_id, family_id, user_id, display_name, role, "
                "status, created_at, updated_at) VALUES (?, ?, ?, ?, 'OWNER', 'ACTIVE', ?, ?)",
                (member_id, family_id, owner_user_id, owner_display_name, ts, ts),
            )
            conn.execute(
                "INSERT INTO family_memberships(membership_id, family_id, member_id, user_id, "
                "role, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'OWNER', 'ACTIVE', ?, ?)",
                (new_ulid(), family_id, member_id, owner_user_id, ts, ts),
            )
            conn.execute(
                "INSERT INTO family_spaces(space_id, family_id, name, space_type, created_at, "
                "updated_at) VALUES (?, ?, 'Shared', 'SHARED', ?, ?)",
                (new_ulid(), family_id, ts, ts),
            )
        row = self.get_family(family_id)
        if row is None:
            raise RuntimeError("family insert failed")
        return row

    def get_family(self, family_id: str) -> FamilyRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM families WHERE family_id = ?", (family_id,)
            ).fetchone()
        return FamilyRow.from_row(row) if row else None

    def list_for_user(self, user_id: int) -> list[FamilyRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT f.* FROM families f JOIN family_memberships m "
                "ON m.family_id = f.family_id WHERE m.user_id = ? AND m.status = 'ACTIVE' "
                "ORDER BY f.created_at, f.id",
                (user_id,),
            ).fetchall()
        return map_rows(rows, FamilyRow)

    def get_membership(self, family_id: str, user_id: int) -> DbRow | None:
        with self._db.connect() as conn:
            return conn.execute(
                "SELECT * FROM family_memberships WHERE family_id = ? AND user_id = ? "
                "AND status = 'ACTIVE'",
                (family_id, user_id),
            ).fetchone()

    def create_member(
        self,
        family_id: str,
        *,
        display_name: str,
        role: str,
        user_id: int | None = None,
        avatar: str | None = None,
        birthday: str | None = None,
    ) -> FamilyMemberRow:
        member_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO family_members(member_id, family_id, user_id, display_name, role, "
                "avatar, birthday, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                (member_id, family_id, user_id, display_name, role, avatar, birthday, ts, ts),
            )
            if user_id is not None:
                conn.execute(
                    "INSERT INTO family_memberships(membership_id, family_id, member_id, "
                    "user_id, role, status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, 'ACTIVE', ?, ?)",
                    (new_ulid(), family_id, member_id, user_id, role, ts, ts),
                )
        row = self.get_member(member_id)
        if row is None:
            raise RuntimeError("family member insert failed")
        return row

    def get_member(self, member_id: str) -> FamilyMemberRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM family_members WHERE member_id = ?", (member_id,)
            ).fetchone()
        return FamilyMemberRow.from_row(row) if row else None

    def list_members(self, family_id: str) -> list[FamilyMemberRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM family_members WHERE family_id = ? ORDER BY created_at, id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyMemberRow)

    def create_relationship(
        self, family_id: str, *, from_member_id: str, to_member_id: str, relationship_type: str
    ) -> FamilyRelationshipRow:
        relationship_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO family_relationships(relationship_id, family_id, from_member_id, "
                "to_member_id, relationship_type, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    relationship_id,
                    family_id,
                    from_member_id,
                    to_member_id,
                    relationship_type,
                    ts,
                    ts,
                ),
            )
        return self._required_relationship(relationship_id)

    def _required_relationship(self, relationship_id: str) -> FamilyRelationshipRow:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM family_relationships WHERE relationship_id = ?",
                (relationship_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("family relationship insert failed")
        return FamilyRelationshipRow.from_row(row)

    def list_relationships(self, family_id: str) -> list[FamilyRelationshipRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM family_relationships WHERE family_id = ? ORDER BY created_at, id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyRelationshipRow)

    def create_space(
        self, family_id: str, *, name: str, space_type: str, owner_member_id: str | None = None
    ) -> FamilySpaceRow:
        space_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO family_spaces(space_id, family_id, name, space_type, "
                "owner_member_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (space_id, family_id, name, space_type, owner_member_id, ts, ts),
            )
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM family_spaces WHERE space_id = ?", (space_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("family space insert failed")
        return FamilySpaceRow.from_row(row)

    def list_spaces(self, family_id: str) -> list[FamilySpaceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM family_spaces WHERE family_id = ? ORDER BY created_at, id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilySpaceRow)

    def create_permission(
        self,
        family_id: str,
        *,
        subject_member_id: str | None,
        space_id: str | None,
        action: str,
        effect: str,
        expires_at: int | None,
        created_by: int,
    ) -> FamilyPermissionRow:
        permission_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO family_permissions(permission_id, family_id, subject_member_id, "
                "space_id, action, effect, expires_at, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    permission_id,
                    family_id,
                    subject_member_id,
                    space_id,
                    action,
                    effect,
                    expires_at,
                    created_by,
                    ts,
                    ts,
                ),
            )
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM family_permissions WHERE permission_id = ?", (permission_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("family permission insert failed")
        return FamilyPermissionRow.from_row(row)

    def list_permissions(self, family_id: str) -> list[FamilyPermissionRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM family_permissions WHERE family_id = ? ORDER BY created_at, id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyPermissionRow)
