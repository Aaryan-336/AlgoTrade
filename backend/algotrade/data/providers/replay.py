"""Deterministic provider for tests, backtests and the offline demo."""

from __future__ import annotations

import math
import random
from bisect import bisect_right
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from algotrade.core.clock import IST, Clock, MarketCalendar
from algotrade.core.types import Bar, ProviderHealth, Tick


def _q(x: float) -> Decimal:
    return Decimal(str(round(x, 2)))


def daily_ts(d: date) -> datetime:
    """Daily bars are stamped at 00:00 IST of their date, like Upstox and yfinance."""
    return datetime.combine(d, time(0, 0), tzinfo=IST).astimezone(UTC)


def synthetic_daily_bars(symbol: str, start: date, days: int, seed: int,
                         start_price: float = 1000.0, drift: float = 0.0004,
                         vol: float = 0.018, calendar: MarketCalendar | None = None) -> list[Bar]:
    """Geometric random walk with realistic OHLC structure. For demos and
    tests only; it has no predictive meaning."""
    cal = calendar or MarketCalendar()
    rng = random.Random(f"{symbol}:{seed}")
    bars: list[Bar] = []
    price = start_price
    d = start
    while len(bars) < days:
        if cal.is_trading_day(d):
            gap = rng.gauss(0, vol * 0.3)
            open_ = price * math.exp(gap)
            ret = rng.gauss(drift, vol)
            close = open_ * math.exp(ret)
            hi = max(open_, close) * (1 + abs(rng.gauss(0, vol * 0.5)))
            lo = min(open_, close) * (1 - abs(rng.gauss(0, vol * 0.5)))
            volume = int(abs(rng.gauss(2_000_000, 600_000))) + 50_000
            bars.append(Bar(symbol, "1d", daily_ts(d), _q(open_), _q(hi), _q(lo), _q(close),
                            volume))
            price = close
        d += timedelta(days=1)
    return bars


class ReplayProvider:
    name = "replay"

    def __init__(self, bars: dict[str, list[Bar]], clock: Clock) -> None:
        self._bars = {s: sorted(b, key=lambda x: x.ts) for s, b in bars.items()}
        self._ts = {s: [b.ts for b in bs] for s, bs in self._bars.items()}
        self.clock = clock
        self._last: datetime | None = None
        self.fail = False  # chaos tests flip this to simulate an outage

    def _visible(self, symbol: str) -> list[Bar]:
        now = self.clock.now()
        bars = self._bars.get(symbol, [])
        if not bars:
            return []
        # A daily bar is visible once its session has closed (15:30 IST).
        cutoff = [b for b in bars[: bisect_right(self._ts[symbol], now)]
                  if b.timeframe != "1d" or b.ts + timedelta(hours=15, minutes=30) <= now]
        return cutoff

    async def ltp(self, symbols: list[str]) -> list[Tick]:
        if self.fail:
            from algotrade.data.providers.base import ProviderError
            raise ProviderError("replay outage")
        now = self.clock.now()
        out: list[Tick] = []
        for s in symbols:
            bars = self._bars.get(s)
            if not bars:
                continue
            idx = bisect_right(self._ts[s], now) - 1
            if idx < 0:
                continue
            bar = bars[idx]
            session_closed = bar.ts + timedelta(hours=15, minutes=30) <= now
            price = bar.close if session_closed else bar.open
            out.append(Tick(s, now, price, bar.volume))
        self._last = now
        return out

    async def history(self, symbol: str, timeframe: str, start: date, end: date) -> list[Bar]:
        return [b for b in self._visible(symbol)
                if b.timeframe == timeframe and start <= b.ts.astimezone(IST).date() <= end]

    async def intraday(self, symbol: str, timeframe: str) -> list[Bar]:
        today = self.clock.now().astimezone(IST).date()
        return [b for b in self._visible(symbol)
                if b.timeframe == timeframe and b.ts.astimezone(IST).date() == today]

    def all_bars(self, symbol: str) -> list[Bar]:
        return list(self._bars.get(symbol, []))

    def health(self) -> ProviderHealth:
        return ProviderHealth(self.name, not self.fail, self._last)

    def is_ready(self) -> bool:
        return True
