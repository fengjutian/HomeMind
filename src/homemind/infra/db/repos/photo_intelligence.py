"""SQL persistence for derived photo intelligence."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, map_rows, now_ts


@dataclass(frozen=True)
class PhotoIntelligenceRow:
    asset_id: str
    family_id: str
    description: str
    objects_json: str
    scenes_json: str
    faces_json: str
    location_name: str | None
    perceptual_hash: str | None
    embedding_json: str | None
    vision_provider: str | None
    embedding_provider: str | None
    analyzed_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> PhotoIntelligenceRow:
        return cls(
            asset_id=str(row["asset_id"]),
            family_id=str(row["family_id"]),
            description=str(row["description"]),
            objects_json=str(row["objects_json"]),
            scenes_json=str(row["scenes_json"]),
            faces_json=str(row["faces_json"]),
            location_name=row["location_name"],
            perceptual_hash=row["perceptual_hash"],
            embedding_json=row["embedding_json"],
            vision_provider=row["vision_provider"],
            embedding_provider=row["embedding_provider"],
            analyzed_at=int(row["analyzed_at"]),
        )


class PhotoIntelligenceRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def upsert(
        self, *, asset_id: str, family_id: str, **values: object
    ) -> PhotoIntelligenceRow:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_photo_intelligence(asset_id, family_id, description, "
                "objects_json, scenes_json, faces_json, location_name, perceptual_hash, "
                "embedding_json, vision_provider, embedding_provider, analyzed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(asset_id) DO UPDATE SET description = excluded.description, "
                "objects_json = excluded.objects_json, scenes_json = excluded.scenes_json, "
                "faces_json = excluded.faces_json, location_name = excluded.location_name, "
                "perceptual_hash = excluded.perceptual_hash, "
                "embedding_json = excluded.embedding_json, "
                "vision_provider = excluded.vision_provider, "
                "embedding_provider = excluded.embedding_provider, analyzed_at = excluded.analyzed_at",
                (
                    asset_id,
                    family_id,
                    values.get("description", ""),
                    values.get("objects_json", "[]"),
                    values.get("scenes_json", "[]"),
                    values.get("faces_json", "[]"),
                    values.get("location_name"),
                    values.get("perceptual_hash"),
                    values.get("embedding_json"),
                    values.get("vision_provider"),
                    values.get("embedding_provider"),
                    now_ts(),
                ),
            )
        return self.get(asset_id)  # type: ignore[return-value]

    def get(self, asset_id: str) -> PhotoIntelligenceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_photo_intelligence WHERE asset_id = ?",
                (asset_id,),
            ).fetchone()
        return PhotoIntelligenceRow.from_row(row) if row else None

    def list(self, family_id: str) -> list[PhotoIntelligenceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_photo_intelligence WHERE family_id = ?",
                (family_id,),
            ).fetchall()
        return map_rows(rows, PhotoIntelligenceRow)
