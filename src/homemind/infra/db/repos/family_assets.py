"""SQL access for HomeMind family assets and photo metadata."""

from __future__ import annotations

from dataclasses import dataclass

from octop.infra.db.pool import DatabasePool
from octop.infra.db.repos._base import DbRow, bool_int, map_rows, now_ts, sql_in_placeholders
from octop.infra.utils.ulid import new_ulid


@dataclass(frozen=True)
class FamilyAssetRow:
    id: str
    pk: int
    family_id: str
    source_id: str | None
    space_id: str | None
    asset_type: str
    name: str
    uri: str
    mime_type: str
    size_bytes: int
    content_hash: str
    captured_at: int | None
    indexed_at: int
    metadata_json: str
    created_by: int
    visibility: str
    status: str
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyAssetRow:
        return cls(
            id=str(row["asset_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            source_id=row["source_id"],
            space_id=row["space_id"],
            asset_type=str(row["asset_type"]),
            name=str(row["name"]),
            uri=str(row["uri"]),
            mime_type=str(row["mime_type"]),
            size_bytes=int(row["size_bytes"]),
            content_hash=str(row["content_hash"]),
            captured_at=int(row["captured_at"]) if row["captured_at"] is not None else None,
            indexed_at=int(row["indexed_at"]),
            metadata_json=str(row["metadata_json"]),
            created_by=int(row["created_by"]),
            visibility=str(row["visibility"]),
            status=str(row["status"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


@dataclass(frozen=True)
class PhotoMetadataRow:
    asset_id: str
    width: int | None
    height: int | None
    camera_make: str | None
    camera_model: str | None
    latitude: float | None
    longitude: float | None
    taken_at: int | None

    @classmethod
    def from_row(cls, row: DbRow) -> PhotoMetadataRow:
        return cls(
            asset_id=str(row["asset_id"]),
            width=int(row["width"]) if row["width"] is not None else None,
            height=int(row["height"]) if row["height"] is not None else None,
            camera_make=row["camera_make"],
            camera_model=row["camera_model"],
            latitude=float(row["latitude"]) if row["latitude"] is not None else None,
            longitude=float(row["longitude"]) if row["longitude"] is not None else None,
            taken_at=int(row["taken_at"]) if row["taken_at"] is not None else None,
        )


@dataclass(frozen=True)
class FamilyAssetSourceRow:
    id: str
    pk: int
    family_id: str
    space_id: str | None
    directory_uri: str
    recursive: bool
    visibility: str
    status: str
    last_scanned_at: int | None
    created_by: int
    created_at: int
    updated_at: int

    @classmethod
    def from_row(cls, row: DbRow) -> FamilyAssetSourceRow:
        return cls(
            id=str(row["source_id"]),
            pk=int(row["id"]),
            family_id=str(row["family_id"]),
            space_id=row["space_id"],
            directory_uri=str(row["directory_uri"]),
            recursive=bool(row["recursive"]),
            visibility=str(row["visibility"]),
            status=str(row["status"]),
            last_scanned_at=(
                int(row["last_scanned_at"]) if row["last_scanned_at"] is not None else None
            ),
            created_by=int(row["created_by"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )


class FamilyAssetRepo:
    def __init__(self, db: DatabasePool) -> None:
        self._db = db

    def upsert_asset(
        self,
        *,
        family_id: str,
        source_id: str | None,
        space_id: str | None,
        asset_type: str,
        name: str,
        uri: str,
        mime_type: str,
        size_bytes: int,
        content_hash: str,
        captured_at: int | None,
        metadata_json: str,
        created_by: int,
        visibility: str,
    ) -> FamilyAssetRow:
        existing = self.get_by_uri(family_id, uri)
        asset_id = existing.id if existing else new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_assets(asset_id, family_id, source_id, space_id, asset_type, "
                "name, uri, "
                "mime_type, size_bytes, content_hash, captured_at, indexed_at, metadata_json, "
                "created_by, visibility, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'INDEXED', ?, ?) "
                "ON CONFLICT(family_id, uri) DO UPDATE SET "
                "source_id = excluded.source_id, space_id = excluded.space_id, "
                "asset_type = excluded.asset_type, "
                "name = excluded.name, mime_type = excluded.mime_type, "
                "size_bytes = excluded.size_bytes, content_hash = excluded.content_hash, "
                "captured_at = excluded.captured_at, indexed_at = excluded.indexed_at, "
                "metadata_json = excluded.metadata_json, visibility = excluded.visibility, "
                "status = 'INDEXED', updated_at = excluded.updated_at",
                (
                    asset_id,
                    family_id,
                    source_id,
                    space_id,
                    asset_type,
                    name,
                    uri,
                    mime_type,
                    size_bytes,
                    content_hash,
                    captured_at,
                    ts,
                    metadata_json,
                    created_by,
                    visibility,
                    ts,
                    ts,
                ),
            )
        row = self.get_by_uri(family_id, uri)
        if row is None:
            raise RuntimeError("family asset upsert failed")
        return row

    def upsert_source(
        self,
        *,
        family_id: str,
        space_id: str | None,
        directory_uri: str,
        recursive: bool,
        visibility: str,
        created_by: int,
    ) -> FamilyAssetSourceRow:
        existing = self.get_source_by_uri(family_id, directory_uri)
        source_id = existing.id if existing else new_ulid()
        ts = now_ts()
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_asset_sources(source_id, family_id, space_id, directory_uri, "
                "recursive, visibility, status, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?) "
                "ON CONFLICT(family_id, directory_uri) DO UPDATE SET "
                "space_id = excluded.space_id, recursive = excluded.recursive, "
                "visibility = excluded.visibility, status = 'ACTIVE', "
                "updated_at = excluded.updated_at",
                (
                    source_id,
                    family_id,
                    space_id,
                    directory_uri,
                    bool_int(recursive),
                    visibility,
                    created_by,
                    ts,
                    ts,
                ),
            )
        row = self.get_source_by_uri(family_id, directory_uri)
        if row is None:
            raise RuntimeError("family asset source upsert failed")
        return row

    def get_source(self, source_id: str) -> FamilyAssetSourceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_asset_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return FamilyAssetSourceRow.from_row(row) if row else None

    def get_source_by_uri(
        self, family_id: str, directory_uri: str
    ) -> FamilyAssetSourceRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_asset_sources WHERE family_id = ? AND directory_uri = ?",
                (family_id, directory_uri),
            ).fetchone()
        return FamilyAssetSourceRow.from_row(row) if row else None

    def list_sources(self, family_id: str) -> list[FamilyAssetSourceRow]:
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_asset_sources WHERE family_id = ? ORDER BY created_at, id",
                (family_id,),
            ).fetchall()
        return map_rows(rows, FamilyAssetSourceRow)

    def list_all_sources(self) -> list[FamilyAssetSourceRow]:
        """Enumerate every asset source across every family.

        Used by the periodic re-scan job so it can fan out without
        knowing which families exist.
        """
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_asset_sources "
                "ORDER BY family_id, created_at, id"
            ).fetchall()
        return map_rows(rows, FamilyAssetSourceRow)

    def finish_source_scan(self, source_id: str, scanned_at: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_asset_sources SET last_scanned_at = ?, status = 'ACTIVE', "
                "updated_at = ? WHERE source_id = ?",
                (scanned_at, scanned_at, source_id),
            )

    def touch_asset(self, asset_id: str, indexed_at: int) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "UPDATE homemind_family_assets SET indexed_at = ?, status = 'INDEXED', updated_at = ? "
                "WHERE asset_id = ?",
                (indexed_at, indexed_at, asset_id),
            )

    def mark_missing(self, source_id: str, seen_asset_ids: list[str], timestamp: int) -> int:
        sql = (
            "UPDATE homemind_family_assets SET status = 'MISSING', updated_at = ? "
            "WHERE source_id = ? AND status <> 'MISSING'"
        )
        params: list[object] = [timestamp, source_id]
        if seen_asset_ids:
            sql += f" AND asset_id NOT IN ({sql_in_placeholders(len(seen_asset_ids))})"
            params.extend(seen_asset_ids)
        with self._db.transaction() as conn:
            cursor = conn.execute(sql, params)
            changed = int(cursor.rowcount or 0)
        return changed

    def upsert_photo_metadata(
        self,
        asset_id: str,
        *,
        width: int | None,
        height: int | None,
        camera_make: str | None,
        camera_model: str | None,
        latitude: float | None,
        longitude: float | None,
        taken_at: int | None,
    ) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                "INSERT INTO homemind_family_photo_metadata(asset_id, width, height, camera_make, "
                "camera_model, latitude, longitude, taken_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(asset_id) DO UPDATE SET width = excluded.width, "
                "height = excluded.height, camera_make = excluded.camera_make, "
                "camera_model = excluded.camera_model, latitude = excluded.latitude, "
                "longitude = excluded.longitude, taken_at = excluded.taken_at",
                (
                    asset_id,
                    width,
                    height,
                    camera_make,
                    camera_model,
                    latitude,
                    longitude,
                    taken_at,
                ),
            )

    def get(self, asset_id: str) -> FamilyAssetRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_assets WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return FamilyAssetRow.from_row(row) if row else None

    def get_by_uri(self, family_id: str, uri: str) -> FamilyAssetRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_assets WHERE family_id = ? AND uri = ?",
                (family_id, uri),
            ).fetchone()
        return FamilyAssetRow.from_row(row) if row else None

    def get_photo_metadata(self, asset_id: str) -> PhotoMetadataRow | None:
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM homemind_family_photo_metadata WHERE asset_id = ?", (asset_id,)
            ).fetchone()
        return PhotoMetadataRow.from_row(row) if row else None

    def search(
        self,
        family_id: str,
        *,
        query: str | None = None,
        asset_type: str | None = None,
        space_id: str | None = None,
        content_hash: str | None = None,
        status: str | None = "INDEXED",
        limit: int = 100,
    ) -> list[FamilyAssetRow]:
        clauses = ["family_id = ?"]
        params: list[object] = [family_id]
        if query:
            clauses.append("LOWER(name) LIKE ?")
            params.append(f"%{query.lower()}%")
        if asset_type:
            clauses.append("asset_type = ?")
            params.append(asset_type)
        if space_id:
            clauses.append("space_id = ?")
            params.append(space_id)
        if content_hash:
            clauses.append("content_hash = ?")
            params.append(content_hash)
        if status:
            clauses.append("status = ?")
            params.append(status)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM homemind_family_assets WHERE "
                + " AND ".join(clauses)
                + " ORDER BY COALESCE(captured_at, created_at) DESC, id DESC LIMIT ?",
                params,
            ).fetchall()
        return map_rows(rows, FamilyAssetRow)

    def duplicate_groups(self, family_id: str) -> list[list[FamilyAssetRow]]:
        with self._db.connect() as conn:
            hashes = conn.execute(
                "SELECT content_hash FROM homemind_family_assets WHERE family_id = ? "
                "GROUP BY content_hash HAVING COUNT(*) > 1 ORDER BY content_hash",
                (family_id,),
            ).fetchall()
            groups = []
            for item in hashes:
                rows = conn.execute(
                    "SELECT * FROM homemind_family_assets WHERE family_id = ? AND content_hash = ? "
                    "ORDER BY id",
                    (family_id, item["content_hash"]),
                ).fetchall()
                groups.append(map_rows(rows, FamilyAssetRow))
        return groups

    def delete(self, asset_id: str) -> bool:
        with self._db.transaction() as conn:
            cursor = conn.execute("DELETE FROM homemind_family_assets WHERE asset_id = ?", (asset_id,))
            deleted = int(cursor.rowcount or 0) > 0
        return deleted
