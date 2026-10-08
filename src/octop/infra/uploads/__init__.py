"""Resumable upload domain: wire protocol and persistence primitives."""

from __future__ import annotations

from octop.infra.uploads.protocol import (
    DEFAULT_UPLOAD_LIMITS,
    TERMINAL_UPLOAD_STATUSES,
    MissingRanges,
    UploadLimits,
    UploadPurpose,
    UploadStatus,
    expected_part_count,
    merge_ranges,
    missing_ranges,
    overlaps,
    part_bounds,
)

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
