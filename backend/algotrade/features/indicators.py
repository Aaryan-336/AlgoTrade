"""Indicator library: pure functions over float series, oldest first.

Values before an indicator has enough history are ``nan``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from functools import cache

from algotrade.core.types import Bar

NAN = float("nan")


def sma(values: Sequence[float], period: int) -> list[float]:
    out = [NAN] * len(values)
    if period <= 0:
        raise ValueError("period must be positive")
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def ema(values: Sequence[float], period: int) -> list[float]:
    """EMA seeded with the SMA of the first ``period`` values."""
    out = [NAN] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float]:
    """Wilder's RSI."""
    out = [NAN] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        ch = closes[i] - closes[i - 1]
        gains += max(ch, 0.0)
        losses += max(-ch, 0.0)
    avg_g, avg_l = gains / period, losses / period

    def _val(g: float, lo: float) -> float:
        if lo == 0:
            return 100.0 if g > 0 else 50.0
        return 100.0 - 100.0 / (1 + g / lo)

    out[period] = _val(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(ch, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-ch, 0.0)) / period
        out[i] = _val(avg_g, avg_l)
    return out


def true_range(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float]) -> list[float]:
    tr: list[float] = []
    for i in range(len(closes)):
        if i == 0:
            tr.append(highs[0] - lows[0])
        else:
            pc = closes[i - 1]
            tr.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    return tr


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> list[float]:
    """Wilder's ATR."""
    tr = true_range(highs, lows, closes)
    out = [NAN] * len(tr)
    if len(tr) < period:
        return out
    prev = sum(tr[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(tr)):
        prev = (prev * (period - 1) + tr[i]) / period
        out[i] = prev
    return out


def rolling_max(values: Sequence[float], period: int) -> list[float]:
    out = [NAN] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = max(values[i - period + 1 : i + 1])
    return out


def rolling_std(values: Sequence[float], period: int) -> list[float]:
    """Population standard deviation over a sliding window (running sums)."""
    out = [NAN] * len(values)
    s = sq = 0.0
    for i, v in enumerate(values):
        s += v
        sq += v * v
        if i >= period:
            old = values[i - period]
            s -= old
            sq -= old * old
        if i >= period - 1:
            m = s / period
            out[i] = math.sqrt(max(sq / period - m * m, 0.0))
    return out


def pct_returns(closes: Sequence[float]) -> list[float]:
    return [(closes[i] / closes[i - 1] - 1.0) if closes[i - 1] else 0.0
            for i in range(1, len(closes))]


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    n = min(len(a), len(b))
    if n < 10:
        return 0.0
    xa, xb = a[-n:], b[-n:]
    ma, mb = sum(xa) / n, sum(xb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(xa, xb, strict=True))
    va = sum((x - ma) ** 2 for x in xa)
    vb = sum((y - mb) ** 2 for y in xb)
    if va == 0 or vb == 0:
        return 0.0
    return cov / math.sqrt(va * vb)


def is_valid(x: float) -> bool:
    return not math.isnan(x)


class FeatureSet:
    """Cached indicator access over one symbol's completed bars."""

    def __init__(self, bars: Sequence[Bar]) -> None:
        self.bars = list(bars)
        self.opens = [float(b.open) for b in self.bars]
        self.highs = [float(b.high) for b in self.bars]
        self.lows = [float(b.low) for b in self.bars]
        self.closes = [float(b.close) for b in self.bars]
        self.volumes = [float(b.volume) for b in self.bars]

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def last(self) -> Bar:
        return self.bars[-1]

    @property
    def close(self) -> float:
        return self.closes[-1]

    @cache  # noqa: B019 - FeatureSet is short-lived and immutable
    def ema(self, period: int) -> list[float]:
        return ema(self.closes, period)

    @cache  # noqa: B019
    def rsi(self, period: int) -> list[float]:
        return rsi(self.closes, period)

    @cache  # noqa: B019
    def atr(self, period: int) -> list[float]:
        return atr(self.highs, self.lows, self.closes, period)

    @cache  # noqa: B019
    def volume_sma(self, period: int) -> list[float]:
        return sma(self.volumes, period)

    @cache  # noqa: B019
    def prior_high(self, lookback: int) -> float:
        """Highest high of the ``lookback`` bars before the last bar."""
        if len(self.highs) <= lookback:
            return NAN
        return max(self.highs[-lookback - 1 : -1])

    @cache  # noqa: B019
    def bb_width(self, period: int) -> list[float]:
        mid = sma(self.closes, period)
        sd = rolling_std(self.closes, period)
        return [(4 * s / m) if is_valid(m) and m else NAN for m, s in zip(mid, sd, strict=True)]

    @cache  # noqa: B019
    def returns(self) -> list[float]:
        return pct_returns(self.closes)

    def avg_traded_value(self, period: int = 20) -> float:
        n = min(period, len(self.bars))
        if n == 0:
            return 0.0
        vals = [c * v for c, v in zip(self.closes[-n:], self.volumes[-n:], strict=True)]
        return sum(vals) / n

    def avg_volume(self, period: int = 20) -> float:
        n = min(period, len(self.volumes))
        return sum(self.volumes[-n:]) / n if n else 0.0
