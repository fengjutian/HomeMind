"""HTTP adapters for persistent asset jobs (Stage 6).

Job control (pause / resume / cancel / retry) is manager-only. Listing a
job and its items is available to any family member, subject to the same
family-access check as the rest of the family surface.

Thumbnails are served from :mod:`homemind.infra.family.thumbnails`, which
resolves the asset by id (never by caller-supplied path) and checks
permission before opening a file.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.asset_jobs import AssetJobItemRow
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.asset_job_handlers import iter_source_paths
from homemind.infra.family.asset_jobs import AssetJobManager, AssetJobSummary
from homemind.infra.family.manager import FamilyManager
from homemind.infra.family.permissions import FamilyPermissionEvaluator
from homemind.infra.family.thumbnails import ThumbnailService
from octop.api.deps import current_user, get_server
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]


class AssetJobCreateBody(BaseModel):
    job_type: str = Field(
        description="SCAN | METADATA | THUMBNAIL | VISION | EMBEDDING | FACE_MATCH | REINDEX",
    )
    source_id: str | None = Field(
        default=None,
        description="Asset source to walk. Required for SCAN jobs; omit to operate on explicit paths.",
    )
    paths: list[str] | None = Field(
        default=None,
        description=(
            "Explicit source paths. Mutually exclusive with ``source_id``; "
            "prefer ``source_id`` for large directories so paths are streamed."
        ),
    )


class AssetJobResponse(BaseModel):
    job_id: str
    family_id: str
    job_type: str
    status: str
    total_items: int
    processed_items: int
    succeeded_items: int
    skipped_items: int
    failed_items: int
    progress_percent: float
    error_summary: str | None


class AssetJobItemResponse(BaseModel):
    item_id: str
    asset_id: str | None
    source_path: str
    status: str
    attempt_count: int
    error: str | None


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _family(services: HomeMindServices) -> FamilyManager:
    return FamilyManager(services.family_repo)


def _manager(services: HomeMindServices) -> AssetJobManager:
    return AssetJobManager(_family(services), services.asset_job_repo)


def _summary_response(summary: AssetJobSummary) -> AssetJobResponse:
    percent = (
        round(summary.processed_items * 100.0 / summary.total_items, 2)
        if summary.total_items > 0
        else 0.0
    )
    return AssetJobResponse(
        job_id=summary.job_id,
        family_id=summary.family_id,
        job_type=summary.job_type,
        status=summary.status,
        total_items=summary.total_items,
        processed_items=summary.processed_items,
        succeeded_items=summary.succeeded_items,
        skipped_items=summary.skipped_items,
        failed_items=summary.failed_items,
        progress_percent=percent,
        error_summary=summary.error_summary,
    )


def _item_response(row: AssetJobItemRow) -> AssetJobItemResponse:
    return AssetJobItemResponse(
        item_id=row.id,
        asset_id=row.asset_id,
        source_path=row.source_path,
        status=row.status,
        attempt_count=row.attempt_count,
        error=row.error,
    )


@router.post(
    "/{family_id}/asset-jobs",
    response_model=AssetJobResponse,
    status_code=202,
    summary="Create a persistent asset job",
    description=(
        "Queues work for a background worker. Items are written to the "
        "database in batches, so a source with hundreds of thousands of "
        "files never has to fit in memory. Returns 202: the job has been "
        "accepted, not completed."
    ),
)
async def create_asset_job(
    family_id: str,
    body: AssetJobCreateBody,
    server: Server,
    user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    family = _family(services)
    family.require_manager(family_id, user)
    manager = _manager(services)

    if body.source_id is None and not body.paths:
        raise OctopError(
            ErrorCode.NOT_FOUND, "provide either source_id or paths",
        )

    if body.source_id is not None:
        source = services.family_asset_repo.get_source(body.source_id)
        if source is None or source.family_id != family_id:
            raise OctopError(ErrorCode.NOT_FOUND, "family asset source not found")
        # Streamed: the generator is consumed in batches by the manager.
        paths = iter_source_paths(
            source.directory_uri, recursive=bool(source.recursive),
        )
        job = manager.create_job(
            family_id,
            user,
            job_type=body.job_type,
            paths=paths,
            source_id=body.source_id,
        )
    else:
        job = manager.create_job(
            family_id,
            user,
            job_type=body.job_type,
            paths=body.paths or [],
        )
    return _summary_response(AssetJobSummary.from_row(job))


@router.get(
    "/{family_id}/asset-jobs",
    response_model=list[AssetJobResponse],
    summary="List asset jobs for a family",
)
async def list_asset_jobs(
    family_id: str,
    server: Server,
    user: CurrentUser,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[AssetJobResponse]:
    services = _services(server)
    summaries = _manager(services).list_jobs(family_id, user, status=status, limit=limit)
    return [_summary_response(summary) for summary in summaries]


@router.get(
    "/{family_id}/asset-jobs/{job_id}",
    response_model=AssetJobResponse,
    summary="Fetch one asset job with progress",
)
async def get_asset_job(
    family_id: str, job_id: str, server: Server, user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    return _summary_response(_manager(services).get_job(family_id, job_id, user))


@router.get(
    "/{family_id}/asset-jobs/{job_id}/items",
    response_model=list[AssetJobItemResponse],
    summary="List items of an asset job",
    description="Filter by ``status`` (``PENDING`` / ``SUCCEEDED`` / ``SKIPPED`` / ``FAILED``) to inspect failures.",
)
async def list_asset_job_items(
    family_id: str,
    job_id: str,
    server: Server,
    user: CurrentUser,
    status: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[AssetJobItemResponse]:
    services = _services(server)
    rows = _manager(services).list_items(
        family_id, job_id, user, status=status, limit=limit,
    )
    return [_item_response(row) for row in rows]


@router.post(
    "/{family_id}/asset-jobs/{job_id}/pause",
    response_model=AssetJobResponse,
    summary="Pause a job so it stops claiming new items",
)
async def pause_asset_job(
    family_id: str, job_id: str, server: Server, user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    return _summary_response(_manager(services).pause_job(family_id, job_id, user))


@router.post(
    "/{family_id}/asset-jobs/{job_id}/resume",
    response_model=AssetJobResponse,
    summary="Resume a paused job from its cursor",
)
async def resume_asset_job(
    family_id: str, job_id: str, server: Server, user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    return _summary_response(_manager(services).resume_job(family_id, job_id, user))


@router.post(
    "/{family_id}/asset-jobs/{job_id}/cancel",
    response_model=AssetJobResponse,
    summary="Cancel an unfinished asset job",
)
async def cancel_asset_job(
    family_id: str, job_id: str, server: Server, user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    return _summary_response(_manager(services).cancel_job(family_id, job_id, user))


@router.post(
    "/{family_id}/asset-jobs/{job_id}/retry",
    response_model=AssetJobResponse,
    summary="Retry only the failed items of a job",
    description=(
        "Succeeded and skipped items keep their terminal state, so a "
        "retry after one bad photo does not redo the whole scan."
    ),
)
async def retry_asset_job(
    family_id: str, job_id: str, server: Server, user: CurrentUser,
) -> AssetJobResponse:
    services = _services(server)
    return _summary_response(_manager(services).retry_job(family_id, job_id, user))


@router.get(
    "/{family_id}/assets/{asset_id}/thumbnail",
    response_class=FileResponse,
    responses={
        200: {"content": {"image/webp": {}, "image/jpeg": {}}},
        404: {"description": "Thumbnail unavailable; fall back to the original asset."},
    },
    summary="Serve a cached thumbnail for an asset",
    description=(
        "Resolves the asset by id — never by a caller-supplied path — and "
        "checks family permission before opening a file. ``width`` / "
        "``height`` are clamped to 2048 px. Returns 404 when the asset is "
        "gone or Pillow cannot decode it, so the dashboard can fall back "
        "to the original."
    ),
)
async def get_asset_thumbnail(
    family_id: str,
    asset_id: str,
    server: Server,
    user: CurrentUser,
    width: int | None = Query(default=None, ge=1, le=2048),
    height: int | None = Query(default=None, ge=1, le=2048),
    format: str | None = Query(default=None, description="webp | jpeg"),
) -> Response:
    services = _services(server)
    family = _family(services)
    # Cache lives under HomeMind's data directory, never beside the
    # original file (the source tree may be read-only or a NAS mount).
    service = ThumbnailService(
        family,
        server.paths.root / "homemind" / "thumbnails",
        asset_repo=services.family_asset_repo,
        permission_evaluator=FamilyPermissionEvaluator(services.family_repo),
    )
    result = service.get_or_build(
        family_id,
        asset_id,
        user,
        width=width,
        height=height,
        requested_format=format,
    )
    if result is None:
        return Response(status_code=404)
    media_type = (
        "image/webp" if result.format == "WEBP" else "image/jpeg"
    )
    return FileResponse(result.path, media_type=media_type)


__all__ = [
    "router",
    "AssetJobCreateBody",
    "AssetJobItemResponse",
    "AssetJobResponse",
]
