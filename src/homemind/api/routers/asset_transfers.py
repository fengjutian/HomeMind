"""Device-facing HTTP surface for asynchronous asset transfers.

Two planes share this router but never share a credential.

**Control plane** (``Authorization: Bearer <device-token>``) creates a
transfer, hands back a manifest, refreshes the data-plane credential, and
accepts progress / completion / failure reports. It moves no bytes.

**Data plane** (``Authorization: Transfer <short-lived-token>``) serves
``HEAD`` and single-range ``GET`` over the bytes themselves.

Routers stay thin here: parse headers, delegate to
:class:`~homemind.infra.family.asset_transfers.AssetTransferManager`, map
domain errors onto HTTP. Every authorization rule lives in the manager so
the same rule cannot be implemented twice with two different outcomes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from homemind.api.headers import bearer_token, transfer_token
from homemind.infra.db.migrate import run_migrations
from homemind.infra.db.repos.asset_transfers import AssetTransferRow
from homemind.infra.db.services import HomeMindServices
from homemind.infra.errors import HomeMindError, HomeMindErrorCode
from homemind.infra.family.asset_transfers import (
    AssetTransferManager,
    RangeNotSatisfiable,
    ResolvedContent,
    TransferHandle,
)
from homemind.infra.family.device_runtime import DeviceRuntimeManager
from homemind.infra.family.events import (
    EVENT_TRANSFER_COMPLETED,
    EVENT_TRANSFER_CREATED,
    EVENT_TRANSFER_FAILED,
    emit_family_event,
)
from homemind.infra.family.manager import FamilyManager
from octop.api.deps import get_server
from octop.infra.server import OctopServer

router = APIRouter()
Server = Annotated[OctopServer, Depends(get_server)]


# ------------------------------------------------------------------ models


class TransferAssetInfo(BaseModel):
    asset_id: str
    name: str
    mime_type: str
    size_bytes: int = Field(description="Total size of the file in bytes.")
    sha256: str = Field(description="SHA-256 of the whole file; the final integrity check.")
    etag: str = Field(description="Opaque quoted resource version; send it back as If-Match.")


class TransferDownloadInfo(BaseModel):
    url: str = Field(
        description="Data-plane path; requires 'Authorization: Transfer <token>'.",
    )
    token: str = Field(
        description=(
            "Short-lived download credential, shown exactly once. "
            "Refresh the transfer to mint another."
        ),
    )
    expires_at: int = Field(description="UTC epoch seconds after which the token stops working.")
    chunk_size: int = Field(description="Suggested chunk size for resumable ranges.")
    max_concurrency: int = Field(description="Suggested parallel range requests.")
    accept_ranges: bool


class TransferStatusResponse(BaseModel):
    """Control-plane view. Never carries a usable download credential
    unless it was just minted by create or refresh."""

    transfer_id: str
    status: str
    asset: TransferAssetInfo
    bytes_downloaded: int
    size_bytes: int
    chunk_size: int
    expires_at: int
    last_progress_at: int | None = None
    completed_at: int | None = None
    failure_code: str | None = None
    created_at: int
    updated_at: int
    download: TransferDownloadInfo | None = None


class CreateTransferBody(BaseModel):
    asset_id: str = Field(min_length=1, max_length=64)
    request_key: str = Field(
        min_length=1,
        max_length=128,
        description=(
            "Device-generated idempotency key. Retrying with the same key returns "
            "the same transfer instead of starting a second download."
        ),
    )


class TransferProgressBody(BaseModel):
    bytes_downloaded: int = Field(ge=0, description="Aggregate bytes assembled so far.")


class TransferCompleteBody(BaseModel):
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64, description="SHA-256 of the assembled file.")


class TransferFailBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(
        default=None,
        max_length=1000,
        description="Diagnostic text; stripped of URL queries, paths and secrets before storage.",
    )


# ------------------------------------------------------------------ wiring


def _manager(server: OctopServer) -> AssetTransferManager:
    assert server.services is not None
    run_migrations(server.services.db)
    services = HomeMindServices.from_pool(server.services.db)
    return AssetTransferManager(
        device_repo=services.family_device_repo,
        asset_repo=services.family_asset_repo,
        transfer_repo=services.asset_transfer_repo,
        device_runtime=DeviceRuntimeManager(
            FamilyManager(services.family_repo),
            services.family_device_repo,
        ),
    )


def _status_response(
    transfer: AssetTransferRow,
    *,
    name: str,
    mime_type: str,
    handle: TransferHandle | None = None,
) -> TransferStatusResponse:
    download = None
    if handle is not None:
        download = TransferDownloadInfo(
            url=f"/api/homemind/runtime/transfers/{transfer.id}/content",
            token=handle.token,
            expires_at=handle.token_expires_at,
            chunk_size=handle.chunk_size,
            max_concurrency=handle.max_concurrency,
            accept_ranges=True,
        )
    return TransferStatusResponse(
        transfer_id=transfer.id,
        status=transfer.status,
        asset=TransferAssetInfo(
            asset_id=transfer.asset_id,
            name=name,
            mime_type=mime_type or "application/octet-stream",
            size_bytes=transfer.size_bytes,
            sha256=transfer.sha256,
            etag=transfer.etag,
        ),
        bytes_downloaded=transfer.bytes_reported,
        size_bytes=transfer.size_bytes,
        chunk_size=transfer.chunk_size,
        expires_at=transfer.expires_at,
        last_progress_at=transfer.last_progress_at,
        completed_at=transfer.completed_at,
        failure_code=transfer.failure_code,
        created_at=transfer.created_at,
        updated_at=transfer.updated_at,
        download=download,
    )


def _response_for(
    manager: AssetTransferManager,
    transfer: AssetTransferRow,
) -> TransferStatusResponse:
    """Build the status body from the transfer row's own snapshot.

    The asset row may already be gone -- deleting an index entry cancels
    its transfers but keeps the audit row, and a device asking "what
    happened to my download?" must get ``CANCELLED``, not a 503. The
    version fields come from the transfer regardless, because those are
    what the device would resume against.
    """
    asset = manager.asset_repo.get(transfer.asset_id)
    return _status_response(
        transfer,
        name=asset.name if asset is not None else transfer.asset_id,
        mime_type=asset.mime_type if asset is not None else "",
    )


def _content_disposition(filename: str, disposition: str = "attachment") -> str:
    """RFC 6266 header value built from parts, never string-concatenated.

    The asset name comes off the filesystem, so characters that would split
    the header are stripped and non-ASCII rides in ``filename*`` alongside
    an ASCII fallback.
    """
    cleaned = (
        "".join(char for char in filename if char.isprintable() and char not in '"\\') or "download"
    )
    fallback = cleaned.encode("ascii", "replace").decode("ascii").replace(";", "_")
    return f"{disposition}; filename=\"{fallback}\"; filename*=UTF-8''{quote(cleaned, safe='')}"


def _data_headers(resolved: ResolvedContent) -> dict[str, str]:
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": resolved.etag,
        "Content-Type": resolved.mime_type,
        "Content-Length": str(resolved.response_length),
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _content_disposition(resolved.filename),
    }
    if resolved.range is not None:
        headers["Content-Range"] = (
            f"bytes {resolved.range.start}-{resolved.range.end}/{resolved.size_bytes}"
        )
    return headers


# ------------------------------------------------------------- control plane


async def _audit(
    server: OctopServer,
    family_id: str,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    """Announce a task transition to the family's managers.

    One event per transition, never one per Range request. The bus lives
    on the server and is absent in a plain Octop deployment or a test
    rig, so a download must not depend on anyone watching -- the transfer
    row is the durable record; this is the live view of it.
    """
    bus = getattr(server, "family_event_bus", None)
    await emit_family_event(bus, event_type, family_id, payload)


@router.post(
    "/runtime/transfers",
    response_model=TransferStatusResponse,
    status_code=201,
    summary="Create or resume a device asset download",
    description=(
        "Authorizes one download of a family asset to the authenticated device "
        "and returns the manifest plus a short-lived data-plane credential. "
        "Idempotent on `request_key`: a retry returns the same transfer with "
        "HTTP 200 instead of 201, so a lost response never starts a second "
        "multi-gigabyte job. The credential is shown exactly once."
    ),
)
async def create_transfer(
    body: CreateTransferBody,
    response: Response,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    handle = await _manager(server).create_transfer(
        bearer_token(authorization),
        asset_id=body.asset_id,
        request_key=body.request_key,
    )
    if not handle.created:
        response.status_code = 200
    else:
        await _audit(
            server,
            handle.transfer.family_id,
            EVENT_TRANSFER_CREATED,
            {
                "transfer_id": handle.transfer.id,
                "device_id": handle.transfer.device_id,
                "asset_id": handle.transfer.asset_id,
                "size_bytes": handle.transfer.size_bytes,
            },
        )
    return _status_response(
        handle.transfer,
        name=handle.asset.name,
        mime_type=handle.asset.mime_type,
        handle=handle,
    )


@router.get(
    "/runtime/transfers/{transfer_id}",
    response_model=TransferStatusResponse,
    summary="Read one transfer owned by this device",
    description=(
        "Ownership is re-derived from the bearer token, so one device cannot "
        "read another device's transfer. The response omits any plaintext "
        "download credential even while the task is active."
    ),
)
async def get_transfer(
    transfer_id: str,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    manager = _manager(server)
    transfer = await manager.get_transfer(bearer_token(authorization), transfer_id)
    return _response_for(manager, transfer)


@router.post(
    "/runtime/transfers/{transfer_id}/refresh",
    response_model=TransferStatusResponse,
    summary="Mint a fresh short-lived download credential",
    description=(
        "Allowed only while the transfer is PENDING or ACTIVE; the previous "
        "credential is revoked before the new one is issued. Returns 412 "
        "SOURCE_CHANGED when the file changed after the task was created -- "
        "resuming onto different bytes would corrupt the result in a way the "
        "device cannot detect."
    ),
)
async def refresh_transfer(
    transfer_id: str,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    handle = await _manager(server).refresh_token(bearer_token(authorization), transfer_id)
    return _status_response(
        handle.transfer,
        name=handle.asset.name,
        mime_type=handle.asset.mime_type,
        handle=handle,
    )


@router.post(
    "/runtime/transfers/{transfer_id}/progress",
    response_model=TransferStatusResponse,
    summary="Report aggregate download progress",
    description=(
        "Progress only moves forward; a smaller or repeated value returns the "
        "stored maximum instead of an error. No per-chunk bitmap is stored -- "
        "that state belongs to the device."
    ),
)
async def report_progress(
    transfer_id: str,
    body: TransferProgressBody,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    manager = _manager(server)
    transfer = await manager.report_progress(
        bearer_token(authorization),
        transfer_id,
        bytes_downloaded=body.bytes_downloaded,
    )
    return _response_for(manager, transfer)


@router.post(
    "/runtime/transfers/{transfer_id}/complete",
    response_model=TransferStatusResponse,
    summary="Report a verified, fully assembled download",
    description=(
        "The reported size and SHA-256 are checked against the manifest before "
        "the task becomes COMPLETED and every download credential is revoked. "
        "A mismatch fails the task rather than recording a success the bytes do "
        "not support. Repeating the call is idempotent."
    ),
)
async def complete_transfer(
    transfer_id: str,
    body: TransferCompleteBody,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    manager = _manager(server)
    # Read the row first so a replayed completion does not emit a second
    # audit event for a transition that already happened.
    before = manager.transfer_repo.get(transfer_id)
    transfer = await manager.complete(
        bearer_token(authorization),
        transfer_id,
        size_bytes=body.size_bytes,
        sha256=body.sha256,
    )
    if before is not None and not before.is_terminal:
        await _audit(
            server,
            transfer.family_id,
            EVENT_TRANSFER_COMPLETED,
            {
                "transfer_id": transfer.id,
                "device_id": transfer.device_id,
                "asset_id": transfer.asset_id,
                "size_bytes": transfer.size_bytes,
            },
        )
    return _response_for(manager, transfer)


@router.post(
    "/runtime/transfers/{transfer_id}/fail",
    response_model=TransferStatusResponse,
    summary="Report a download the device could not finish",
    description=(
        "Diagnostic text is stripped of URL query strings, absolute paths and "
        "opaque secrets before storage. Terminal states are final, so a late "
        "failure report cannot overwrite a completed transfer."
    ),
)
async def fail_transfer(
    transfer_id: str,
    body: TransferFailBody,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
) -> TransferStatusResponse:
    manager = _manager(server)
    before = manager.transfer_repo.get(transfer_id)
    transfer = await manager.fail(
        bearer_token(authorization),
        transfer_id,
        code=body.code,
        detail=body.detail,
    )
    if before is not None and not before.is_terminal:
        await _audit(
            server,
            transfer.family_id,
            EVENT_TRANSFER_FAILED,
            {
                "transfer_id": transfer.id,
                "device_id": transfer.device_id,
                "asset_id": transfer.asset_id,
                "code": transfer.failure_code,
            },
        )
    return _response_for(manager, transfer)


# ---------------------------------------------------------------- data plane


def _authorize_or_416(
    manager: AssetTransferManager,
    transfer_id: str,
    authorization: str | None,
    *,
    if_match: str | None,
    range_header: str | None,
) -> ResolvedContent | Response:
    """Authorize one byte read, answering ``416`` for a bad range.

    A rejected ``Range`` still needs the total length in
    ``Content-Range: bytes * /<size>`` so the client can recompose, which
    means one extra cheap authorization with no range header. That second
    pass also confirms the credential is still good, so a 416 never
    reveals anything to a caller who cannot otherwise read the file.
    """
    token = transfer_token(authorization)
    try:
        resolved = manager.authorize_content(
            token,
            if_match=if_match,
            range_header=range_header,
        )
    except RangeNotSatisfiable:
        whole = manager.authorize_content(token, if_match=if_match)
        manager.release_content(whole)
        return Response(
            status_code=416,
            headers={
                "Content-Range": f"bytes */{whole.size_bytes}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    if resolved.transfer.id != transfer_id:
        # The credential is bound to one transfer; a URL naming another is
        # not this credential's to answer.
        manager.release_content(resolved)
        raise HomeMindError(
            HomeMindErrorCode.ASSET_TRANSFER_NOT_FOUND,
            "asset transfer not found",
        )
    return resolved


@router.head(
    "/runtime/transfers/{transfer_id}/content",
    response_class=Response,
    summary="Probe transfer metadata before downloading",
    description=(
        "Data plane; requires 'Authorization: Transfer <short-lived-token>'. "
        "Returns Content-Length, ETag and Accept-Ranges so the device can "
        "confirm the resource version and plan its chunking. Authorization is "
        "re-checked here, so a revoked device or a deleted asset stops an "
        "in-flight download immediately."
    ),
)
async def probe_transfer_content(
    transfer_id: str,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    manager = _manager(server)
    resolved = _authorize_or_416(
        manager,
        transfer_id,
        authorization,
        if_match=if_match,
        range_header=None,
    )
    if isinstance(resolved, Response):
        return resolved
    headers = _data_headers(resolved)
    manager.release_content(resolved)
    return Response(status_code=200, headers=headers)


@router.get(
    "/runtime/transfers/{transfer_id}/content",
    response_class=Response,
    summary="Stream transfer bytes, whole or one range at a time",
    description=(
        "Data plane; requires 'Authorization: Transfer <short-lived-token>'. A "
        "device credential or dashboard JWT is rejected -- a long-lived token "
        "in a download URL would outlive the job. Serves the whole file, or a "
        "single range when `Range` is present. Multi-range requests are refused "
        "with 416; download one range per request. Send `If-Match` with the "
        "manifest ETag to have a changed resource rejected with 412 instead of "
        "a range computed against stale geometry."
    ),
)
async def stream_transfer_content(
    transfer_id: str,
    server: Server,
    authorization: Annotated[str | None, Header()] = None,
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    manager = _manager(server)
    resolved = _authorize_or_416(
        manager,
        transfer_id,
        authorization,
        if_match=if_match,
        range_header=range_header,
    )
    if isinstance(resolved, Response):
        return resolved
    headers = _data_headers(resolved)
    status_code = 206 if resolved.range is not None else 200

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in manager.stream_bytes(resolved):
                yield chunk
        finally:
            # Runs on client disconnect too, so an aborted multi-gigabyte
            # transfer strands neither a file descriptor nor a rate slot.
            manager.release_content(resolved)

    return StreamingResponse(body(), status_code=status_code, headers=headers)


__all__ = ["router"]
