"""Volatility-compression breakout (docs/strategy-spec.md §3.2)."""

from __future__ import annotations

from algotrade.config.models import BreakoutParams
from algotrade.core.types import Signal
from algotrade.features.indicators import FeatureSet, is_valid
from algotrade.strategies.base import StrategyContext, hold, price


class BreakoutStrategy:
    name = "breakout"

    def __init__(self, params: BreakoutParams) -> None:
        self.p = params

    def min_bars(self) -> int:
        return max(self.p.lookback, self.p.squeeze_lookback, self.p.atr_period) * 2 + 2

    def evaluate(self, symbol: str, fs: FeatureSet, ctx: StrategyContext) -> Signal:
        p = self.p
        if len(fs) < self.min_bars():
            return hold(symbol, self.name, "insufficient history")
        close = fs.close
        a = fs.atr(p.atr_period)[-1]
        level = fs.prior_high(p.lookback)
        vavg = fs.volume_sma(p.lookback)[-2]  # average before the breakout bar
        exit_ema = fs.ema(p.lookback)[-1]
        if not all(is_valid(x) for x in (a, level, vavg, exit_ema)) or a <= 0 or vavg <= 0:
            return hold(symbol, self.name, "indicators not ready")

        if ctx.holding:
            if close < exit_ema:
                return Signal(symbol, self.name, "SELL", 0.7,
                              (f"close {close:.2f} fell below EMA{p.lookback} {exit_ema:.2f}",))
            return hold(symbol, self.name, "breakout holding")

        widths = [w for w in fs.bb_width(p.squeeze_lookback)[-p.squeeze_lookback - 1 : -1]
                  if is_valid(w)]
        squeezed = bool(widths) and widths[-1] <= sorted(widths)[len(widths) // 2]
        vol_ratio = fs.volumes[-1] / vavg
        checks = {
            f"close {close:.2f} above {p.lookback}-bar high {level:.2f}": close > level,
            f"volume {vol_ratio:.1f}x average (need {p.volume_mult}x)": vol_ratio >= p.volume_mult,
            "volatility was compressed before the break": squeezed,
        }
        if not all(checks.values()):
            return hold(symbol, self.name, *(f"not {k}" for k, ok in checks.items() if not ok))

        stop = close - p.atr_stop_mult * a
        target = close + p.reward_risk * (close - stop)
        strength = min(1.0, 0.5 + (vol_ratio - p.volume_mult) * 0.1 + (close / level - 1) * 5)
        return Signal(symbol, self.name, "BUY", max(0.0, strength), tuple(checks),
                      suggested_stop=price(stop), suggested_target=price(target))
