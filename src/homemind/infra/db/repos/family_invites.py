"""Read/write access for family invites (stage 10)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts, optional_updates
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyInviteRow:
    id: str
    pk: int
    family_id: str
    role: str
    display_name: str
    token_hash: str
    created_by: int
    created_at: int
    expires_at: int
    redeemed_at: int | None
    redeemed_by: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyInviteRow:
        return cls(
            id=str(row["invite_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            role=str(row["role"]),
            display_name=str(row["display_name"]),
            token_hash=str(row["token_hash"]),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            expires_at=int(row["expires_at"]),
            redeemed_at=int(row["redeemed_at"]) if row["redeemed_at"] is not None else None,
            redeemed_by=int(row["redeemed_by"]) if row["redeemed_by"] is not None else None,
        )


class FamilyInviteRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def create(
        self,
        family_id: str,
        *,
        role: str,
        display_name: str,
        token_hash: str,
        created_by: int,
        expires_at: int,
    ) -> FamilyInviteRow:
        invite_id = new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_invites(invite_id, family_id, role, "
                "display_name, token_hash, created_by, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (invite_id, family_id, role, display_name.strip(), token_hash, created_by, ts, expires_at),
            )
        return self.get(invite_id)  # type: ignore[return-value]

    def get(self, invite_id: str) -> FamilyInviteRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_invites WHERE invite_id = ?",
                (invite_id,),
            ).fetchone()
        return FamilyInviteRow.from_row(row) if row else None

    def get_by_token_hash(self, token_hash: str) -> FamilyInviteRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_invites WHERE token_hash = ?",
                (token_hash,),
            ).fetchone()
        return FamilyInviteRow.from_row(row) if row else None

    def list_for_family(
        self, family_id: str, *, include_redeemed: bool = True
    ) -> list[FamilyInviteRow]:
        clauses = ["family_id = ?"]
        params: list[Any] = [family_id]
        if not include_redeemed:
            clauses.append("redeemed_at IS NULL")
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_invites WHERE "
                + " AND ".join(clauses)
                + " ORDER BY created_at DESC, id DESC",
                params,
            ).fetchall()
        return map_rows(rows, FamilyInviteRow)

    def revoke(self, invite_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM homemind_family_invites "
                "WHERE invite_id = ? AND redeemed_at IS NULL",
                (invite_id,),
            )
        return int(cursor.rowcount or 0) > 0

    def mark_redeemed(self, invite_id: str, redeemed_by: int) -> FamilyInviteRow | None:
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_invites SET redeemed_at = ?, redeemed_by = ? "
                "WHERE invite_id = ? AND redeemed_at IS NULL",
                (ts, redeemed_by, invite_id),
            )
        return self.get(invite_id)


__all__ = ["FamilyInviteRepo", "FamilyInviteRow"]
