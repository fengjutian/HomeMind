"""Item handlers for each persistent asset job type (Stage 6).

Each handler receives one :class:`AssetJobItemRow` and is idempotent —
a retry re-invokes it with the same row, and ``asset_id`` / ``content
hash`` short-circuits work that is already done.

Handlers never raise for "nothing to do"; they raise only to mark the
item failed, which the job manager isolates per item.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from homemind.infra.db.repos.asset_jobs import (
    ITEM_STATUS_SKIPPED,
    JOB_TYPE_METADATA,
    JOB_TYPE_SCAN,
    AssetJobItemRow,
)
from homemind.infra.db.repos.family_assets import FamilyAssetRepo
from homemind.infra.family.assets import FamilyAssetManager

logger = logging.getLogger(__name__)


def iter_source_paths(
    directory_uri: str, *, recursive: bool = True,
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


def build_handler(
    job_type: str,
    *,
    asset_manager: FamilyAssetManager,
    asset_repo: FamilyAssetRepo,
    family_id: str,
    created_by_user_id: int,
) -> object:
    """Pick the handler for ``job_type``.

    Job types that need an external provider (vision / embedding / face
    match) raise here rather than silently doing nothing — a job that
    cannot do its work must fail loudly, not report success.
    """
    if job_type == JOB_TYPE_SCAN:
        return make_scan_item_handler(
            asset_manager,
            family_id=family_id,
            created_by_user_id=created_by_user_id,
        )
    if job_type == JOB_TYPE_METADATA:
        return make_metadata_item_handler(asset_manager)
    raise NotImplementedError(
        f"no handler registered for asset job type {job_type!r}",
    )


__all__ = [
    "ITEM_STATUS_SKIPPED",
    "build_handler",
    "iter_source_paths",
    "make_metadata_item_handler",
    "make_scan_item_handler",
]
