"""Transaction cost model shared by the paper broker, backtester and the
Risk Engine's expected-edge check. Values come from config (verify them
against the broker's charge calculator)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from algotrade.config.models import CostConfig
from algotrade.core.types import Charges, Side

_C = Decimal("0.01")
_HUNDRED = Decimal(100)
_CRORE = Decimal(10_000_000)


def _r(x: Decimal) -> Decimal:
    return x.quantize(_C, rounding=ROUND_HALF_UP)


class CostModel:
    def __init__(self, cfg: CostConfig) -> None:
        self.cfg = cfg

    def charges(self, side: Side, qty: int, price: Decimal, dp_applies: bool = True) -> Charges:
        c = self.cfg
        turnover = price * qty
        brokerage = min(turnover * c.brokerage_pct / _HUNDRED, c.brokerage_max)
        stt = turnover * c.stt_pct / _HUNDRED
        exchange = turnover * c.exchange_txn_pct / _HUNDRED
        sebi = turnover * c.sebi_per_crore / _CRORE
        stamp = turnover * c.stamp_duty_buy_pct / _HUNDRED if side is Side.BUY else Decimal(0)
        gst = (brokerage + exchange + sebi) * c.gst_pct / _HUNDRED
        dp = c.dp_charge_per_sell if (side is Side.SELL and dp_applies) else Decimal(0)
        return Charges(brokerage=_r(brokerage), stt=_r(stt), exchange=_r(exchange),
                       sebi=_r(sebi), stamp_duty=_r(stamp), gst=_r(gst), dp=_r(dp))

    def round_trip(self, qty: int, entry: Decimal, exit_: Decimal) -> Decimal:
        return (self.charges(Side.BUY, qty, entry).total
                + self.charges(Side.SELL, qty, exit_).total)
