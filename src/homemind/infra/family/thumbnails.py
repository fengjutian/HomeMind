"""Thumbnail generation and serving for family assets (Stage 6).

Cache lives under HomeMind's own data directory — never next to the
original file, because the source tree may be read-only, may be a NAS
mount, and may be shared with other applications.

Security rules enforced here:

* Cache keys are derived from ``asset.content_hash`` (plus the asset id),
  so a path traversal through the cache key is impossible by
  construction.
* The requested size is clamped before it reaches Pillow; an unbounded
  ``width`` would let a caller allocate a gigapixel bitmap.
* Permission is checked *before* the file is opened, not after.
* Errors degrade gracefully: a missing codec returns ``None`` rather than
  a 500, so the dashboard can fall back to the original.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homemind.infra.db.repos.family_assets import FamilyAssetRow
from homemind.infra.family.manager import FamilyManager
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)


# Hard ceiling so a caller cannot request a bitmap larger than memory
# allows. The dashboard asks for at most 1024 px.
MAX_THUMBNAIL_DIMENSION = 2048
DEFAULT_THUMBNAIL_WIDTH = 320
DEFAULT_THUMBNAIL_HEIGHT = 320

# Output format. WebP is the default because it is small; JPEG is used
# when the caller cannot handle WebP.
THUMBNAIL_FORMAT_WEBP = "WEBP"
THUMBNAIL_FORMAT_JPEG = "JPEG"

_SUPPORTED_FORMATS = {
    "webp": THUMBNAIL_FORMAT_WEBP,
    "jpg": THUMBNAIL_FORMAT_JPEG,
    "jpeg": THUMBNAIL_FORMAT_JPEG,
}


@dataclass(frozen=True)
class ThumbnailResult:
    """Either a cached file, or ``None`` when it could not be built."""

    path: Path
    width: int
    height: int
    format: str


def resolve_output_format(requested: str | None) -> str:
    """Map a request's ``format`` query to a Pillow format name."""
    if not requested:
        return THUMBNAIL_FORMAT_WEBP
    return _SUPPORTED_FORMATS.get(requested.strip().lower(), THUMBNAIL_FORMAT_WEBP)


def clamp_dimension(value: int | None, *, fallback: int) -> int:
    """Clamp a requested width / height into a sane, bounded range."""
    if not value or value <= 0:
        return fallback
    return min(int(value), MAX_THUMBNAIL_DIMENSION)


def cache_key(asset: FamilyAssetRow, *, width: int, height: int, fmt: str) -> str:
    """Deterministic cache filename.

    Derived only from server-side fields (asset id + content hash + the
    clamped dimensions), so no part of the key can be influenced into a
    directory traversal.
    """
    digest = hashlib.sha256(
        f"{asset.id}:{asset.content_hash}:{width}:{height}:{fmt}".encode()
    ).hexdigest()
    suffix = "webp" if fmt == THUMBNAIL_FORMAT_WEBP else "jpg"
    return f"{asset.id}_{digest[:32]}.{suffix}"


def _open_asset_path(asset: FamilyAssetRow) -> Path:
    """Resolve an asset's ``uri`` to a local path.

    Only ``file://`` URIs are accepted — a remote asset must be fetched
    through the storage backend, not by opening a network path here.
    """
    from urllib.parse import unquote, urlparse
    from urllib.request import url2pathname

    parsed = urlparse(asset.uri)
    if parsed.scheme != "file":
        raise ValueError("asset is not backed by a local file")
    return Path(url2pathname(unquote(parsed.path)))


def _resample_filter() -> Any:
    """Pillow's LANCZOS constant moved between major versions."""
    from PIL import Image  # noqa: PLC0415

    return getattr(Image, "Resampling", Image).LANCZOS


def _safe_segment(value: str) -> str:
    """Reduce ``value`` to a single path segment.

    Any character outside ``[A-Za-z0-9_-]`` becomes ``_``, so ``../``,
    an absolute path, or a nested path all collapse to one harmless
    directory name. Length is capped so a caller cannot create an
    over-long component.
    """
    cleaned = "".join(char if (char.isalnum() or char in "-_") else "_" for char in str(value))
    return cleaned[:128] or "_"


class ThumbnailService:
    """Build and cache thumbnails under HomeMind's data directory."""

    def __init__(
        self,
        family: FamilyManager,
        cache_dir: Path,
        *,
        asset_repo: Any | None = None,
        permission_evaluator: Any | None = None,
    ) -> None:
        self._family = family
        self._cache_dir = Path(cache_dir)
        self._asset_repo = asset_repo
        self._permissions = permission_evaluator

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def cache_path(
        self,
        family_id: str,
        asset: FamilyAssetRow,
        *,
        width: int,
        height: int,
        fmt: str,
    ) -> Path:
        """Absolute cache location for one rendered size.

        ``family_id`` arrives from the URL path, so it is sanitised
        before being joined: an untrusted ``../../etc`` would otherwise
        let a caller steer the write outside ``cache_dir``. The asset id
        is checked against the stored row, so the cache key itself can
        never contain a separator.
        """
        key = cache_key(asset, width=width, height=height, fmt=fmt)
        return self._cache_dir / _safe_segment(family_id) / key

    def get_or_build(
        self,
        family_id: str,
        asset_id: str,
        user: User,
        *,
        width: int | None = None,
        height: int | None = None,
        requested_format: str | None = None,
    ) -> ThumbnailResult | None:
        """Return a cached thumbnail, rendering it on first request.

        Returns ``None`` when the caller may not read the asset, when the
        file is gone, or when Pillow cannot decode it — the caller then
        falls back to the original asset.
        """
        self._family.require_access(family_id, user)
        asset = self._asset_repo.get(asset_id) if self._asset_repo is not None else None
        if asset is None or asset.family_id != family_id:
            return None
        # Permission is checked before any filesystem access.
        if not self._can_read(family_id, user, asset):
            return None

        target_width = clamp_dimension(width, fallback=DEFAULT_THUMBNAIL_WIDTH)
        target_height = clamp_dimension(height, fallback=DEFAULT_THUMBNAIL_HEIGHT)
        fmt = resolve_output_format(requested_format)

        destination = self.cache_path(
            family_id,
            asset,
            width=target_width,
            height=target_height,
            fmt=fmt,
        )
        if destination.exists():
            return ThumbnailResult(
                path=destination,
                width=target_width,
                height=target_height,
                format=fmt,
            )

        try:
            source = _open_asset_path(asset)
        except ValueError:
            return None
        if not source.is_file():
            return None

        rendered = self._render(source, destination, target_width, target_height, fmt)
        if rendered is None:
            return None
        return ThumbnailResult(
            path=rendered.path,
            width=rendered.width,
            height=rendered.height,
            format=rendered.format,
        )

    def build_for_asset(
        self,
        family_id: str,
        asset: FamilyAssetRow,
        *,
        width: int | None = None,
        height: int | None = None,
        requested_format: str | None = None,
    ) -> ThumbnailResult | None:
        """Render a thumbnail with no end user in the loop.

        A background job runs as the system, not as the member who queued
        it, so there is no ``User`` to evaluate a permission against. The
        caller is responsible for having established that the asset belongs
        to ``family_id`` — :meth:`get_or_build` does that check before it
        reaches here, and so must any job handler.
        """
        target_width = clamp_dimension(width, fallback=DEFAULT_THUMBNAIL_WIDTH)
        target_height = clamp_dimension(height, fallback=DEFAULT_THUMBNAIL_HEIGHT)
        fmt = resolve_output_format(requested_format)
        destination = self.cache_path(
            family_id,
            asset,
            width=target_width,
            height=target_height,
            fmt=fmt,
        )
        if destination.exists():
            return ThumbnailResult(
                path=destination,
                width=target_width,
                height=target_height,
                format=fmt,
            )
        try:
            source = _open_asset_path(asset)
        except ValueError:
            return None
        if not source.is_file():
            return None
        return self._render(source, destination, target_width, target_height, fmt)

    def _render(
        self,
        source: Path,
        destination: Path,
        width: int,
        height: int,
        fmt: str,
    ) -> ThumbnailResult | None:
        try:
            from PIL import Image, ImageOps  # noqa: PLC0415
        except ImportError:
            logger.info("Pillow unavailable; skipping thumbnail render")
            return None
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as opened:
                # EXIF rotation must be applied before resizing or the
                # thumbnail comes out sideways.
                image = ImageOps.exif_transpose(opened)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGB")
                thumbnail = image.copy()
                thumbnail.thumbnail((width, height), _resample_filter())
                save_kwargs = (
                    {"quality": 82, "method": 4}
                    if fmt == THUMBNAIL_FORMAT_WEBP
                    else {"quality": 85, "optimize": True}
                )
                thumbnail.save(destination, fmt, **save_kwargs)
                actual_width, actual_height = thumbnail.size
        except Exception as exc:  # noqa: BLE001 — degrade, do not 500
            logger.info(
                "thumbnail render failed for %s: %s",
                source.name,
                exc,
            )
            return None
        return ThumbnailResult(
            path=destination,
            width=actual_width,
            height=actual_height,
            format=fmt,
        )

    # ------------------------------------------------------------- helpers

    def _can_read(self, family_id: str, user: User, asset: FamilyAssetRow) -> bool:
        if self._permissions is None:
            return False
        from homemind.infra.family.permissions import PermissionEffect  # noqa: PLC0415

        decision = self._permissions.evaluate(
            family_id=family_id,
            user=user,
            action="asset.read",
            asset=asset,
        )
        return decision.effect is PermissionEffect.ALLOW


__all__ = [
    "DEFAULT_THUMBNAIL_HEIGHT",
    "DEFAULT_THUMBNAIL_WIDTH",
    "MAX_THUMBNAIL_DIMENSION",
    "THUMBNAIL_FORMAT_JPEG",
    "THUMBNAIL_FORMAT_WEBP",
    "ThumbnailResult",
    "ThumbnailService",
    "cache_key",
    "clamp_dimension",
    "resolve_output_format",
]
