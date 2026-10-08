"""Upload session protocol — limits and byte-range geometry."""

from __future__ import annotations

import pytest

from octop.infra.uploads.protocol import (
    DEFAULT_UPLOAD_LIMITS,
    TERMINAL_UPLOAD_STATUSES,
    UploadLimits,
    UploadStatus,
    expected_part_count,
    merge_ranges,
    missing_ranges,
    overlaps,
    part_bounds,
)


def test_limits_cover_the_plans_acceptance_file_size() -> None:
    limits = DEFAULT_UPLOAD_LIMITS
    # The plan uploads a 2 GB file; the cap must accept it, not sit below it.
    assert limits.max_file_bytes >= 2 * 1024**3
    assert limits.min_chunk_bytes <= limits.default_chunk_bytes <= limits.max_chunk_bytes
    assert limits.session_ttl_seconds > 0
    assert limits.max_open_sessions_per_user > 0
    assert limits.global_staging_quota_bytes >= limits.max_file_bytes


def test_clamp_chunk_bytes() -> None:
    limits = UploadLimits()
    assert limits.clamp_chunk_bytes(None) == limits.default_chunk_bytes
    assert limits.clamp_chunk_bytes(0) == limits.default_chunk_bytes
    assert limits.clamp_chunk_bytes(-5) == limits.default_chunk_bytes
    assert limits.clamp_chunk_bytes(1) == limits.min_chunk_bytes
    assert limits.clamp_chunk_bytes(10**12) == limits.max_chunk_bytes
    assert limits.clamp_chunk_bytes(limits.default_chunk_bytes) == limits.default_chunk_bytes


def test_expected_part_count() -> None:
    assert expected_part_count(0, 1024) == 0
    assert expected_part_count(1, 1024) == 1
    assert expected_part_count(1024, 1024) == 1
    assert expected_part_count(1025, 1024) == 2
    assert expected_part_count(2048, 1024) == 2
    with pytest.raises(ValueError):
        expected_part_count(-1, 1024)
    with pytest.raises(ValueError):
        expected_part_count(10, 0)


def test_part_bounds_tile_the_file_exactly() -> None:
    bounds = [part_bounds(n, total_bytes=2500, chunk_size=1024) for n in (1, 2, 3)]
    assert bounds == [(0, 1024), (1024, 1024), (2048, 452)]
    # No gap, no overlap.
    assert sum(size for _o, size in bounds) == 2500
    for (_o1, s1), (o2, _s2) in zip(bounds, bounds[1:], strict=False):
        assert _o1 + s1 == o2


def test_part_bounds_rejects_out_of_range_and_zero_based() -> None:
    with pytest.raises(ValueError):
        part_bounds(0, total_bytes=100, chunk_size=10)
    # 100 bytes at 10 is 10 parts; part 11 does not exist.
    assert part_bounds(10, total_bytes=100, chunk_size=10) == (90, 10)
    with pytest.raises(ValueError):
        part_bounds(11, total_bytes=100, chunk_size=10)
    with pytest.raises(ValueError):
        part_bounds(1, total_bytes=0, chunk_size=10)


def test_overlaps() -> None:
    assert overlaps(0, 10, 5, 10) is True
    assert overlaps(5, 10, 0, 10) is True
    assert overlaps(0, 10, 10, 10) is False  # touching is not overlapping
    assert overlaps(10, 10, 0, 10) is False
    assert overlaps(0, 10, 2, 3) is True
    assert overlaps(0, 0, 0, 10) is False  # empty range never overlaps
    assert overlaps(0, 10, 50, 0) is False


def test_merge_ranges_coalesces_touching_and_overlapping() -> None:
    assert merge_ranges([]) == []
    assert merge_ranges([(20, 10), (0, 10)]) == [(0, 10), (20, 10)]
    assert merge_ranges([(0, 10), (10, 10)]) == [(0, 20)]
    assert merge_ranges([(0, 20), (10, 5)]) == [(0, 20)]
    assert merge_ranges([(0, 10), (5, 10), (30, 5)]) == [(0, 15), (30, 5)]
    assert merge_ranges([(0, 0), (5, 5)]) == [(5, 5)]


def test_missing_ranges_reports_only_real_gaps() -> None:
    result = missing_ranges([(0, 1024), (2048, 1024)], total_bytes=4096, max_ranges=100)
    assert result.truncated is False
    assert result.ranges == ((1024, 1024), (3072, 1024))


def test_missing_ranges_empty_when_complete() -> None:
    result = missing_ranges([(0, 4096)], total_bytes=4096, max_ranges=100)
    assert result.ranges == ()
    assert result.truncated is False


def test_missing_ranges_empty_for_zero_byte_file() -> None:
    assert missing_ranges([], total_bytes=0, max_ranges=10).ranges == ()


def test_missing_ranges_drops_partial_final_chunk_gap() -> None:
    # 5000 bytes at 1024: parts cover 0..4096, tail 4096..5000 still missing.
    result = missing_ranges(
        [(0, 1024), (1024, 1024), (2048, 1024), (3072, 1024)], total_bytes=5000, max_ranges=100
    )
    assert result.ranges == ((4096, 904),)


def test_missing_ranges_is_bounded_for_a_hundred_thousand_parts() -> None:
    """The plan forbids unbounded arrays; a pathological gap list must truncate."""
    total = 1024 * 100_000
    # Every other part received -> ~50k gaps.
    received = [(i * 1024, 1024) for i in range(0, total // 1024, 2)]
    result = missing_ranges(received, total_bytes=total, max_ranges=1024)
    assert result.truncated is True
    assert len(result.ranges) == 1024
    # Every returned range is genuinely missing, so resuming from them is safe.
    for offset, size in result.ranges:
        assert not overlaps(offset, size, *received[0])


def test_missing_ranges_validates_arguments() -> None:
    with pytest.raises(ValueError):
        missing_ranges([], total_bytes=-1, max_ranges=10)
    with pytest.raises(ValueError):
        missing_ranges([], total_bytes=10, max_ranges=0)


def test_failed_is_not_terminal_so_parts_survive_until_ttl() -> None:
    assert UploadStatus.FAILED not in TERMINAL_UPLOAD_STATUSES
    assert UploadStatus.COMPLETED in TERMINAL_UPLOAD_STATUSES
    assert UploadStatus.ABORTED in TERMINAL_UPLOAD_STATUSES
    assert UploadStatus.EXPIRED in TERMINAL_UPLOAD_STATUSES
