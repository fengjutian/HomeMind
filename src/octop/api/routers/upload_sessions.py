"""Resumable upload sessions — HTTP surface for chunked large-file upload.

Thin adapter: validate HTTP, resolve the target permission, call
:class:`~octop.infra.uploads.service.UploadSessionService`, map errors. The
legacy ``POST /api/agents/{agent_id}/upload`` multipart path is untouched, so
small files keep their existing behaviour.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from octop.api.common.attachments import dashboard_inbound_preview_url, save_attachment
from octop.api.common.workspace import require_running_workspace
from octop.api.deps import current_user, get_server
from octop.infra.db.repos.upload_sessions import UploadSessionRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.gateway.media.inbound_store import (
    INBOUND_EXTENSION_MEDIA_TYPES,
    sanitize_inbound_filename,
)
from octop.infra.uploads.blobstore import UploadBlobStore
from octop.infra.uploads.protocol import UploadPurpose
from octop.infra.uploads.service import LandedUpload, UploadSessionService

router = APIRouter()

_HEX = set("0123456789abcdefABCDEF")


class UploadSessionCreateBody(BaseModel):
    filename: str = Field(description="Display name only; never used as a target path.")
    total_bytes: int = Field(gt=0, description="Declared size of the whole file.")
    purpose: UploadPurpose = Field(description="Where the assembled file is delivered.")
    agent_id: str | None = Field(default=None, description="Required for workspace purposes.")
    relative_target: str | None = Field(
        default=None, description="Workspace-relative path for WORKSPACE_FILE uploads."
    )
    mime_type: str | None = None
    total_sha256: str | None = Field(default=None, description="Optional whole-file SHA-256.")
    chunk_size: int | None = Field(default=None, gt=0, description="Clamped to protocol limits.")


def _service(server: Any) -> UploadSessionService:
    assert server.services is not None
    return UploadSessionService(server.services.repos.upload_session_repo, server.services.paths)


def _resolve_media_type(filename: str, declared: str | None) -> str:
    raw = (declared or "").split(";", 1)[0].strip().lower()
    if raw and raw != "application/octet-stream":
        return raw
    ext = Path(filename or "").suffix.lower()
    if ext in INBOUND_EXTENSION_MEDIA_TYPES:
        return INBOUND_EXTENSION_MEDIA_TYPES[ext]
    guessed, _ = mimetypes.guess_type(filename or "")
    return guessed.lower() if guessed else "application/octet-stream"


def _require_hex_digest(raw: str, *, header: str) -> str:
    value = raw.strip()
    if len(value) != 64 or not set(value) <= _HEX:
        raise OctopError(
            ErrorCode.UPLOAD_CHECKSUM_MISMATCH,
            f"{header} must be a 64-character hex SHA-256",
            details={"header": header},
        )
    return value.lower()


async def _target_workspace(
    body_purpose: UploadPurpose, agent_id: str | None, *, user: Any, server: Any
) -> Any:
    """Resolve the destination workspace, or refuse the purpose.

    Only workspace-backed purposes are wired in phase 2. ``KNOWLEDGE_DOCUMENT``
    and ``FAMILY_ASSET`` need an id column the protocol does not have yet, so
    they are rejected explicitly rather than silently mis-landed.
    """
    if body_purpose not in (UploadPurpose.CHAT_ATTACHMENT, UploadPurpose.WORKSPACE_FILE):
        raise OctopError(
            ErrorCode.UPLOAD_PURPOSE_UNSUPPORTED,
            f"purpose {body_purpose} is not available for resumable upload yet",
            details={"purpose": body_purpose.value},
        )
    if not agent_id:
        raise OctopError(
            ErrorCode.UPLOAD_SESSION_NOT_OPEN,
            "agent_id is required for workspace uploads",
            details={"purpose": body_purpose.value},
        )
    return await require_running_workspace(
        agent_id, user=user, as_user=None, server=server, owner_only=True
    )


@router.post(
    "/uploads/sessions",
    summary="Open a resumable upload session",
    description=(
        "Returns the server-assigned chunk size and the empty missing-range list. "
        "The client then PUTs each part as a raw binary body."
    ),
)
async def create_upload_session(
    body: UploadSessionCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    await _target_workspace(body.purpose, body.agent_id, user=user, server=server)
    total_sha = (
        _require_hex_digest(body.total_sha256, header="total_sha256") if body.total_sha256 else None
    )
    view = _service(server).create(
        owner_user_id=user.id,
        purpose=body.purpose.value,
        filename=sanitize_inbound_filename(body.filename) or "upload.bin",
        total_bytes=body.total_bytes,
        mime_type=_resolve_media_type(body.filename, body.mime_type),
        agent_id=body.agent_id,
        relative_target=body.relative_target,
        expected_sha256=total_sha,
        chunk_size=body.chunk_size,
    )
    return view.as_payload()


@router.get(
    "/uploads/sessions/{upload_id}",
    summary="Inspect an upload session",
    description="Returns bounded missing_ranges so huge files never return an unbounded array.",
)
async def get_upload_session(
    upload_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    return _service(server).status(upload_id=upload_id, owner_user_id=user.id).as_payload()


@router.put(
    "/uploads/sessions/{upload_id}/parts/{part_number}",
    summary="Upload one part",
    description=(
        "Raw binary body (never multipart). Requires Content-Length matching the "
        "part geometry and X-Chunk-SHA256. Re-sending identical bytes is idempotent; "
        "different bytes for the same part number return 409."
    ),
)
async def put_upload_part(
    upload_id: str,
    part_number: int,
    request: Request,
    x_chunk_sha256: str = Header(..., description="SHA-256 of this part's bytes."),
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    raw_length = request.headers.get("content-length")
    declared_length = int(raw_length) if raw_length and raw_length.isdigit() else None
    view = await _service(server).write_part(
        upload_id=upload_id,
        owner_user_id=user.id,
        part_number=part_number,
        stream=request.stream(),
        declared_length=declared_length,
        declared_sha256=_require_hex_digest(x_chunk_sha256, header="X-Chunk-SHA256"),
    )
    return view.as_payload()


@router.post(
    "/uploads/sessions/{upload_id}/complete",
    summary="Finish an upload",
    description=(
        "Takes an exclusive claim, streams the parts together, verifies size and "
        "SHA-256, then hands the file to the destination service."
    ),
)
async def complete_upload_session(
    upload_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    view = _service(server).status(upload_id=upload_id, owner_user_id=user.id)
    workspace = await _target_workspace(
        UploadPurpose(view.purpose), view.agent_id, user=user, server=server
    )
    landed: list[LandedUpload] = []

    async def lander(row: UploadSessionRow, assembled: Path) -> LandedUpload:
        """Deliver the assembled file to whichever target can hold its size."""
        max_bytes = int(server.services.config.max_upload_bytes)
        # octop-harness writes workspace files from bytes, so anything larger
        # than the configured cap would have to be fully buffered in memory.
        # Those go to Octop's own blob store and stream back from a dedicated route.
        if row.total_bytes > max_bytes:
            blobs = UploadBlobStore(server.services.paths)
            blobs.promote(row.upload_id, assembled)
            result = LandedUpload(
                kind="blob",
                resource_id=row.upload_id,
                path=row.upload_id,
                media_type=row.mime_type or "application/octet-stream",
                filename=row.filename,
            )
            landed.append(result)
            return result

        stored = await save_attachment(
            workspace,
            owner_id=user.id,
            filename=row.filename,
            media_type=row.mime_type or _resolve_media_type(row.filename, None),
            data=assembled.read_bytes(),
            max_bytes=max_bytes,
        )
        result = LandedUpload(
            kind="workspace",
            resource_id=stored.data_path,
            path=stored.data_path,
            media_type=stored.media_type,
            filename=stored.filename,
        )
        landed.append(result)
        return result

    completed = await _service(server).complete(
        upload_id=upload_id, owner_user_id=user.id, lander=lander
    )
    delivery = landed[0]
    if delivery.kind == "blob":
        access_url = f"/api/uploads/blobs/{upload_id}"
    else:
        access_url = dashboard_inbound_preview_url(
            completed.agent_id or "",
            delivery.path,
            media_type=delivery.media_type,
        )
    # Payload stays a superset of the legacy multipart endpoint's fields.
    return {
        "upload_id": completed.upload_id,
        "status": completed.status,
        "filename": delivery.filename,
        "media_type": delivery.media_type,
        "storage": delivery.kind,
        "size": completed.total_bytes,
        "path": delivery.path,
        "workspace_path": delivery.path,
        "url": access_url,
        "access_url": access_url,
    }


@router.get(
    "/uploads/blobs/{upload_id}",
    summary="Stream a completed oversized upload",
    description=(
        "Serves uploads too large to land in an agent workspace. Streams from disk "
        "in bounded buffers and is scoped to the session owner."
    ),
    response_class=StreamingResponse,
)
async def download_upload_blob(
    upload_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> Any:
    row = _service(server).completed_row(upload_id=upload_id, owner_user_id=user.id)
    blobs = UploadBlobStore(server.services.paths)
    if row is None or not blobs.exists(upload_id):
        raise OctopError(ErrorCode.UPLOAD_SESSION_NOT_FOUND, "upload not found")

    return StreamingResponse(
        blobs.stream_chunks(upload_id),
        media_type=row.mime_type or "application/octet-stream",
        headers={
            "Content-Length": str(blobs.size(upload_id)),
            "Content-Disposition": f'inline; filename="{row.filename}"',
            "X-Octop-Upload-Storage": "blob",
        },
    )


@router.delete(
    "/uploads/sessions/{upload_id}",
    status_code=204,
    summary="Cancel an upload and reclaim its staging bytes",
)
async def cancel_upload_session(
    upload_id: str,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> None:
    _service(server).cancel(upload_id=upload_id, owner_user_id=user.id)
