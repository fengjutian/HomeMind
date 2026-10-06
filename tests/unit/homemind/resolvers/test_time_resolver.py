"""Tests for the natural-language time resolver (Stage 2)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from homemind.infra.family.resolvers.time_range import TimeRangeResolver


FROZEN_NOW = datetime(2026, 4, 15, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai"))


def _frozen_clock() -> datetime:
    return FROZEN_NOW


def _resolver() -> TimeRangeResolver:
    return TimeRangeResolver(timezone="Asia/Shanghai", clock=_frozen_clock)


def test_today_range() -> None:
    r = _resolver().resolve("今天发生什么了")
    assert r.start_at == int(FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    assert r.end_at is not None
    assert r.end_at > r.start_at
    assert r.confidence > 0.0


def test_yesterday_range() -> None:
    r = _resolver().resolve("昨天")
    from datetime import timedelta
    expected_start = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    assert r.start_at == int(expected_start.timestamp())


def test_last_week_range() -> None:
    r = _resolver().resolve("上周")
    assert r.start_at is not None
    assert r.end_at is not None
    # Last week starts on Monday of the previous ISO week
    from datetime import timedelta
    monday_of_this_week = FROZEN_NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=FROZEN_NOW.weekday()
    )
    expected_start = monday_of_this_week - timedelta(days=7)
    assert r.start_at == int(expected_start.timestamp())


def test_last_year_range() -> None:
    r = _resolver().resolve("去年春节我们去了哪里")
    assert r.start_at is not None
    assert r.end_at is not None
    # 春节 falls in the previous calendar year (2025)
    from datetime import timedelta
    expected_start = datetime(2025, 1, 21, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert r.start_at == int(expected_start.timestamp())
    assert r.end_at - r.start_at == int(timedelta(days=7).total_seconds()) - 1


def test_this_year_range() -> None:
    r = _resolver().resolve("今年")
    expected_start = datetime(2026, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert r.start_at == int(expected_start.timestamp())


def test_year_before_last() -> None:
    r = _resolver().resolve("前年")
    expected_start = datetime(2024, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert r.start_at == int(expected_start.timestamp())


def test_recent_days() -> None:
    r = _resolver().resolve("最近7天")
    assert r.start_at is not None
    assert r.end_at is not None
    assert r.confidence > 0.5


def test_year_month_expression() -> None:
    r = _resolver().resolve("2023年5月")
    expected_start = datetime(2023, 5, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert r.start_at == int(expected_start.timestamp())


def test_unknown_expression_returns_zero_confidence() -> None:
    r = _resolver().resolve("某年某月某日")
    assert r.start_at is None
    assert r.end_at is None
    assert r.confidence == 0.0


def test_birthday_returns_low_confidence() -> None:
    """The birthday phrase carries low confidence because the actual date
    resolution happens after the relationship resolver runs."""
    r = _resolver().resolve("妈妈的生日")
    assert r.confidence < 0.5


def test_clock_is_injectable() -> None:
    """Tests can freeze time deterministically."""

    class _CustomClock:
        def __call__(self) -> datetime:
            return datetime(2030, 1, 1, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

    r = TimeRangeResolver(timezone="Asia/Shanghai", clock=_CustomClock()).resolve("今年")
    expected_start = datetime(2030, 1, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert r.start_at == int(expected_start.timestamp())