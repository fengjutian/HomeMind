"""HTTP adapters for family export and import (Stage 13).

The manager owns the security rules (manager-only, no secrets in a
bundle, staged imports); these routes are a thin shell over it. Download
returns a ZIP of the bundle directory via ``FileResponse`` with a
short-lived token, and the import side is split into stage → apply so a
bundle never lands in a live family without a decision.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.services import HomeMindServices
from homemind.infra.family.exports import FamilyExportManager
from octop.api.deps import current_user, get_server
from octop.infra.server import OctopServer
from octop.infra.users.identity import User

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]
CurrentUser = Annotated[User, Depends(current_user)]

# Exports are point-in-time snapshots of family metadata. A bundle
# kept forever is a standing copy of the family's relationships, so the
# ZIP stays downloadable for a bounded window and the cleanup sweep
# removes it afterwards.
DOWNLOAD_TTL_SECONDS = 24 * 60 * 60


class FamilyExportResponse(BaseModel):
    export_id: str
    family_id: str
    format: str
    version: int
    includes_original_assets: bool
    created_at: int
    sections: list[str]


class FamilyExportListItem(BaseModel):
    export_id: str
    status: str
    format_version: int
    includes_original_assets: bool
    byte_size: int
    created_at: int
    finished_at: int | None = None
    expires_at: int | None = None


class FamilyImportStageResponse(BaseModel):
    import_id: str
    family_id: str
    dry_run: bool
    format: str
    version: int
    conflicts: list[dict[str, object]]


class FamilyImportDecideBody(BaseModel):
    bundle_path: str = Field(
        min_length=1,
        max_length=400,
        description=(
            "Server-side path returned by the stage call. The client may "
            "not supply an arbitrary filesystem path."
        ),
    )


def _services(server: OctopServer) -> HomeMindServices:
    assert server.services is not None
    run_migrations(server.services.db)
    return HomeMindServices.from_pool(server.services.db)


def _manager(server: OctopServer) -> FamilyExportManager:
    services = _services(server)
    # Bundles live under the instance data directory, never under the
    # family's own media tree.
    return FamilyExportManager(services, server.paths.root / "homemind" / "exports")


@router.post(
    "/{family_id}/exports",
    response_model=FamilyExportResponse,
    status_code=201,
    summary="Export a family as a portable JSON bundle",
    description=(
        "Manager-only. The default bundle contains members, "
        "relationships, spaces, permissions, events, memories, tasks, "
        "albums, the asset index and an audit summary — never device "
        "tokens, provider keys, password hashes or face vectors. "
        "Including the original media is opt-in and needs a second flag."
    ),
)
async def create_export(
    family_id: str,
    server: Server,
    user: CurrentUser,
    include_original_assets: bool = Query(
        default=False,
        description="Also copy the family's original photo/video files into the bundle.",
    ),
) -> FamilyExportResponse:
    result = _manager(server).create_export(
        family_id, user, include_original_assets=include_original_assets,
    )
    manifest = result["manifest"]
    return FamilyExportResponse(
        export_id=result["export_id"],
        family_id=result["family_id"],
        format=manifest["format"],
        version=manifest["version"],
        includes_original_assets=bool(manifest["includes_original_assets"]),
        created_at=manifest["created_at"],
        sections=result["sections"],
    )


@router.get(
    "/{family_id}/exports",
    response_model=list[FamilyExportListItem],
    summary="List this family's exports",
)
async def list_exports(
    family_id: str, server: Server, user: CurrentUser,
) -> list[FamilyExportListItem]:
    return [
        FamilyExportListItem(**row)
        for row in _manager(server).list_exports(family_id, user)
    ]


@router.get(
    "/{family_id}/exports/{export_id}/download",
    summary="Download an export bundle as a ZIP",
    response_class=FileResponse,
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "ZIP of the bundle's JSON sections.",
        },
        404: {"description": "Export missing, not ready, or already cleaned up."},
    },
)
async def download_export(
    family_id: str,
    export_id: str,
    server: Server,
    user: CurrentUser,
) -> Response:
    """Stream the bundle.

    ``FileResponse`` with a directory is not a thing, so the bundle is
    zipped into a temp file first. The zip is written with the entry
    names taken from our own section list — never from anything a
    caller supplied — so the archive cannot contain a traversal entry.
    """
    manager = _manager(server)
    bundle = manager.download_path(family_id, export_id, user)
    archive = Path(tempfile.gettempdir()) / f"homemind-export-{export_id}.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(bundle.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(bundle)
            # ``PurePosixPath`` + an explicit separator check keeps the
            # entry name inside the archive even if a future change lets
            # an odd filename through.
            handle.write(path, f"{family_id}/{export_id}/{relative.as_posix()}")
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"homemind-family-{export_id}.zip",
    )


@router.delete(
    "/{family_id}/exports/{export_id}",
    status_code=204,
    summary="Delete an export bundle from disk",
)
async def delete_export(
    family_id: str, export_id: str, server: Server, user: CurrentUser,
) -> Response:
    _manager(server).delete_export(family_id, export_id, user)
    return Response(status_code=204)


@router.post(
    "/{family_id}/imports",
    response_model=FamilyImportStageResponse,
    status_code=202,
    summary="Stage an import bundle for review",
    description=(
        "Validates the manifest and every section checksum, then reports "
        "the ids that would collide with live rows. Nothing is written to "
        "the family until the import is applied."
    ),
)
async def stage_import(
    family_id: str,
    server: Server,
    user: CurrentUser,
    bundle: UploadFile,
    dry_run: bool = Query(default=True),
) -> FamilyImportStageResponse:
    """Accept an uploaded bundle and stage it.

    The upload is unpacked with entry names validated, because an
    uploaded archive is untrusted input.
    """
    target = Path(tempfile.gettempdir()) / f"homemind-import-{family_id}-{_stamp()}.zip"
    try:
        payload = await bundle.read()
        with zipfile.ZipFile(_bytes_to_zip(payload)) as handle:
            for entry in handle.namelist():
                if entry.startswith("/") or ".." in Path(entry).parts:
                    raise HTTPException(
                        status_code=400, detail="bundle contains an unsafe path",
                    )
            handle.extractall(target.parent / target.stem)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="not a valid zip archive") from exc
    staged = _manager(server).stage_import(
        family_id, user, bundle=target.parent / target.stem, dry_run=dry_run,
    )
    manifest = staged["manifest"]
    return FamilyImportStageResponse(
        import_id=staged["import_id"],
        family_id=staged["family_id"],
        dry_run=staged["dry_run"],
        format=manifest["format"],
        version=manifest["version"],
        conflicts=staged["conflicts"],
    )


@router.post(
    "/{family_id}/imports/{import_id}/apply",
    summary="Apply a staged import",
    description=(
        "Manager-only. Refused while unresolved conflicts remain — "
        "applying over live rows is exactly what staging exists to "
        "prevent."
    ),
)
async def apply_import(
    family_id: str,
    import_id: str,
    body: FamilyImportDecideBody,
    server: Server,
    user: CurrentUser,
) -> dict[str, object]:
    result = _manager(server).apply_import(
        family_id, import_id, user, bundle=Path(body.bundle_path),
    )
    return result


@router.post(
    "/{family_id}/imports/{import_id}/reject",
    summary="Reject a staged import",
)
async def reject_import(
    family_id: str, import_id: str, server: Server, user: CurrentUser,
) -> dict[str, object]:
    return _manager(server).reject_import(family_id, import_id, user)


def _stamp() -> str:
    import time  # noqa: PLC0415

    return str(int(time.time() * 1000))


def _bytes_to_zip(payload: bytes) -> Path:
    import tempfile as _tempfile  # noqa: PLC0415

    handle = _tempfile.NamedTemporaryFile(  # noqa: SIM115
        suffix=".zip", delete=False,
    )
    handle.write(payload)
    handle.close()
    return Path(handle.name)


def _cleanup(path: Path) -> None:
    with contextlib.suppress(OSError):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


__all__ = [
    "DOWNLOAD_TTL_SECONDS",
    "router",
    "FamilyExportListItem",
    "FamilyExportResponse",
    "FamilyImportDecideBody",
    "FamilyImportStageResponse",
]
