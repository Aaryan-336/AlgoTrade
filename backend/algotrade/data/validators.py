"""Data validation (docs/data-sources.md §5). Invalid data is quarantined,
never used, and counted; too much quarantined data trips a breaker."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from algotrade.core.types import Bar, Tick

MAX_CLOCK_SKEW = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class Issue:
    symbol: str
    kind: str
    detail: str


def validate_bar(bar: Bar, prev: Bar | None, now: datetime, max_jump_pct: float) -> list[Issue]:
    issues: list[Issue] = []
    s = bar.symbol
    if min(bar.open, bar.high, bar.low, bar.close) <= 0:
        issues.append(Issue(s, "non_positive_price", str(bar)))
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        issues.append(Issue(s, "ohlc_inconsistent", f"o={bar.open} h={bar.high} "
                                                    f"l={bar.low} c={bar.close}"))
    if bar.volume < 0:
        issues.append(Issue(s, "negative_volume", str(bar.volume)))
    if bar.ts > now + MAX_CLOCK_SKEW:
        issues.append(Issue(s, "future_timestamp", bar.ts.isoformat()))
    if prev is not None:
        if bar.ts <= prev.ts:
            issues.append(Issue(s, "out_of_order_or_duplicate", bar.ts.isoformat()))
        elif prev.close > 0:
            jump = abs(bar.close - prev.close) / prev.close * 100
            if jump > Decimal(str(max_jump_pct)):
                issues.append(Issue(s, "bad_tick_jump", f"{jump:.1f}% vs previous close"))
    return issues


def clean_series(bars: Sequence[Bar], now: datetime, max_jump_pct: float) -> tuple[list[Bar],
                                                                                  list[Issue]]:
    """Drop invalid bars; keep the valid chain. Returns (clean, issues)."""
    clean: list[Bar] = []
    issues: list[Issue] = []
    prev_raw: Bar | None = None
    for b in bars:
        # Jumps are measured against the previous raw bar so one bad print is
        # quarantined without poisoning every bar after it.
        found = validate_bar(b, prev_raw, now, max_jump_pct)
        if clean and b.ts <= clean[-1].ts:
            found.append(Issue(b.symbol, "out_of_order_or_duplicate", b.ts.isoformat()))
        if found:
            issues.extend(found)
        else:
            clean.append(b)
        if prev_raw is None or b.ts > prev_raw.ts:
            prev_raw = b
    return clean, issues


def validate_tick(tick: Tick, last_price: Decimal | None, now: datetime,
                  max_jump_pct: float) -> list[Issue]:
    issues: list[Issue] = []
    if tick.ltp <= 0:
        issues.append(Issue(tick.symbol, "non_positive_price", str(tick.ltp)))
    if tick.ts > now + MAX_CLOCK_SKEW:
        issues.append(Issue(tick.symbol, "future_timestamp", tick.ts.isoformat()))
    if last_price and last_price > 0 and tick.ltp > 0:
        jump = abs(tick.ltp - last_price) / last_price * 100
        if jump > Decimal(str(max_jump_pct)):
            issues.append(Issue(tick.symbol, "bad_tick_jump", f"{jump:.1f}% vs last price"))
    return issues


@dataclass
class QuarantineMonitor:
    """Counts quarantined items in a rolling window."""

    window: timedelta = timedelta(minutes=15)
    threshold: int = 20
    events: deque[datetime] = field(default_factory=deque)

    def add(self, ts: datetime, n: int = 1) -> None:
        for _ in range(n):
            self.events.append(ts)
        self._trim(ts)

    def _trim(self, now: datetime) -> None:
        while self.events and self.events[0] < now - self.window:
            self.events.popleft()

    def tripped(self, now: datetime) -> bool:
        self._trim(now)
        return len(self.events) >= self.threshold
