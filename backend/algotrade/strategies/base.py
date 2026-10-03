"""Strategy plugin interface (docs/design.md §2.2).

Strategies are pure: no I/O, no broker, no clock. Time and holdings arrive
in ``StrategyContext``. They emit signals only; risk and sizing live elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from algotrade.core.types import Signal
from algotrade.features.indicators import FeatureSet


@dataclass(frozen=True, slots=True)
class StrategyContext:
    now: datetime
    holding: bool
    entry_price: Decimal | None = None


class Strategy(Protocol):
    name: str

    def min_bars(self) -> int: ...

    def evaluate(self, symbol: str, fs: FeatureSet, ctx: StrategyContext) -> Signal: ...


TICK = Decimal("0.05")


def price(x: float) -> Decimal:
    """Round to the NSE tick size (0.05)."""
    return (Decimal(str(x)) / TICK).quantize(Decimal(1)) * TICK


def hold(symbol: str, strategy: str, *reasons: str) -> Signal:
    return Signal(symbol, strategy, "HOLD", 0.0, tuple(reasons))
