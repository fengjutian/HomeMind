"""Read access for registered HomeMind family devices."""

from __future__ import annotations

import json
from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows


@dataclass(frozen=True)
class FamilyDeviceRow:
    id: str
    pk: int
    family_id: str
    name: str
    device_type: str
    platform: str | None
    status: str
    address: str | None
    capabilities: list[str]
    last_seen: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyDeviceRow:
        return cls(
            id=str(row["device_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            name=str(row["name"]),
            device_type=str(row["device_type"]),
            platform=row["platform"],
            status=str(row["status"]),
            address=row["address"],
            capabilities=[str(value) for value in json.loads(str(row["capabilities"]))],
            last_seen=int(row["last_seen"]) if row["last_seen"] is not None else None,
        )


class FamilyDeviceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def get(self, device_id: str) -> FamilyDeviceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_devices WHERE device_id = ?", (device_id,)
            ).fetchone()
        return FamilyDeviceRow.from_row(row) if row else None

    def list(self, family_id: str) -> list[FamilyDeviceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_devices WHERE family_id = ? "
                "ORDER BY name, device_id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyDeviceRow)
