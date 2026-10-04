"""SQL persistence for family albums and organization plans."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyAlbumRow:
    id: str
    family_id: str
    name: str
    description: str
    cover_asset_id: str | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyAlbumRow:
        return cls(
            id=str(row["album_id"]),
            family_id=str(row["family_id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            cover_asset_id=row["cover_asset_id"],
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class OrganizationPlanRow:
    id: str
    family_id: str
    strategy: str
    status: str
    groups_json: str
    created_by: int
    created_at: int
    applied_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> OrganizationPlanRow:
        return cls(
            id=str(row["plan_id"]),
            family_id=str(row["family_id"]),
            strategy=str(row["strategy"]),
            status=str(row["status"]),
            groups_json=str(row["groups_json"]),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            applied_at=int(row["applied_at"]) if row["applied_at"] is not None else None,
        )


class FamilyAlbumRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create_album(
        self, family_id: str, name: str, description: str, created_by: int
    ) -> FamilyAlbumRow:
        album_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_albums(album_id, family_id, name, description, "
                "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (album_id, family_id, name, description, created_by, timestamp, timestamp),
            )
        return self.get_album(album_id)  # type: ignore[return-value]

    def get_album(self, album_id: str) -> FamilyAlbumRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_albums WHERE album_id = ?", (album_id,)
            ).fetchone()
        return FamilyAlbumRow.from_row(row) if row else None

    def get_album_by_name(self, family_id: str, name: str) -> FamilyAlbumRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_albums WHERE family_id = ? AND name = ?",
                (family_id, name),
            ).fetchone()
        return FamilyAlbumRow.from_row(row) if row else None

    def list_albums(self, family_id: str) -> list[FamilyAlbumRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_albums WHERE family_id = ? "
                "ORDER BY updated_at DESC, id DESC",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyAlbumRow)

    def add_asset(self, album_id: str, asset_id: str, user_id: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_album_assets(album_id, asset_id, added_by, added_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(album_id, asset_id) DO NOTHING",
                (album_id, asset_id, user_id, now_ts()),
            )

    def remove_asset(self, album_id: str, asset_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_album_assets WHERE album_id = ? AND asset_id = ?",
                (album_id, asset_id),
            )
        return int(cursor.rowcount or 0) > 0

    def list_asset_ids(self, album_id: str) -> list[str]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT asset_id FROM homemind_family_album_assets WHERE album_id = ? "
                "ORDER BY added_at, asset_id",
                (album_id,),
            ).fetchall()
        return [str(row["asset_id"]) for row in rows]

    def create_plan(
        self, family_id: str, strategy: str, groups_json: str, created_by: int
    ) -> OrganizationPlanRow:
        plan_id, timestamp = new_ulid(), now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_organization_plans(plan_id, family_id, strategy, "
                "groups_json, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (plan_id, family_id, strategy, groups_json, created_by, timestamp),
            )
        return self.get_plan(plan_id)  # type: ignore[return-value]

    def get_plan(self, plan_id: str) -> OrganizationPlanRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_organization_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return OrganizationPlanRow.from_row(row) if row else None

    def mark_plan_applied(self, plan_id: str) -> OrganizationPlanRow:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE homemind_family_organization_plans SET status = 'APPLIED', applied_at = ? "
                "WHERE plan_id = ? AND status = 'PLANNED'",
                (now_ts(), plan_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("organization plan is not pending")
        return self.get_plan(plan_id)  # type: ignore[return-value]
