"""Resolve natural-language time expressions to ``ResolvedTimeRange``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from homemind.infra.family.resolvers.models import ResolvedTimeRange


# Day-count per Chinese relative term.
_PHRASE_RULES: tuple[tuple[str, int], ...] = (
    ("今天", 0),
    ("昨天", -1),
    ("前天", -2),
)


def _start_of_day(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_week(now: datetime) -> datetime:
    return _start_of_day(now) - timedelta(days=(now.weekday()))


def _start_of_month(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _start_of_year(now: datetime) -> datetime:
    return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


@dataclass(frozen=True)
class TimeRangeResolver:
    """Resolve Chinese / English time expressions to ``ResolvedTimeRange``.

    All times are anchored to the family timezone. The current time is
    injected for deterministic tests via the ``clock`` callable.
    """

    timezone: str
    clock: Callable[[], datetime] = datetime.now

    def resolve(self, expression: str) -> ResolvedTimeRange:
        """Return the resolved range, or a 0-confidence empty range if unknown."""
        text = (expression or "").strip()
        if not text:
            return ResolvedTimeRange(None, None, None, 0.0)
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(self.timezone))
        else:
            now = now.astimezone(ZoneInfo(self.timezone))

        if (matched := self._match_relative_day(text, now)) is not None:
            return matched
        if (matched := self._match_relative_week(text, now)) is not None:
            return matched
        if (matched := self._match_relative_month(text, now)) is not None:
            return matched
        if (matched := self._match_recent_days(text, now)) is not None:
            return matched
        if (matched := self._match_year_month(text, now)) is not None:
            return matched
        # Festival matching must take precedence over plain "去年" /
        # "今年" because both can appear in the same query
        # (e.g. "去年春节").
        if (matched := self._match_year_and_festival(text, now)) is not None:
            return matched
        if (matched := self._match_relative_year(text, now)) is not None:
            return matched
        if (matched := self._match_member_birthday(text, now)) is not None:
            return matched
        return ResolvedTimeRange(None, None, text, 0.0)

    # ------------------------------------------------------------- helpers

    def _range(
        self,
        start: datetime,
        end: datetime,
        expression: str,
        confidence: float,
    ) -> ResolvedTimeRange:
        return ResolvedTimeRange(
            start_at=int(start.timestamp()),
            end_at=int(end.timestamp()),
            expression=expression,
            confidence=confidence,
        )

    def _match_relative_day(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        for phrase, day_offset in _PHRASE_RULES:
            if phrase in text:
                target = _start_of_day(now) + timedelta(days=day_offset)
                end = target + timedelta(days=1) - timedelta(seconds=1)
                return self._range(target, end, text, 0.9)
        return None

    def _match_relative_week(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        if "本周" in text or "this week" in text.casefold():
            start = _start_of_week(now)
            end = start + timedelta(days=7) - timedelta(seconds=1)
            return self._range(start, end, text, 0.9)
        if "上周" in text or "last week" in text.casefold():
            start = _start_of_week(now) - timedelta(days=7)
            end = start + timedelta(days=7) - timedelta(seconds=1)
            return self._range(start, end, text, 0.85)
        return None

    def _match_relative_month(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        if "本月" in text or "this month" in text.casefold():
            start = _start_of_month(now)
            if start.month == 12:
                next_start = start.replace(year=start.year + 1, month=1)
            else:
                next_start = start.replace(month=start.month + 1)
            end = next_start - timedelta(seconds=1)
            return self._range(start, end, text, 0.9)
        if "上个月" in text or "last month" in text.casefold():
            this_month = _start_of_month(now)
            if this_month.month == 1:
                last_month = this_month.replace(year=this_month.year - 1, month=12)
            else:
                last_month = this_month.replace(month=this_month.month - 1)
            end = this_month - timedelta(seconds=1)
            return self._range(last_month, end, text, 0.85)
        return None

    def _match_relative_year(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        if "前年" in text or "the year before last" in text.casefold():
            return self._year_range(now.year - 2, text, 0.85)
        if "去年" in text or "last year" in text.casefold():
            return self._year_range(now.year - 1, text, 0.9)
        if "今年" in text or "this year" in text.casefold():
            return self._year_range(now.year, text, 0.95)
        return None

    def _year_range(self, year: int, text: str, confidence: float) -> ResolvedTimeRange:
        start = datetime(year, 1, 1, tzinfo=ZoneInfo(self.timezone))
        end = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(self.timezone)) - timedelta(seconds=1)
        return self._range(start, end, text, confidence)

    def _match_recent_days(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        match = re.search(r"最近\s*(\d+)\s*天", text)
        if match is None:
            match = re.search(r"last\s+(\d+)\s+days", text, re.IGNORECASE)
        if match is None:
            return None
        days = int(match.group(1))
        end = now
        start = now - timedelta(days=days - 1)
        start = _start_of_day(start)
        return self._range(start, end, text, 0.9)

    def _match_year_month(self, text: str, now: datetime) -> ResolvedTimeRange | None:
        match = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", text)
        if match is None:
            return None
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return ResolvedTimeRange(None, None, text, 0.0)
        start = datetime(year, month, 1, tzinfo=ZoneInfo(self.timezone))
        if month == 12:
            next_start = datetime(year + 1, 1, 1, tzinfo=ZoneInfo(self.timezone))
        else:
            next_start = datetime(year, month + 1, 1, tzinfo=ZoneInfo(self.timezone))
        end = next_start - timedelta(seconds=1)
        return self._range(start, end, text, 0.95)

    def _match_year_and_festival(
        self, text: str, now: datetime
    ) -> ResolvedTimeRange | None:
        match = re.search(r"(今年|去年|前年|this|last)?\s*春节", text)
        if match is None:
            return None
        qualifier = match.group(1)
        if qualifier in (None, "", "今年", "this"):
            year = now.year
        elif qualifier in ("last", "去年"):
            year = now.year - 1
        else:
            year = now.year - 2
        # Chinese New Year heuristic: Jan 21 to Jan 27 of the resolved year.
        start = datetime(year, 1, 21, tzinfo=ZoneInfo(self.timezone))
        end = start + timedelta(days=7) - timedelta(seconds=1)
        return self._range(start, end, text, 0.7)

    def _match_member_birthday(
        self, text: str, now: datetime
    ) -> ResolvedTimeRange | None:
        if "生日" not in text and "birthday" not in text.casefold():
            return None
        return ResolvedTimeRange(None, None, text, 0.4)


__all__ = ["TimeRangeResolver"]