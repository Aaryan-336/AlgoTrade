from __future__ import annotations

from algotrade.config.models import StrategiesConfig
from algotrade.strategies.base import Strategy
from algotrade.strategies.breakout import BreakoutStrategy
from algotrade.strategies.trend import TrendStrategy


def build_strategies(cfg: StrategiesConfig) -> list[Strategy]:
    out: list[Strategy] = []
    if cfg.trend.enabled:
        out.append(TrendStrategy(cfg.trend))
    if cfg.breakout.enabled:
        out.append(BreakoutStrategy(cfg.breakout))
    return out


def max_holding_days(cfg: StrategiesConfig, strategy: str) -> int:
    if strategy == "breakout":
        return cfg.breakout.max_holding_days
    return cfg.trend.max_holding_days
