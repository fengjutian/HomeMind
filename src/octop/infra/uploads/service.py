"""Upload session domain service — resumable upload orchestration.

Phase 2 of the resumable-upload plan. Owns the state machine, quota checks,
streaming part writes and the streaming merge. HTTP concerns stay in
``api/routers/upload_sessions.py``.

Two invariants this module exists to guarantee:

* **Bounded memory.** Parts are streamed from the request to disk in fixed
  buffers and merged with a streaming SHA-256; nothing scales with file size.
* **No partial state.** A part is registered in the DB only after its bytes are
  fsynced to disk under their final name, so a crash can never leave the DB
  claiming bytes that do not exist.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.upload_sessions import (
    PartWriteOutcome,
    UploadPartConflict,
    UploadPartRow,
    UploadSessionRepo,
    UploadSessionRow,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.uploads.protocol import (
    DEFAULT_UPLOAD_LIMITS,
    MissingRanges,
    UploadLimits,
    UploadStatus,
    expected_part_count,
    missing_ranges,
    part_bounds,
)
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

#: Copy buffer for merge/verify. Fixed size, so peak memory is this constant
#: regardless of how large the uploaded file is.
_STREAM_BUFFER = 1024 * 1024

#: Staging statuses whose bytes still occupy the staging quota.
_QUOTA_STATUSES = (
    UploadStatus.OPEN.value,
    UploadStatus.ASSEMBLING.value,
    UploadStatus.FAILED.value,
)


@dataclass(frozen=True)
class LandedUpload:
    """Where an assembled file ended up.

    ``kind`` distinguishes the two delivery strategies:

    * ``workspace`` — landed through ``write_inbound``; the bytes API requires
      the whole file in memory, so this is only used below the configured cap.
    * ``blob`` — moved (never copied) into Octop's own blob store and served by
      a streaming route. This is what makes multi-GB uploads possible without
      buffering the file, because ``octop-harness`` exposes no streaming
      workspace write.
    """

    kind: str
    resource_id: str
    path: str
    media_type: str
    filename: str


#: Hands the assembled file to the real destination and describes where it landed.
UploadLander = Callable[[UploadSessionRow, Path], Awaitable[LandedUpload]]


@dataclass(frozen=True)
class UploadSessionView:
    """API-facing projection of a session. Never carries local paths."""

    upload_id: str
    status: str
    purpose: str
    filename: str
    mime_type: str | None
    total_bytes: int
    chunk_size: int
    received_bytes: int
    expires_at: int
    expected_sha256: str | None
    agent_id: str | None
    family_id: str | None
    final_resource_id: str | None
    part_count: int
    missing: MissingRanges

    def as_payload(self) -> dict[str, object]:
        return {
            "upload_id": self.upload_id,
            "status": self.status,
            "purpose": self.purpose,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "total_bytes": self.total_bytes,
            "chunk_size": self.chunk_size,
            "received_bytes": self.received_bytes,
            "expires_at": self.expires_at,
            "expected_sha256": self.expected_sha256,
            "agent_id": self.agent_id,
            "family_id": self.family_id,
            "final_resource_id": self.final_resource_id,
            "part_count": self.part_count,
            "missing_ranges": [
                {"offset": offset, "size": size} for offset, size in self.missing.ranges
            ],
            "missing_truncated": self.missing.truncated,
        }


class UploadSessionService:
    def __init__(
        self,
        repo: UploadSessionRepo,
        paths: PathLayout,
        *,
        limits: UploadLimits = DEFAULT_UPLOAD_LIMITS,
    ) -> None:
        self._repo = repo
        self._paths = paths
        self._limits = limits

    # ------------------------------------------------------------------ helpers

    def _part_file(self, upload_id: str, part_number: int, *, suffix: str = ".part") -> Path:
        return self._paths.upload_staging_dir(upload_id) / f"{part_number:06d}{suffix}"

    def _require_owned(self, upload_id: str, owner_user_id: int) -> UploadSessionRow:
        """Fetch a session, scoped to its owner.

        A session owned by somebody else is reported as *not found* so this API
        cannot be used to probe for other users' upload ids.
        """
        row = self._repo.get_for_owner(upload_id, owner_user_id)
        if row is None:
            raise OctopError(ErrorCode.UPLOAD_SESSION_NOT_FOUND, "upload session not found")
        return row

    def _require_open(self, row: UploadSessionRow, *, now: int) -> None:
        if row.expires_at <= now and row.status != UploadStatus.COMPLETED.value:
            self._repo.set_status(row.upload_id, status=UploadStatus.EXPIRED.value)
            raise OctopError(ErrorCode.UPLOAD_SESSION_EXPIRED, "upload session expired")
        if row.status != UploadStatus.OPEN.value:
            raise OctopError(
                ErrorCode.UPLOAD_SESSION_NOT_OPEN,
                f"upload session is {row.status}",
                details={"status": row.status},
            )

    def _view(self, row: UploadSessionRow, ranges: list[tuple[int, int]]) -> UploadSessionView:
        return UploadSessionView(
            upload_id=row.upload_id,
            status=row.status,
            purpose=row.purpose,
            filename=row.filename,
            mime_type=row.mime_type,
            total_bytes=row.total_bytes,
            chunk_size=row.chunk_size,
            received_bytes=row.received_bytes,
            expires_at=row.expires_at,
            expected_sha256=row.expected_sha256,
            agent_id=row.agent_id,
            family_id=row.family_id,
            final_resource_id=row.final_resource_id,
            part_count=len(ranges),
            missing=missing_ranges(
                ranges,
                total_bytes=row.total_bytes,
                max_ranges=self._limits.max_missing_ranges,
            ),
        )

    def _staging_cleanup(self, upload_id: str) -> None:
        shutil.rmtree(self._paths.upload_staging_dir(upload_id), ignore_errors=True)

    # ------------------------------------------------------------------ commands

    def create(
        self,
        *,
        owner_user_id: int,
        purpose: str,
        filename: str,
        total_bytes: int,
        mime_type: str | None = None,
        agent_id: str | None = None,
        family_id: str | None = None,
        relative_target: str | None = None,
        expected_sha256: str | None = None,
        chunk_size: int | None = None,
        upload_id: str | None = None,
        expires_at: int | None = None,
        now: int | None = None,
    ) -> UploadSessionView:
        """Open a session after checking size, per-user concurrency and quota."""
        ts = now if now is not None else now_ts()
        if total_bytes <= 0:
            raise OctopError(ErrorCode.UPLOAD_INCOMPLETE, "total_bytes must be > 0")
        if total_bytes > self._limits.max_file_bytes:
            max_mb = max(1, self._limits.max_file_bytes // (1024 * 1024))
            raise OctopError(
                ErrorCode.ATTACHMENT_TOO_LARGE,
                f"file too large (max {max_mb}MB)",
                details={"max_mb": max_mb},
            )

        if self._repo.count_active_for_owner(owner_user_id, now=ts) >= (
            self._limits.max_open_sessions_per_user
        ):
            raise OctopError(
                ErrorCode.UPLOAD_TOO_MANY_SESSIONS,
                "too many upload sessions in progress",
                details={"max_sessions": self._limits.max_open_sessions_per_user},
            )

        reserved = self._repo.sum_reserved_bytes(statuses=list(_QUOTA_STATUSES))
        if reserved + total_bytes > self._limits.global_staging_quota_bytes:
            raise OctopError(
                ErrorCode.UPLOAD_QUOTA_EXCEEDED,
                "upload staging quota exceeded",
                details={
                    "quota_bytes": self._limits.global_staging_quota_bytes,
                    "reserved_bytes": reserved,
                },
            )

        row = self._repo.create(
            upload_id=upload_id or new_ulid(),
            owner_user_id=owner_user_id,
            purpose=purpose,
            filename=filename,
            mime_type=mime_type,
            agent_id=agent_id,
            family_id=family_id,
            relative_target=relative_target,
            total_bytes=total_bytes,
            chunk_size=self._limits.clamp_chunk_bytes(chunk_size),
            expected_sha256=expected_sha256,
            expires_at=expires_at if expires_at is not None else self._limits.expires_at(now=ts),
        )
        self._paths.ensure_upload_staging_dir(row.upload_id)
        return self._view(row, [])

    def status(
        self,
        *,
        upload_id: str,
        owner_user_id: int,
        now: int | None = None,
    ) -> UploadSessionView:
        return self._view(
            self._require_owned(upload_id, owner_user_id),
            self._repo.received_ranges(upload_id),
        )

    async def write_part(
        self,
        *,
        upload_id: str,
        owner_user_id: int,
        part_number: int,
        stream: AsyncIterable[bytes],
        declared_length: int | None,
        declared_sha256: str,
        now: int | None = None,
    ) -> UploadSessionView:
        """Stream one part to disk, verify it, then register it atomically."""
        ts = now if now is not None else now_ts()
        row = self._require_owned(upload_id, owner_user_id)
        self._require_open(row, now=ts)

        offset, expected_size = part_bounds(
            part_number, total_bytes=row.total_bytes, chunk_size=row.chunk_size
        )
        if declared_length is not None and declared_length != expected_size:
            raise OctopError(
                ErrorCode.UPLOAD_PART_CONFLICT,
                "Content-Length does not match the part geometry",
                details={
                    "part_number": part_number,
                    "expected_size": expected_size,
                    "declared_length": declared_length,
                },
            )

        digest, written = await self._spool_part(row.upload_id, part_number, stream, expected_size)
        if digest.lower() != declared_sha256.lower():
            self._discard_part(row.upload_id, part_number)
            raise OctopError(
                ErrorCode.UPLOAD_CHECKSUM_MISMATCH,
                "X-Chunk-SHA256 does not match the received bytes",
                details={"part_number": part_number},
            )

        try:
            outcome = self._repo.add_part(
                upload_id=row.upload_id,
                part_number=part_number,
                offset=offset,
                size=written,
                sha256=digest,
            )
        except UploadPartConflict as exc:
            self._discard_part(row.upload_id, part_number)
            raise OctopError(
                ErrorCode.UPLOAD_PART_CONFLICT, str(exc), details={"part_number": part_number}
            ) from exc

        if outcome is PartWriteOutcome.IDEMPOTENT:
            # The spooled file replaced an existing part with byte-identical
            # content (same digest ⇒ same bytes), so there is nothing to delete.
            # Removing it here would destroy the durable copy.
            logger.debug("upload part already registered", extra={"upload_id": upload_id})

        return self.status(upload_id=row.upload_id, owner_user_id=owner_user_id, now=ts)

    async def complete(
        self,
        *,
        upload_id: str,
        owner_user_id: int,
        lander: UploadLander,
        now: int | None = None,
    ) -> UploadSessionView:
        """Claim the session, stream-merge, verify, then hand to the target."""
        ts = now if now is not None else now_ts()
        row = self._require_owned(upload_id, owner_user_id)
        self._require_open(row, now=ts)
        if not self._repo.claim_for_assembly(upload_id):
            current = self._repo.get(upload_id)
            raise OctopError(
                ErrorCode.UPLOAD_SESSION_NOT_OPEN,
                "upload session is already being completed",
                details={"status": current.status if current else "unknown"},
            )

        try:
            parts = self._repo.list_parts(upload_id)
            self._assert_contiguous(row, parts)
            merged = self._paths.upload_staging_dir(upload_id) / "assembled.bin"
            size, digest = self._merge(row, parts, merged)
            if size != row.total_bytes:
                raise OctopError(
                    ErrorCode.UPLOAD_CHECKSUM_MISMATCH,
                    "assembled size does not match the declared total",
                    details={"expected": row.total_bytes, "actual": size},
                )
            if row.expected_sha256 and digest.lower() != row.expected_sha256.lower():
                raise OctopError(
                    ErrorCode.UPLOAD_CHECKSUM_MISMATCH,
                    "assembled content failed SHA-256 verification",
                )
            landed = await lander(row, merged)
        except OctopError as exc:
            # Keep every part so the client can resume or retry.
            self._repo.release_to_open(row.upload_id, last_error=str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - surface as a retryable failure
            logger.exception("upload completion failed", extra={"upload_id": upload_id})
            self._repo.release_to_open(row.upload_id, last_error=type(exc).__name__)
            raise OctopError(ErrorCode.INTERNAL_ERROR, "upload completion failed") from exc

        self._repo.mark_completed(upload_id, final_resource_id=landed.resource_id)
        self._staging_cleanup(upload_id)
        completed = self._repo.get(upload_id)
        assert completed is not None  # noqa: S101
        return self._view(completed, [])

    def completed_row(self, *, upload_id: str, owner_user_id: int) -> UploadSessionRow | None:
        """Owner-scoped row lookup for a finished upload, or ``None``.

        Used by the blob download route, which must not hand out a file for a
        session that is still open, expired, or owned by somebody else.
        """
        row = self._repo.get_for_owner(upload_id, owner_user_id)
        if row is None or row.status != UploadStatus.COMPLETED.value:
            return None
        return row

    def cancel(self, *, upload_id: str, owner_user_id: int) -> None:
        row = self._require_owned(upload_id, owner_user_id)
        if row.status == UploadStatus.COMPLETED.value:
            raise OctopError(
                ErrorCode.UPLOAD_SESSION_NOT_OPEN,
                "cannot cancel a completed upload",
                details={"status": row.status},
            )
        self._repo.set_status(upload_id, status=UploadStatus.ABORTED.value)
        self._staging_cleanup(upload_id)

    async def cleanup_expired(self, *, now: int | None = None, limit: int = 100) -> int:
        """Expire sessions past their TTL and reclaim their staging bytes."""
        ts = now if now is not None else now_ts()
        rows = self._repo.list_expired(now=ts, limit=limit)
        for row in rows:
            self._repo.set_status(row.upload_id, status=UploadStatus.EXPIRED.value)
            self._staging_cleanup(row.upload_id)
        return len(rows)

    # ------------------------------------------------------------------ internals

    def _assert_contiguous(self, row: UploadSessionRow, parts: list[UploadPartRow]) -> None:
        """Parts must tile the file exactly: no gap, no overlap, no short middle."""
        cursor = 0
        for part in parts:
            exp_offset, exp_size = part_bounds(
                part.part_number, total_bytes=row.total_bytes, chunk_size=row.chunk_size
            )
            if part.offset != cursor or part.offset != exp_offset or part.size != exp_size:
                raise OctopError(
                    ErrorCode.UPLOAD_INCOMPLETE,
                    "upload is not a contiguous tiling of the file",
                    details={"missing_count": self._missing_count(row, parts)},
                )
            cursor += part.size
        if cursor != row.total_bytes:
            raise OctopError(
                ErrorCode.UPLOAD_INCOMPLETE,
                "upload is missing parts",
                details={"missing_count": self._missing_count(row, parts)},
            )

    def _missing_count(self, row: UploadSessionRow, parts: list[UploadPartRow]) -> int:
        return max(0, expected_part_count(row.total_bytes, row.chunk_size) - len(parts))

    async def _spool_part(
        self,
        upload_id: str,
        part_number: int,
        stream: AsyncIterable[bytes],
        expected_size: int,
    ) -> tuple[str, int]:
        """Write the part to a temp file, fsync, then rename into place."""
        self._paths.ensure_upload_staging_dir(upload_id)
        temp = self._part_file(upload_id, part_number, suffix=".part.tmp")
        final = self._part_file(upload_id, part_number)
        digest = hashlib.sha256()
        written = 0
        try:
            with temp.open("wb") as handle:
                async for chunk in stream:
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > expected_size:
                        raise OctopError(
                            ErrorCode.UPLOAD_PART_CONFLICT,
                            "part body longer than the declared geometry",
                            details={
                                "part_number": part_number,
                                "expected_size": expected_size,
                            },
                        )
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            temp.replace(final)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
        return digest.hexdigest(), written

    def _discard_part(self, upload_id: str, part_number: int) -> None:
        for suffix in (".part", ".part.tmp"):
            self._part_file(upload_id, part_number, suffix=suffix).unlink(missing_ok=True)

    def _merge(
        self,
        row: UploadSessionRow,
        parts: list[UploadPartRow],
        target: Path,
    ) -> tuple[int, str]:
        """Concatenate parts in order with a streaming hash. Never buffers the file."""
        digest = hashlib.sha256()
        total = 0
        with target.open("wb") as out:
            for part in parts:
                source = self._part_file(row.upload_id, part.part_number)
                with source.open("rb") as handle:
                    while True:
                        buffer = handle.read(_STREAM_BUFFER)
                        if not buffer:
                            break
                        digest.update(buffer)
                        out.write(buffer)
                        total += len(buffer)
            out.flush()
            os.fsync(out.fileno())
        return total, digest.hexdigest()
