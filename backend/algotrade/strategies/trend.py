"""Trend following (docs/strategy-spec.md §3.1)."""

from __future__ import annotations

from algotrade.config.models import TrendParams
from algotrade.core.types import Signal
from algotrade.features.indicators import FeatureSet, is_valid
from algotrade.strategies.base import StrategyContext, hold, price


class TrendStrategy:
    name = "trend"

    def __init__(self, params: TrendParams) -> None:
        self.p = params

    def min_bars(self) -> int:
        return max(self.p.slow_ema, self.p.volume_period, self.p.atr_period) + 5

    def evaluate(self, symbol: str, fs: FeatureSet, ctx: StrategyContext) -> Signal:
        p = self.p
        if len(fs) < self.min_bars():
            return hold(symbol, self.name, "insufficient history")
        close = fs.close
        fast, slow = fs.ema(p.fast_ema)[-1], fs.ema(p.slow_ema)[-1]
        r = fs.rsi(p.rsi_period)[-1]
        a = fs.atr(p.atr_period)[-1]
        vavg = fs.volume_sma(p.volume_period)[-1]
        if not all(is_valid(x) for x in (fast, slow, r, a, vavg)) or a <= 0:
            return hold(symbol, self.name, "indicators not ready")

        if ctx.holding:
            if close < fast:
                return Signal(symbol, self.name, "SELL", 0.7,
                              (f"close {close:.2f} below EMA{p.fast_ema} {fast:.2f}",))
            return hold(symbol, self.name, "trend intact")

        checks = {
            f"close above EMA{p.slow_ema}": close > slow,
            f"EMA{p.fast_ema} above EMA{p.slow_ema}": fast > slow,
            f"RSI {r:.0f} in [{p.rsi_min:.0f}, {p.rsi_max:.0f}]": p.rsi_min <= r <= p.rsi_max,
            f"volume above {p.volume_period}-bar average": fs.volumes[-1] > vavg,
        }
        if not all(checks.values()):
            failed = [k for k, ok in checks.items() if not ok]
            return hold(symbol, self.name, *(f"not {f}" for f in failed))

        stop = close - p.atr_stop_mult * a
        target = close + p.reward_risk * (close - stop)
        strength = min(1.0, 0.5 + (fast / slow - 1) * 10 + (fs.volumes[-1] / vavg - 1) * 0.1)
        return Signal(symbol, self.name, "BUY", max(0.0, strength), tuple(checks),
                      suggested_stop=price(stop), suggested_target=price(target))
