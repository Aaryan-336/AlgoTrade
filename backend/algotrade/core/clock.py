"""Market calendar and clock helpers.

Strategies never read the wall clock; the engine passes time in. The
``Clock`` protocol lets backtests and tests drive time explicitly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass
class ManualClock:
    current: datetime

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("ManualClock requires timezone-aware datetimes")
        self.current = value


def to_ist(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(IST)


def ist_datetime(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST).astimezone(UTC)


class MarketCalendar:
    """NSE cash-market session: 09:15 to 15:30 IST on weekdays that are not
    exchange holidays. Holidays come from config (verify against NSE's list
    every year)."""

    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self.holidays = frozenset(holidays)

    def add_holidays(self, days: Iterable[date]) -> None:
        self.holidays = self.holidays | frozenset(days)

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def session_bounds(self, d: date) -> tuple[datetime, datetime]:
        return ist_datetime(d, MARKET_OPEN), ist_datetime(d, MARKET_CLOSE)

    def is_open(self, ts: datetime) -> bool:
        local = to_ist(ts)
        if not self.is_trading_day(local.date()):
            return False
        return MARKET_OPEN <= local.time() < MARKET_CLOSE

    def minutes_since_open(self, ts: datetime) -> float:
        open_, _ = self.session_bounds(to_ist(ts).date())
        return (ts - open_).total_seconds() / 60

    def minutes_to_close(self, ts: datetime) -> float:
        _, close = self.session_bounds(to_ist(ts).date())
        return (close - ts).total_seconds() / 60

    def in_entry_window(self, ts: datetime, skip_first_min: int, skip_last_min: int) -> bool:
        return (
            self.is_open(ts)
            and self.minutes_since_open(ts) >= skip_first_min
            and self.minutes_to_close(ts) > skip_last_min
        )

    def next_trading_day(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while not self.is_trading_day(nxt):
            nxt += timedelta(days=1)
        return nxt

    def previous_trading_day(self, d: date) -> date:
        prv = d - timedelta(days=1)
        while not self.is_trading_day(prv):
            prv -= timedelta(days=1)
        return prv

    def trading_days_between(self, start: date, end: date) -> int:
        """Trading days in (start, end]."""
        n, d = 0, start
        while d < end:
            d += timedelta(days=1)
            if self.is_trading_day(d):
                n += 1
        return n

    def week_start(self, d: date) -> date:
        return d - timedelta(days=d.weekday())
