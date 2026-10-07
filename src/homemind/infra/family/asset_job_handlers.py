"""Item handlers for each persistent asset job type.

Each handler receives one :class:`AssetJobItemRow` and is idempotent — a
retry re-invokes it with the same row, and ``asset_id`` / content hash
short-circuits work that is already done.

Handlers never raise for "nothing to do"; they raise only to mark the item
failed, which the job manager isolates per item. :class:`SkipItem` is the
one exception: it marks an item *skipped*, which is a terminal success state
rather than a failure — a video has no thumbnail, and that is not an error.

Handlers are built from the job row rather than from a bare ``job_type`` so
they can read the job's persisted config. Without that, a VISION job started
after a restart would have no way to learn which provider to call.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from homemind.infra.db.repos.asset_jobs import (
    JOB_TYPE_EMBEDDING,
    JOB_TYPE_FACE_MATCH,
    JOB_TYPE_METADATA,
    JOB_TYPE_REINDEX,
    JOB_TYPE_SCAN,
    JOB_TYPE_THUMBNAIL,
    JOB_TYPE_VISION,
    AssetJobItemRow,
    AssetJobRow,
)
from homemind.infra.db.repos.family_assets import FamilyAssetRepo, FamilyAssetRow
from homemind.infra.family.asset_job_config import AssetJobConfig
from homemind.infra.family.assets import FamilyAssetManager
from homemind.infra.family.photo_providers import (
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleVisionProvider,
    require_provider,
)
from homemind.infra.family.thumbnails import ThumbnailService
from octop.infra.users.identity import User

logger = logging.getLogger(__name__)

#: MIME types Pillow can open. Anything else is skipped rather than failed:
#: a family library legitimately contains videos and PDFs.
_IMAGE_MIME_PREFIX = "image/"
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})


class SkipItem(Exception):
    """Raised by a handler to record ``SKIPPED`` instead of ``FAILED``."""


def iter_source_paths(
    directory_uri: str,
    *,
    recursive: bool = True,
) -> Iterator[str]:
    """Stream candidate file paths under a ``file://`` directory.

    Yields lazily so a directory with hundreds of thousands of files
    never lands in memory at once — the job manager writes each batch to
    the DB and reads them back a batch at a time.
    """
    parsed = urlparse(directory_uri)
    if parsed.scheme != "file":
        raise ValueError("asset source is not a local directory")
    root = Path(url2pathname(unquote(parsed.path)))
    if not root.is_dir():
        raise ValueError("asset scan path must be a directory")
    candidates = root.rglob("*") if recursive else root.glob("*")
    for path in candidates:
        if ".homemind-trash" in path.parts or path.is_symlink() or not path.is_file():
            continue
        yield str(path)


def make_scan_item_handler(
    asset_manager: FamilyAssetManager,
    *,
    family_id: str,
    created_by_user_id: int,
) -> object:
    """Handler for ``SCAN`` jobs: index one file into the asset table."""

    def handle(item: AssetJobItemRow) -> None:
        # The manager owns hashing / EXIF / dedup; reuse it so a
        # persistent job and an interactive scan produce identical rows.
        asset_manager.index_path_for_job(
            family_id=family_id,
            path=item.source_path,
            created_by_user_id=created_by_user_id,
        )

    return handle


def make_metadata_item_handler(asset_manager: FamilyAssetManager) -> object:
    """Handler for ``METADATA`` jobs: refresh EXIF / GPS on a known asset."""

    def handle(item: AssetJobItemRow) -> None:
        if not item.asset_id:
            raise ValueError("metadata item is not bound to an asset")
        asset_manager.refresh_metadata_for_job(item.asset_id, item.source_path)

    return handle


def make_thumbnail_item_handler(
    *,
    asset_repo: FamilyAssetRepo,
    thumbnail_service: ThumbnailService,
    family_id: str,
    config: AssetJobConfig,
) -> object:
    """Handler for ``THUMBNAIL``: pre-render so the grid never waits on Pillow.

    Idempotence comes from the cache key, which embeds the asset's content
    hash: re-running after the file changed renders a *new* file rather than
    serving a stale one, and re-running without a change is a cache hit.
    """

    def handle(item: AssetJobItemRow) -> None:
        asset = _require_asset(asset_repo, family_id, item)
        if not _is_image(asset.mime_type, asset.name):
            raise SkipItem(f"{asset.mime_type or 'unknown'} is not an image")
        result = thumbnail_service.build_for_asset(
            family_id,
            asset,
            width=config.thumbnail_width,
            height=config.thumbnail_height,
            requested_format=config.thumbnail_format,
        )
        if result is None:
            # A corrupt file is a real failure for this item, but only for
            # this item — a batch of 10k photos must not die on one.
            raise ValueError(f"thumbnail render failed for asset {asset.id}")

    return handle


def make_reindex_item_handler(
    *,
    asset_repo: FamilyAssetRepo,
    search_indexer: object,
    family_id: str,
) -> object:
    """Handler for ``REINDEX``: project one asset into the search index.

    One entity per item is the point: an item is individually retryable,
    and a 50k-asset family never has to fit in a single work unit. The
    family id is threaded into every call so a stale job can never write
    another family's documents.
    """

    def handle(item: AssetJobItemRow) -> None:
        if not item.asset_id:
            raise ValueError("reindex item is not bound to an asset")
        asset = asset_repo.get(item.asset_id)
        if asset is None:
            # The asset was deleted after the job was queued. Leaving its
            # document behind would make a removed photo keep showing up
            # in search, so removal is the correct outcome, not a failure.
            _remove_asset_documents(search_indexer, family_id, item.asset_id)
            return
        if asset.family_id != family_id:
            raise ValueError("asset does not belong to this family")
        search_indexer.index_asset(family_id, asset)  # type: ignore[attr-defined]

    return handle


def make_vision_item_handler(
    *,
    asset_repo: FamilyAssetRepo,
    photo_intelligence: Any,
    provider_repo: Any,
    family_manager: Any,
    user: User,
    family_id: str,
    config: AssetJobConfig,
) -> object:
    """Handler for ``VISION``: describe one photo with the configured model.

    The provider is resolved *per item* rather than once at construction:
    a provider can be disabled or deleted between queueing and execution,
    and a job that silently no-ops in that case would look successful while
    producing nothing.
    """

    def handle(item: AssetJobItemRow) -> None:
        asset = _require_asset(asset_repo, family_id, item)
        if not _is_image(asset.mime_type, asset.name):
            raise SkipItem(f"{asset.mime_type or 'unknown'} is not an image")
        provider_id = config.vision_provider_id
        model = config.vision_model
        if provider_id is None or model is None:
            raise ValueError("VISION job is missing its provider configuration")
        provider_row = require_provider(provider_repo.get(provider_id))
        vision = OpenAICompatibleVisionProvider(provider_row, model)
        photo_intelligence.analyze(
            family_id,
            asset.id,
            user,
            vision=vision,
        )

    return handle


def make_embedding_item_handler(
    *,
    asset_repo: FamilyAssetRepo,
    photo_intelligence: Any,
    provider_repo: Any,
    user: User,
    family_id: str,
    config: AssetJobConfig,
) -> object:
    """Handler for ``EMBEDDING``: vectorise one photo for similarity search.

    Vectors are never compared across models, so the provenance recorded
    alongside each vector must match this job's model. ``analyze`` stores
    the provider name; the dimension check here catches a model that
    changed shape between runs.
    """

    def handle(item: AssetJobItemRow) -> None:
        asset = _require_asset(asset_repo, family_id, item)
        if not _is_image(asset.mime_type, asset.name):
            raise SkipItem(f"{asset.mime_type or 'unknown'} is not an image")
        provider_id = config.embedding_provider_id
        model = config.embedding_model
        if provider_id is None or model is None:
            raise ValueError("EMBEDDING job is missing its provider configuration")
        provider_row = require_provider(provider_repo.get(provider_id))
        embedding = OpenAICompatibleEmbeddingProvider(provider_row, model)
        row = photo_intelligence.analyze(
            family_id,
            asset.id,
            user,
            embedding=embedding,
        )
        if row.embedding_json is None:
            raise ValueError("embedding provider returned no vector")
        vector = json.loads(row.embedding_json)
        if not vector:
            raise ValueError("photo embedding cannot be empty")
        _assert_stable_dimension(photo_intelligence, family_id, asset.id, len(vector))

    return handle


def _assert_stable_dimension(
    photo_intelligence: Any,
    family_id: str,
    asset_id: str,
    dimensions: int,
) -> None:
    """Refuse to mix vector sizes inside one family.

    Cosine similarity between vectors of different lengths is a number, not
    a similarity. Silently truncating or padding would quietly corrupt
    every neighbour score, so the mismatch is surfaced instead.
    """
    repo = getattr(photo_intelligence, "repo", None)
    if repo is None:
        return
    for other in repo.list(family_id):
        if other.asset_id == asset_id or not other.embedding_json:
            continue
        try:
            other_dimensions = len(json.loads(other.embedding_json))
        except (TypeError, ValueError):
            continue
        if other_dimensions != dimensions:
            raise ValueError(
                f"embedding dimension changed from {other_dimensions} to {dimensions}; "
                "reindex the family before mixing models",
            )


def _remove_asset_documents(search_indexer: object, family_id: str, asset_id: str) -> None:
    remove = getattr(search_indexer, "remove_asset", None)
    if remove is None:
        return
    try:
        remove(family_id, asset_id)
    except Exception as exc:  # noqa: BLE001 — never fail the batch
        logger.warning("reindex could not remove asset %s: %s", asset_id, exc)


def _require_asset(
    asset_repo: FamilyAssetRepo,
    family_id: str,
    item: AssetJobItemRow,
) -> FamilyAssetRow:
    """Load the item's asset, refusing anything outside this family."""
    if not item.asset_id:
        raise ValueError("item is not bound to an asset")
    asset = asset_repo.get(item.asset_id)
    if asset is None:
        raise ValueError(f"asset {item.asset_id} no longer exists")
    if asset.family_id != family_id:
        # Defence in depth: creation already refuses cross-family ids, so
        # reaching here means the row moved families underneath the job.
        raise ValueError("asset does not belong to this family")
    return asset


def _is_image(mime_type: str | None, name: str) -> bool:
    mime = (mime_type or "").split(";")[0].strip().lower()
    if mime in _IMAGE_MIME_TYPES:
        return True
    if mime.startswith(_IMAGE_MIME_PREFIX):
        return True
    return Path(name).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def make_face_match_item_handler(
    *,
    asset_repo: FamilyAssetRepo,
    face_manager: Any,
    user: User,
    family_id: str,
) -> object:
    """Handler for ``FACE_MATCH``: propose who is in a photo.

    Every result becomes a *candidate*, never a label. A provider's
    confidence is a suggestion, and turning one into "this is your son"
    without review is the failure this design exists to prevent. A family
    with no reference photos is skipped rather than failed: there is
    nothing to match against, and that is not an error.
    """

    def handle(item: AssetJobItemRow) -> None:
        asset = _require_asset(asset_repo, family_id, item)
        if not _is_image(asset.mime_type, asset.name):
            raise SkipItem(f"{asset.mime_type or 'unknown'} is not an image")
        face_manager.match_faces(family_id, asset.id, user)

    return handle


def build_handler(
    job: AssetJobRow,
    *,
    asset_manager: FamilyAssetManager,
    asset_repo: FamilyAssetRepo,
    thumbnail_service: ThumbnailService | None = None,
    search_indexer: object | None = None,
    photo_intelligence: Any | None = None,
    provider_repo: Any | None = None,
    face_manager: Any | None = None,
    family_manager: Any | None = None,
    user: User | None = None,
    family_id: str,
    created_by_user_id: int,
) -> object:
    """Pick the handler for ``job``.

    Built from the row, not the type, so a handler can read the job's
    persisted config after a restart. A job that cannot do its work fails
    loudly at construction rather than reporting success.
    """
    config = AssetJobConfig.from_json(job.config_json)
    if job.job_type in {JOB_TYPE_VISION, JOB_TYPE_EMBEDDING} and user is None:
        raise NotImplementedError(
            f"{job.job_type} job requires an acting user",
        )
    if job.job_type == JOB_TYPE_SCAN:
        return make_scan_item_handler(
            asset_manager,
            family_id=family_id,
            created_by_user_id=created_by_user_id,
        )
    if job.job_type == JOB_TYPE_METADATA:
        return make_metadata_item_handler(asset_manager)
    if job.job_type == JOB_TYPE_THUMBNAIL:
        if thumbnail_service is None:
            raise NotImplementedError("THUMBNAIL job requires a thumbnail service")
        return make_thumbnail_item_handler(
            asset_repo=asset_repo,
            thumbnail_service=thumbnail_service,
            family_id=family_id,
            config=config,
        )
    if job.job_type == JOB_TYPE_REINDEX:
        if search_indexer is None:
            raise NotImplementedError("REINDEX job requires a search indexer")
        return make_reindex_item_handler(
            asset_repo=asset_repo,
            search_indexer=search_indexer,
            family_id=family_id,
        )
    if user is None and job.job_type in {
        JOB_TYPE_VISION,
        JOB_TYPE_EMBEDDING,
        JOB_TYPE_FACE_MATCH,
    }:
        raise NotImplementedError(f"{job.job_type} job requires an acting user")
    if job.job_type == JOB_TYPE_FACE_MATCH:
        if face_manager is None:
            raise NotImplementedError("FACE_MATCH job requires a face manager")
        assert user is not None  # narrowed by the guard above
        return make_face_match_item_handler(
            asset_repo=asset_repo,
            face_manager=face_manager,
            user=user,
            family_id=family_id,
        )
    if job.job_type in {JOB_TYPE_VISION, JOB_TYPE_EMBEDDING}:
        if photo_intelligence is None or provider_repo is None:
            raise NotImplementedError(
                f"{job.job_type} job requires a photo intelligence service",
            )
        assert user is not None  # narrowed by the guard above
        if job.job_type == JOB_TYPE_VISION:
            return make_vision_item_handler(
                asset_repo=asset_repo,
                photo_intelligence=photo_intelligence,
                provider_repo=provider_repo,
                family_manager=family_manager,
                user=user,
                family_id=family_id,
                config=config,
            )
        return make_embedding_item_handler(
            asset_repo=asset_repo,
            photo_intelligence=photo_intelligence,
            provider_repo=provider_repo,
            user=user,
            family_id=family_id,
            config=config,
        )
    raise NotImplementedError(f"no handler registered for asset job type {job.job_type!r}")


__all__ = [
    "SkipItem",
    "build_handler",
    "iter_source_paths",
    "make_metadata_item_handler",
    "make_reindex_item_handler",
    "make_scan_item_handler",
    "make_thumbnail_item_handler",
]
