"""Simulated broker (docs/design.md §5).

It behaves like an exchange-connected broker: it keeps its own ledger
(cash, holdings, working orders) separate from the bot's portfolio so the
Reconciler has something real to compare against. Stop orders live here,
like exchange-side stops, and keep protecting positions even when the
engine is halted.

Pessimistic by default: market orders pay half-spread plus slippage plus a
size-based impact, stops that gap fill at the gap price, limit orders only
fill when price trades through, and nothing fills outside market data.
"""

from __future__ import annotations

import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

from algotrade.brokers.costs import CostModel
from algotrade.config.models import PaperBrokerConfig
from algotrade.core.clock import to_ist
from algotrade.core.types import (
    BrokerAck,
    BrokerPosition,
    Fill,
    Funds,
    Order,
    OrderType,
    Side,
)

TICK = Decimal("0.05")
_BPS = Decimal(10_000)


def round_to_tick(price: Decimal, up: bool) -> Decimal:
    steps = (price / TICK).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR)
    return steps * TICK


@dataclass
class WorkingOrder:
    broker_order_id: str
    client_order_id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    placed_at: datetime
    limit_price: Decimal | None = None
    trigger_price: Decimal | None = None
    filled: int = 0

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker_order_id": self.broker_order_id, "client_order_id": self.client_order_id,
            "symbol": self.symbol, "side": self.side.value, "qty": self.qty,
            "order_type": self.order_type.value, "placed_at": self.placed_at.isoformat(),
            "limit_price": str(self.limit_price) if self.limit_price is not None else None,
            "trigger_price": str(self.trigger_price) if self.trigger_price is not None else None,
            "filled": self.filled,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> WorkingOrder:
        return cls(
            broker_order_id=d["broker_order_id"], client_order_id=d["client_order_id"],
            symbol=d["symbol"], side=Side(d["side"]), qty=int(d["qty"]),
            order_type=OrderType(d["order_type"]),
            placed_at=datetime.fromisoformat(d["placed_at"]),
            limit_price=Decimal(d["limit_price"]) if d.get("limit_price") else None,
            trigger_price=Decimal(d["trigger_price"]) if d.get("trigger_price") else None,
            filled=int(d.get("filled", 0)),
        )


@dataclass
class _Holding:
    qty: int
    avg_price: Decimal


class PaperBroker:
    name = "paper"

    def __init__(self, cfg: PaperBrokerConfig, costs: CostModel, starting_cash: Decimal,
                 persist: Callable[[dict[str, Any]], None] | None = None,
                 state: dict[str, Any] | None = None) -> None:
        self.cfg = cfg
        self.costs = costs
        self._persist = persist
        self._rng = random.Random(cfg.seed)
        self.cash = starting_cash
        self.holdings: dict[str, _Holding] = {}
        self.working: dict[str, WorkingOrder] = {}
        self.by_client: dict[str, str] = {}
        self.last_price: dict[str, tuple[Decimal, datetime]] = {}
        self.adv_volume: dict[str, float] = {}
        self.dp_days: set[str] = set()  # "SYMBOL:YYYY-MM-DD" already charged DP
        self._fills: list[Fill] = []
        self.rejected_log: list[tuple[datetime, str]] = []
        if state:
            self._load(state)

    # --------------------------------------------------------- persistence
    def snapshot(self) -> dict[str, Any]:
        return {
            "cash": str(self.cash),
            "holdings": {s: {"qty": h.qty, "avg_price": str(h.avg_price)}
                         for s, h in self.holdings.items()},
            "working": [w.to_dict() for w in self.working.values()],
            "by_client": dict(self.by_client),
            "dp_days": sorted(self.dp_days),
        }

    def _load(self, st: dict[str, Any]) -> None:
        self.cash = Decimal(st["cash"])
        self.holdings = {s: _Holding(int(h["qty"]), Decimal(h["avg_price"]))
                         for s, h in st.get("holdings", {}).items()}
        self.working = {w["broker_order_id"]: WorkingOrder.from_dict(w)
                        for w in st.get("working", [])}
        self.by_client = dict(st.get("by_client", {}))
        self.dp_days = set(st.get("dp_days", []))

    def _save(self) -> None:
        if self._persist:
            self._persist(self.snapshot())

    # ------------------------------------------------------------ interface
    async def place(self, order: Order) -> BrokerAck:
        bid = f"P{uuid.uuid4().hex[:12]}"
        ref = self.last_price.get(order.symbol)
        reject = self._placement_reject(order, ref[0] if ref else None)
        if reject:
            self.rejected_log.append((order.created_at, reject))
            return BrokerAck(bid, False, reject)
        w = WorkingOrder(bid, order.id, order.symbol, order.side, order.qty, order.order_type,
                         order.updated_at, order.limit_price, order.trigger_price)
        self.working[bid] = w
        self.by_client[order.id] = bid
        self._save()
        return BrokerAck(bid, True, "accepted")

    def _placement_reject(self, order: Order, ref: Decimal | None) -> str:
        if order.qty <= 0:
            return "invalid quantity"
        if self.cfg.random_reject_prob and self._rng.random() < self.cfg.random_reject_prob:
            return "simulated broker reject"
        if order.side is Side.BUY:
            price = order.limit_price or ref
            if price is None:
                return "no market price"
            if price * order.qty * Decimal("1.003") > self.cash - self._reserved_cash():
                return "insufficient funds"
        else:
            held = self.holdings.get(order.symbol)
            committed = sum(w.remaining for w in self.working.values()
                            if w.symbol == order.symbol and w.side is Side.SELL)
            if held is None or held.qty - committed < order.qty:
                return "sell exceeds holdings (no short selling)"
        return ""

    def _reserved_cash(self) -> Decimal:
        total = Decimal(0)
        for w in self.working.values():
            if w.side is Side.BUY:
                px = w.limit_price or self.last_price.get(w.symbol, (Decimal(0), None))[0]
                total += px * w.remaining
        return total

    async def cancel(self, broker_order_id: str) -> bool:
        w = self.working.pop(broker_order_id, None)
        if w:
            self._save()
        return w is not None

    async def modify_trigger(self, broker_order_id: str, trigger_price: object) -> bool:
        w = self.working.get(broker_order_id)
        if w is None or w.order_type is not OrderType.STOP:
            return False
        w.trigger_price = Decimal(str(trigger_price))
        self._save()
        return True

    async def positions(self) -> list[BrokerPosition]:
        return [BrokerPosition(s, h.qty, h.avg_price) for s, h in self.holdings.items()
                if h.qty > 0]

    async def open_order_ids(self) -> dict[str, str]:
        return {w.client_order_id: bid for bid, w in self.working.items()}

    async def find_order(self, client_order_id: str) -> str | None:
        return self.by_client.get(client_order_id)

    async def funds(self) -> Funds:
        return Funds(self.cash)

    async def poll_fills(self) -> list[Fill]:
        out, self._fills = self._fills, []
        return out

    # ------------------------------------------------------- market events
    def on_market(self, symbol: str, ts: datetime, open_: Decimal, high: Decimal, low: Decimal,
                  close: Decimal, volume: int | None = None,
                  session_start: datetime | None = None) -> list[Fill]:
        """Process working orders for one symbol against a price event.

        For a tick pass o=h=l=c=LTP. ``session_start`` enables gap handling:
        a stop placed before it that the open gapped through fills at the open.
        """
        self.last_price[symbol] = (close, ts)
        fills: list[Fill] = []
        for bid in [b for b, w in self.working.items() if w.symbol == symbol]:
            w = self.working[bid]
            if w.placed_at > ts:
                continue
            f = self._try_fill(w, ts, open_, high, low, volume, session_start)
            if f:
                fills.append(f)
                if w.remaining <= 0:
                    self.working.pop(bid, None)
        if fills:
            self._fills.extend(fills)
            self._save()
        return fills

    def _impact_bps(self, w: WorkingOrder, price: Decimal) -> Decimal:
        adv = self.adv_volume.get(w.symbol)
        if not adv:
            return Decimal(0)
        pct_adv = Decimal(w.remaining) / Decimal(str(adv)) * 100
        return pct_adv * Decimal(str(self.cfg.impact_bps_per_pct_adv))

    def _try_fill(self, w: WorkingOrder, ts: datetime, open_: Decimal, high: Decimal,
                  low: Decimal, volume: int | None, session_start: datetime | None) -> Fill | None:
        base: Decimal | None = None
        slip_bps = Decimal(str(self.cfg.spread_bps + self.cfg.base_slippage_bps))
        gapped = session_start is not None and w.placed_at < session_start
        if w.order_type is OrderType.MARKET:
            base = open_
        elif w.order_type is OrderType.LIMIT and w.limit_price is not None:
            lp = w.limit_price
            if w.side is Side.BUY and low < lp:
                base = min(lp, open_) if gapped else lp
            elif w.side is Side.SELL and high > lp:
                base = max(lp, open_) if gapped else lp
            slip_bps = Decimal(0)  # limit orders fill at the limit or better
        elif w.order_type is OrderType.STOP and w.trigger_price is not None:
            tp = w.trigger_price
            if w.side is Side.SELL and low <= tp:
                base = open_ if (gapped and open_ <= tp) else min(tp, high)
            elif w.side is Side.BUY and high >= tp:
                base = open_ if (gapped and open_ >= tp) else max(tp, low)
        if base is None:
            return None

        qty = w.remaining
        if volume and volume > 0:
            cap = int(volume * self.cfg.max_fill_fraction_of_bar_volume)
            qty = max(1, min(qty, cap))
        slip = (slip_bps + self._impact_bps(w, base)) / _BPS
        if w.side is Side.BUY:
            price = round_to_tick(base * (1 + slip), up=True)
        else:
            price = round_to_tick(base * (1 - slip), up=False)

        if w.side is Side.BUY:
            charges = self.costs.charges(Side.BUY, qty, price)
            need = price * qty + charges.total
            if need > self.cash:
                # Funds disappeared (e.g. price ran away): cancel, do not overdraw.
                self.working.pop(w.broker_order_id, None)
                self.rejected_log.append((ts, "insufficient funds at fill"))
                return None
            self.cash -= need
            h = self.holdings.get(w.symbol)
            if h is None or h.qty == 0:
                self.holdings[w.symbol] = _Holding(qty, price)
            else:
                total = h.avg_price * h.qty + price * qty
                h.qty += qty
                h.avg_price = total / h.qty
        else:
            day_key = f"{w.symbol}:{to_ist(ts).date().isoformat()}"
            charges = self.costs.charges(Side.SELL, qty, price,
                                         dp_applies=day_key not in self.dp_days)
            self.dp_days.add(day_key)
            self.cash += price * qty - charges.total
            h = self.holdings[w.symbol]
            h.qty -= qty
            if h.qty == 0:
                del self.holdings[w.symbol]
        w.filled += qty
        return Fill(w.client_order_id, w.broker_order_id, w.symbol, w.side, ts, qty, price,
                    charges)
