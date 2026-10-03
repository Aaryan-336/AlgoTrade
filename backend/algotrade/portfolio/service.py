"""Portfolio Service: positions, cash and P&L, rebuilt from fills.

On restart the portfolio is rebuilt by replaying every fill (NFR-05); only
the stop/target metadata is read from the positions table.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from algotrade.core.types import Fill, Position, Side
from algotrade.risk.engine import PositionView


@dataclass(frozen=True, slots=True)
class ClosedTrade:
    symbol: str
    strategy: str
    opened_at: datetime
    closed_at: datetime
    qty: int
    entry: Decimal
    exit: Decimal
    pnl: Decimal  # after all charges on both legs
    fees: Decimal


class Portfolio:
    def __init__(self, starting_cash: Decimal) -> None:
        self.starting_cash = starting_cash
        self.cash = starting_cash
        self.positions: dict[str, Position] = {}
        self.realized = Decimal(0)
        self.fees = Decimal(0)
        self.closed_trades: list[ClosedTrade] = []

    # ------------------------------------------------------------- updates
    def apply_fill(self, f: Fill, strategy: str, sector: str, stop: Decimal | None = None,
                   target: Decimal | None = None) -> ClosedTrade | None:
        fee = f.charges.total
        self.fees += fee
        if f.side is Side.BUY:
            self.cash -= f.price * f.qty + fee
            p = self.positions.get(f.symbol)
            if p is None:
                self.positions[f.symbol] = Position(
                    symbol=f.symbol, qty=f.qty, avg_price=f.price, opened_at=f.ts,
                    strategy=strategy, sector=sector, stop=stop, target=target,
                    initial_stop=stop, highest_close=f.price, fees=fee)
            else:
                total = p.avg_price * p.qty + f.price * f.qty
                p.qty += f.qty
                p.avg_price = total / p.qty
                p.fees += fee
            return None

        p = self.positions.get(f.symbol)
        if p is None or p.qty < f.qty:
            raise ValueError(f"sell fill for {f.symbol} exceeds position")
        self.cash += f.price * f.qty - fee
        # Allocate the position's entry fees pro rata to the quantity sold.
        entry_fee_share = p.fees * f.qty / p.qty
        pnl = (f.price - p.avg_price) * f.qty - fee - entry_fee_share
        self.realized += (f.price - p.avg_price) * f.qty
        p.fees -= entry_fee_share
        p.qty -= f.qty
        trade = ClosedTrade(f.symbol, p.strategy, p.opened_at, f.ts, f.qty, p.avg_price,
                            f.price, pnl, fee + entry_fee_share)
        self.closed_trades.append(trade)
        if p.qty == 0:
            del self.positions[f.symbol]
        return trade

    # ------------------------------------------------------------ valuation
    def market_value(self, prices: Mapping[str, Decimal]) -> Decimal:
        return sum((p.qty * prices.get(s, p.avg_price) for s, p in self.positions.items()),
                   Decimal(0))

    def equity(self, prices: Mapping[str, Decimal]) -> Decimal:
        return self.cash + self.market_value(prices)

    def unrealized(self, prices: Mapping[str, Decimal]) -> Decimal:
        return sum(((prices.get(s, p.avg_price) - p.avg_price) * p.qty
                    for s, p in self.positions.items()), Decimal(0))

    def views(self, prices: Mapping[str, Decimal]) -> dict[str, PositionView]:
        return {s: PositionView(p.qty, p.qty * prices.get(s, p.avg_price), p.sector)
                for s, p in self.positions.items()}

    def sector_exposure(self, prices: Mapping[str, Decimal]) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for s, p in self.positions.items():
            out[p.sector] = out.get(p.sector, Decimal(0)) + p.qty * prices.get(s, p.avg_price)
        return out

    # --------------------------------------------------------------- rebuild
    @classmethod
    def rebuild(cls, starting_cash: Decimal, fills: Iterable[Fill],
                meta: Mapping[str, tuple[str, str]],
                stops: Mapping[str, tuple[Decimal | None, Decimal | None, Decimal | None,
                                          Decimal | None]]) -> Portfolio:
        """``meta``: order_id -> (strategy, sector).
        ``stops``: symbol -> (stop, target, initial_stop, highest_close)."""
        pf = cls(starting_cash)
        for f in fills:
            strategy, sector = meta.get(f.order_id, ("unknown", "Unknown"))
            pf.apply_fill(f, strategy, sector)
        for sym, (stop, target, initial, highest) in stops.items():
            p = pf.positions.get(sym)
            if p:
                p.stop, p.target, p.initial_stop = stop, target, initial
                p.highest_close = highest or p.highest_close
        return pf
