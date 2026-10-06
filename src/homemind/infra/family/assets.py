"""Read-only local asset scanner and family asset orchestration."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from zoneinfo import ZoneInfo

from homemind.infra.db.repos.families import FamilyRepo
from homemind.infra.db.repos.family_assets import (
    FamilyAssetRepo,
    FamilyAssetRow,
    FamilyAssetSourceRow,
    PhotoMetadataRow,
)
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import (
    FamilyPermissionEvaluator,
    PermissionEffect,
)
from octop.infra.db.repos._base import now_ts
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import User


@dataclass(frozen=True)
class PhotoMetadata:
    width: int | None = None
    height: int | None = None
    camera_make: str | None = None
    camera_model: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    taken_at: int | None = None


@dataclass(frozen=True)
class AssetScanResult:
    source_id: str
    indexed: int
    unchanged: int
    skipped: int
    failed: int
    missing: int
    asset_ids: list[str]
    errors: list[str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_type(mime_type: str) -> str:
    if mime_type.startswith("image/"):
        return "PHOTO"
    if mime_type.startswith("video/"):
        return "VIDEO"
    if mime_type.startswith("audio/"):
        return "AUDIO"
    if mime_type.startswith("text/") or mime_type in {
        "application/pdf",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return "DOCUMENT"
    return "OTHER"


def _ratio(value: Any) -> float:
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        return float(value.numerator) / float(value.denominator)
    return float(value)


def _gps_coordinate(values: Any, reference: Any) -> float | None:
    if not values or len(values) != 3:
        return None
    degrees, minutes, seconds = (_ratio(item) for item in values)
    coordinate = degrees + minutes / 60 + seconds / 3600
    ref = reference.decode(errors="ignore") if isinstance(reference, bytes) else str(reference)
    return -coordinate if ref.upper() in {"S", "W"} else coordinate


def _photo_metadata(path: Path, timezone: ZoneInfo) -> PhotoMetadata:
    try:
        from PIL import ExifTags, Image

        with Image.open(path) as image:
            width, height = image.size
            exif = image.getexif()
            make = exif.get(ExifTags.Base.Make)
            model = exif.get(ExifTags.Base.Model)
            date_text = exif.get(ExifTags.Base.DateTimeOriginal) or exif.get(
                ExifTags.Base.DateTime
            )
            taken_at = None
            if isinstance(date_text, str):
                try:
                    captured = datetime.strptime(date_text, "%Y:%m:%d %H:%M:%S")
                    taken_at = int(captured.replace(tzinfo=timezone).timestamp())
                except ValueError:
                    taken_at = None
            latitude = longitude = None
            gps = exif.get_ifd(ExifTags.IFD.GPSInfo) if ExifTags.IFD.GPSInfo in exif else {}
            if gps:
                latitude = _gps_coordinate(
                    gps.get(ExifTags.GPS.GPSLatitude), gps.get(ExifTags.GPS.GPSLatitudeRef)
                )
                longitude = _gps_coordinate(
                    gps.get(ExifTags.GPS.GPSLongitude), gps.get(ExifTags.GPS.GPSLongitudeRef)
                )
            return PhotoMetadata(
                width=width,
                height=height,
                camera_make=str(make).strip() if make else None,
                camera_model=str(model).strip() if model else None,
                latitude=latitude,
                longitude=longitude,
                taken_at=taken_at,
            )
    except (AttributeError, OSError, ValueError, TypeError):
        return PhotoMetadata()


class FamilyAssetManager:
    def __init__(
        self,
        family_repo: FamilyRepo,
        asset_repo: FamilyAssetRepo,
        *,
        permission_evaluator: FamilyPermissionEvaluator | None = None,
    ) -> None:
        self.family = FamilyManager(family_repo)
        self.repo = asset_repo
        self.permissions = permission_evaluator or FamilyPermissionEvaluator(family_repo)

    def scan_directory(
        self,
        family_id: str,
        user: User,
        *,
        directory: str,
        space_id: str | None = None,
        recursive: bool = True,
        visibility: str = "FAMILY",
    ) -> AssetScanResult:
        family = self.family.require_manager(family_id, user)
        if space_id is not None:
            space = self.family.repo.get_space(space_id)
            if space is None or space.family_id != family_id:
                raise OctopError(ErrorCode.NOT_FOUND, "family space not found")
            if visibility == "PRIVATE" and space.owner_member_id is None:
                raise HomeMindError(
                    HomeMindErrorCode.FAMILY_INVALID,
                    "private assets require a member-owned space",
                )
        elif visibility in {"PRIVATE", "SENSITIVE"}:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "private and sensitive assets require a space",
            )
        root = Path(directory).expanduser().resolve()
        if not root.is_dir():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "asset scan path must be a directory"
            )
        source = self.repo.upsert_source(
            family_id=family_id,
            space_id=space_id,
            directory_uri=root.as_uri(),
            recursive=recursive,
            visibility=visibility,
            created_by=user.id,
        )
        return self._execute_scan(
            family_id=family_id,
            family_timezone=family.timezone,
            source=source,
            created_by_user_id=user.id,
        )

    def scan_source_internal(
        self,
        family_id: str,
        source_id: str,
        *,
        created_by_user_id: int,
    ) -> AssetScanResult:
        """System-side variant of :meth:`scan_source` that skips
        ``require_manager``. Used by the periodic re-scan job.
        """
        source = self.repo.get_source(source_id)
        if source is None or source.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family asset source not found")
        family = self.family.repo.get_family(family_id)
        if family is None:
            raise OctopError(ErrorCode.NOT_FOUND, "family not found")
        return self._execute_scan(
            family_id=family_id,
            family_timezone=family.timezone,
            source=source,
            created_by_user_id=created_by_user_id,
        )

    def _execute_scan(
        self,
        *,
        family_id: str,
        family_timezone: str,
        source: FamilyAssetSourceRow,
        created_by_user_id: int,
    ) -> AssetScanResult:
        parsed = urlparse(source.directory_uri)
        if parsed.scheme != "file":
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "asset source is not a local directory"
            )
        root = Path(url2pathname(unquote(parsed.path)))
        if not root.is_dir():
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID, "asset scan path must be a directory"
            )
        recursive = bool(source.recursive)
        visibility = source.visibility
        space_id = source.space_id
        candidates = root.rglob("*") if recursive else root.glob("*")
        indexed: list[str] = []
        seen: list[str] = []
        unchanged = 0
        skipped = 0
        errors: list[str] = []
        timezone = ZoneInfo(family_timezone)
        for path in candidates:
            if ".homemind-trash" in path.parts or path.is_symlink() or not path.is_file():
                skipped += 1
                continue
            try:
                uri = path.resolve().as_uri()
                existing = self.repo.get_by_uri(family_id, uri)
                stat = path.stat()
                if existing is not None:
                    seen.append(existing.id)
                if existing is not None and self._is_unchanged(
                    existing, stat.st_size, stat.st_mtime
                ):
                    self.repo.touch_asset(existing.id, now_ts())
                    unchanged += 1
                    continue
                asset = self._index_file(
                    family_id,
                    created_by_user_id,
                    source_id=source.id,
                    root=root,
                    path=path,
                    stat=stat,
                    space_id=space_id,
                    visibility=visibility,
                    timezone=timezone,
                )
                if existing is None:
                    seen.append(asset.id)
                indexed.append(asset.id)
            except (OSError, ValueError) as exc:
                errors.append(f"{path.name}: {exc}")
        timestamp = now_ts()
        missing = self.repo.mark_missing(source.id, seen, timestamp)
        self.repo.finish_source_scan(source.id, timestamp)
        return AssetScanResult(
            source_id=source.id,
            indexed=len(indexed),
            unchanged=unchanged,
            skipped=skipped,
            failed=len(errors),
            missing=missing,
            asset_ids=indexed,
            errors=errors,
        )

    def _index_file(
        self,
        family_id: str,
        created_by_user_id: int,
        *,
        source_id: str,
        root: Path,
        path: Path,
        stat: os.stat_result,
        space_id: str | None,
        visibility: str,
        timezone: ZoneInfo,
    ) -> FamilyAssetRow:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        kind = _asset_type(mime_type)
        photo = _photo_metadata(path, timezone) if kind == "PHOTO" else PhotoMetadata()
        captured_at = photo.taken_at or int(stat.st_mtime)
        metadata = {
            "relative_path": path.relative_to(root).as_posix(),
            "modified_at": int(stat.st_mtime),
        }
        asset = self.repo.upsert_asset(
            family_id=family_id,
            source_id=source_id,
            space_id=space_id,
            asset_type=kind,
            name=path.name,
            uri=path.resolve().as_uri(),
            mime_type=mime_type,
            size_bytes=stat.st_size,
            content_hash=_sha256(path),
            captured_at=captured_at,
            metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            created_by=created_by_user_id,
            visibility=visibility,
        )
        if kind == "PHOTO":
            self.repo.upsert_photo_metadata(asset.id, **asdict(photo))
        return asset

    @staticmethod
    def _is_unchanged(asset: FamilyAssetRow, size_bytes: int, modified_at: float) -> bool:
        try:
            metadata = json.loads(asset.metadata_json)
        except (TypeError, ValueError):
            return False
        return asset.size_bytes == size_bytes and metadata.get("modified_at") == int(modified_at)

    def list_sources(self, family_id: str, user: User) -> list[FamilyAssetSourceRow]:
        self.family.require_manager(family_id, user)
        return self.repo.list_sources(family_id)

    def scan_source(
        self, family_id: str, source_id: str, user: User
    ) -> AssetScanResult:
        self.family.require_manager(family_id, user)
        return self.scan_source_internal(
            family_id, source_id, created_by_user_id=user.id,
        )

    def search(
        self,
        family_id: str,
        user: User,
        *,
        query: str | None = None,
        asset_type: str | None = None,
        space_id: str | None = None,
        content_hash: str | None = None,
        status: str | None = "INDEXED",
        limit: int = 100,
    ) -> list[FamilyAssetRow]:
        self.family.require_access(family_id, user)
        rows = self.repo.search(
            family_id,
            query=query,
            asset_type=asset_type,
            space_id=space_id,
            content_hash=content_hash,
            status=status,
            limit=limit,
        )
        return [row for row in rows if self._can_read(row, user)]

    def get(self, family_id: str, asset_id: str, user: User) -> FamilyAssetRow:
        self.family.require_access(family_id, user)
        asset = self.repo.get(asset_id)
        if asset is None or asset.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family asset not found")
        if not self._can_read(asset, user):
            raise OctopError(ErrorCode.FORBIDDEN, "family asset access denied")
        return asset

    def photo_metadata(
        self, family_id: str, asset_id: str, user: User
    ) -> PhotoMetadataRow | None:
        self.get(family_id, asset_id, user)
        return self.repo.get_photo_metadata(asset_id)

    def local_content_path(self, family_id: str, asset_id: str, user: User) -> Path:
        asset = self.get(family_id, asset_id, user)
        parsed = urlparse(asset.uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise HomeMindError(
                HomeMindErrorCode.FAMILY_INVALID,
                "asset content is not stored in a local file",
            )
        raw_path = url2pathname(unquote(parsed.path))
        if os.name == "nt" and raw_path.startswith("\\") and raw_path[2:3] == ":":
            raw_path = raw_path[1:]
        path = Path(raw_path)
        if asset.status != "INDEXED" or not path.is_file():
            raise OctopError(ErrorCode.NOT_FOUND, "family asset content not found")
        return path

    def duplicate_groups(
        self, family_id: str, user: User
    ) -> list[list[FamilyAssetRow]]:
        self.family.require_access(family_id, user)
        groups: list[list[FamilyAssetRow]] = []
        for group in self.repo.duplicate_groups(family_id):
            visible = [row for row in group if self._can_read(row, user)]
            if len(visible) > 1:
                groups.append(visible)
        return groups

    def delete_index(self, family_id: str, asset_id: str, user: User) -> None:
        self.family.require_manager(family_id, user)
        self.get(family_id, asset_id, user)
        self.repo.delete(asset_id)

    def _can_read(self, asset: FamilyAssetRow, user: User) -> bool:
        action = f"{asset.asset_type.lower()}.read"
        decision = self.permissions.evaluate(
            family_id=asset.family_id,
            user=user,
            action=action,
            space_id=asset.space_id,
            asset=asset,
        )
        return decision.effect is PermissionEffect.ALLOW
