"""Upload session protocol — server-managed resumable upload.

Phase 1 of the resumable-upload plan: the wire contract, the protocol limits and
byte-range geometry. This module is pure (no I/O) so it can be unit-tested
directly.

Persistence lives in ``octop.infra.db.repos.upload_sessions``; staging paths
live in ``octop.infra.utils.paths``; the HTTP surface is phase 2.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "DEFAULT_UPLOAD_LIMITS",
    "TERMINAL_UPLOAD_STATUSES",
    "MissingRanges",
    "UploadLimits",
    "UploadPurpose",
    "UploadStatus",
    "expected_part_count",
    "merge_ranges",
    "missing_ranges",
    "overlaps",
    "part_bounds",
]


class UploadPurpose(StrEnum):
    """Where the assembled file is finally handed to."""

    CHAT_ATTACHMENT = "CHAT_ATTACHMENT"
    WORKSPACE_FILE = "WORKSPACE_FILE"
    FAMILY_ASSET = "FAMILY_ASSET"
    KNOWLEDGE_DOCUMENT = "KNOWLEDGE_DOCUMENT"


class UploadStatus(StrEnum):
    OPEN = "OPEN"
    ASSEMBLING = "ASSEMBLING"
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"


#: Statuses a session can never leave. ``FAILED`` is *not* terminal — the plan
#: keeps parts until TTL so the client can retry and resume.
TERMINAL_UPLOAD_STATUSES: frozenset[UploadStatus] = frozenset(
    {UploadStatus.COMPLETED, UploadStatus.ABORTED, UploadStatus.EXPIRED}
)


@dataclass(frozen=True)
class UploadLimits:
    """Server-side protocol limits.

    Every knob the plan requires to be configurable lives here, so routers never
    hard-code them. Values are overridable per deployment in phase 2.
    """

    default_chunk_bytes: int = 8 * 1024 * 1024
    min_chunk_bytes: int = 256 * 1024
    max_chunk_bytes: int = 64 * 1024 * 1024
    session_ttl_seconds: int = 24 * 3600
    max_open_sessions_per_user: int = 20
    #: 2 GiB — the plan's acceptance scenario uploads a 2 GB file, so the cap
    #: must accept it rather than sit just below it.
    max_file_bytes: int = 2 * 1024**3
    global_staging_quota_bytes: int = 20 * 1024**3
    #: Upper bound on the gap list returned by a status query. A 2 GiB upload at
    #: the minimum chunk size has ~800k parts; never return an unbounded array.
    max_missing_ranges: int = 1024

    def clamp_chunk_bytes(self, requested: int | None) -> int:
        """Coerce a client-requested chunk size into ``[min, max]``."""
        if requested is None or requested <= 0:
            return self.default_chunk_bytes
        return max(self.min_chunk_bytes, min(self.max_chunk_bytes, requested))

    def expires_at(self, *, now: int) -> int:
        return now + self.session_ttl_seconds


DEFAULT_UPLOAD_LIMITS = UploadLimits()


def expected_part_count(total_bytes: int, chunk_size: int) -> int:
    """Parts needed for *total_bytes* at *chunk_size*; a 0-byte file needs 0."""
    if total_bytes < 0:
        raise ValueError("total_bytes must be >= 0")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be > 0")
    return -(-total_bytes // chunk_size)


def part_bounds(part_number: int, *, total_bytes: int, chunk_size: int) -> tuple[int, int]:
    """``(offset, size)`` for a 1-based *part_number*.

    Part numbers must tile the file exactly: every part starts at
    ``(n - 1) * chunk_size`` and only the last one may be short.
    """
    if part_number < 1:
        raise ValueError("part_number is 1-based")
    total_parts = expected_part_count(total_bytes, chunk_size)
    if part_number > total_parts:
        raise ValueError(f"part_number {part_number} exceeds {total_parts} parts")
    offset = (part_number - 1) * chunk_size
    size = min(chunk_size, total_bytes - offset)
    return offset, size


def overlaps(a_offset: int, a_size: int, b_offset: int, b_size: int) -> bool:
    """True when two half-open ``[offset, offset + size)`` ranges intersect."""
    if a_size <= 0 or b_size <= 0:
        return False
    return a_offset < b_offset + b_size and b_offset < a_offset + a_size


def merge_ranges(ranges: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    """Sort and coalesce touching or overlapping half-open ranges."""
    ordered = sorted((o, s) for o, s in ranges if s > 0)
    merged: list[tuple[int, int]] = []
    for offset, size in ordered:
        if merged and offset <= merged[-1][0] + merged[-1][1]:
            prev_offset, prev_size = merged[-1]
            merged[-1] = (prev_offset, max(prev_offset + prev_size, offset + size) - prev_offset)
        else:
            merged.append((offset, size))
    return merged


@dataclass(frozen=True)
class MissingRanges:
    """Bounded result of a status query.

    ``ranges`` are half-open ``(offset, size)`` pairs that are genuinely
    missing. When the gap count exceeds the cap, the tail is dropped and
    ``truncated`` is set: part writes are idempotent, so a client resuming from
    the returned ranges may re-send parts it already sent without corrupting
    the session.
    """

    ranges: tuple[tuple[int, int], ...]
    truncated: bool


def missing_ranges(
    received: Iterable[tuple[int, int]],
    *,
    total_bytes: int,
    max_ranges: int,
) -> MissingRanges:
    """Byte ranges of the file that have not been received yet."""
    if total_bytes < 0:
        raise ValueError("total_bytes must be >= 0")
    if max_ranges <= 0:
        raise ValueError("max_ranges must be > 0")

    gaps: list[tuple[int, int]] = []
    cursor = 0
    for offset, size in merge_ranges(received):
        if offset > cursor:
            gaps.append((cursor, offset - cursor))
        cursor = max(cursor, offset + size)
    if cursor < total_bytes:
        gaps.append((cursor, total_bytes - cursor))

    if len(gaps) > max_ranges:
        return MissingRanges(ranges=tuple(gaps[:max_ranges]), truncated=True)
    return MissingRanges(ranges=tuple(gaps), truncated=False)
