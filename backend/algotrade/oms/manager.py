"""Order Manager (OMS): lifecycle, idempotency, protective stops.

Orders only exist after a Risk Engine decision (rule I-01). Placement is
never retried blindly: an uncertain outcome is resolved by asking the
broker (rule I-15).
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Any

from algotrade.audit.logger import AuditLog
from algotrade.brokers.base import Broker, BrokerError
from algotrade.core.types import (
    Fill,
    Mode,
    Order,
    OrderIntent,
    OrderState,
    OrderType,
    Side,
)
from algotrade.db.repository import Repository
from algotrade.oms.state_machine import TERMINAL, WORKING, check_transition
from algotrade.risk.engine import RiskDecision


def idempotency_key(strategy: str, symbol: str, side: Side, decision_id: str,
                    bar_ts: datetime) -> str:
    raw = f"{strategy}|{symbol}|{side.value}|{decision_id}|{bar_ts.isoformat()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:40]


class OrderManager:
    def __init__(self, broker: Broker, mode: Mode, repo: Repository | None = None,
                 audit: AuditLog | None = None,
                 config_version: Callable[[], int] = lambda: 0) -> None:
        self.broker = broker
        self.mode = mode
        self.repo = repo
        self.audit = audit
        self.config_version = config_version
        self.orders: dict[str, Order] = {}
        self._keys: set[str] = set()
        self.broker_errors = 0
        self.broker_rejects: list[datetime] = []

    # ------------------------------------------------------------ plumbing
    def _persist(self, o: Order) -> None:
        if self.repo:
            self.repo.save_order(o, self.config_version())

    def _audit(self, ts: datetime, event: str, payload: dict[str, Any], cid: str) -> None:
        if self.audit:
            self.audit.record(ts, "oms", event, payload, correlation_id=cid,
                              config_version=self.config_version())

    def transition(self, o: Order, to: OrderState, now: datetime, **detail: Any) -> None:
        check_transition(o.state, to)
        frm = o.state
        o.state = to
        o.updated_at = now
        self._persist(o)
        if self.repo:
            self.repo.add_order_event(o.id, now, frm, to, detail)
        self._audit(now, f"order.{to.value.lower()}",
                    {"order_id": o.id, "symbol": o.symbol, "side": o.side.value, "qty": o.qty,
                     "from": frm.value, **detail}, o.decision_id or o.id)

    def is_duplicate(self, key: str) -> bool:
        if key in self._keys:
            return True
        return bool(self.repo and self.repo.idempotency_key_exists(key))

    def working_orders(self, symbol: str | None = None) -> list[Order]:
        return [o for o in self.orders.values() if o.state in WORKING
                and (symbol is None or o.symbol == symbol)]

    def protective_stop(self, symbol: str) -> Order | None:
        for o in self.working_orders(symbol):
            if o.purpose == "protective_stop":
                return o
        return None

    def pending_entry_value(self, prices: dict[str, Decimal]) -> Decimal:
        total = Decimal(0)
        for o in self.working_orders():
            if o.side is Side.BUY:
                px = o.limit_price or prices.get(o.symbol) or Decimal(0)
                total += px * o.remaining_qty
        return total

    # ------------------------------------------------------------- creation
    def create(self, intent: OrderIntent, decision: RiskDecision, now: datetime) -> Order:
        key = idempotency_key(intent.strategy, intent.symbol, intent.side, intent.decision_id,
                              intent.bar_ts)
        o = Order(
            id=f"O{uuid.uuid4().hex[:16]}", idempotency_key=key, mode=self.mode,
            symbol=intent.symbol, side=intent.side, qty=max(decision.qty, 0),
            order_type=OrderType.MARKET, state=OrderState.CREATED, created_at=now,
            updated_at=now, purpose="entry" if intent.side is Side.BUY else "exit",
            decision_id=intent.decision_id, strategy=intent.strategy, stop=intent.stop,
            target=intent.target, reason=intent.reason,
        )
        if self.is_duplicate(key):
            # Never persisted twice: the unique key would collide. Record and drop.
            o.state = OrderState.REJECTED
            o.reason = "duplicate intent"
            self._audit(now, "order.duplicate_dropped", {"symbol": o.symbol, "key": key},
                        intent.decision_id)
            return o
        self._keys.add(key)
        self.orders[o.id] = o
        self._persist(o)
        self._audit(now, "order.created", {"order_id": o.id, "symbol": o.symbol,
                                           "side": o.side.value, "qty": o.qty,
                                           "reason": o.reason}, intent.decision_id)
        if decision.approved and decision.qty > 0:
            self.transition(o, OrderState.RISK_APPROVED, now, risk=decision.summary())
        else:
            o.reason = decision.summary()
            self.transition(o, OrderState.REJECTED, now, risk=decision.summary())
        return o

    # ----------------------------------------------------------- submission
    async def submit(self, o: Order, now: datetime) -> Order:
        if o.state is not OrderState.RISK_APPROVED:
            raise RuntimeError(f"refusing to submit order in state {o.state.value}")
        o.updated_at = now
        self.transition(o, OrderState.SUBMITTED, now)
        try:
            ack = await self.broker.place(o)
        except BrokerError as exc:
            self.broker_errors += 1
            # Outcome unknown: ask the broker instead of resending.
            found = await self.broker.find_order(o.id)
            if found:
                o.broker_order_id = found
                self.transition(o, OrderState.ACKED, now, note="adopted after uncertain place",
                                error=str(exc))
            else:
                self.transition(o, OrderState.FAILED, now, error=str(exc))
            return o
        self.broker_errors = 0
        o.broker_order_id = ack.broker_order_id
        if ack.accepted:
            self.transition(o, OrderState.ACKED, now, broker_order_id=ack.broker_order_id)
        else:
            self.broker_rejects.append(now)
            o.reason = ack.message
            self.transition(o, OrderState.FAILED, now, broker_reject=ack.message)
        return o

    async def place_protective_stop(self, symbol: str, qty: int, trigger: Decimal,
                                    parent: Order, now: datetime) -> Order | None:
        """Stop for an approved, filled entry. It reduces risk, so it rides on
        the entry's risk approval instead of a new one."""
        existing = self.protective_stop(symbol)
        if existing and existing.qty == qty:
            return existing
        if existing:
            await self.cancel(existing, now, reason="resize protective stop")
        seq = sum(1 for o in self.orders.values() if o.parent_order_id == parent.id)
        key = hashlib.sha256(f"stop|{parent.id}|{seq}".encode()).hexdigest()[:40]
        o = Order(id=f"O{uuid.uuid4().hex[:16]}", idempotency_key=key, mode=self.mode,
                  symbol=symbol, side=Side.SELL, qty=qty, order_type=OrderType.STOP,
                  state=OrderState.CREATED, created_at=now, updated_at=now,
                  purpose="protective_stop", decision_id=parent.decision_id,
                  strategy=parent.strategy, trigger_price=trigger, stop=trigger,
                  reason=f"protective stop for {parent.id}", parent_order_id=parent.id)
        self._keys.add(key)
        self.orders[o.id] = o
        self._persist(o)
        self.transition(o, OrderState.RISK_APPROVED, now, note="covered by entry approval",
                        parent=parent.id)
        return await self.submit(o, now)

    async def cancel(self, o: Order, now: datetime, reason: str = "") -> bool:
        if o.state in TERMINAL:
            return False
        if o.state is OrderState.SUBMITTED:
            return False  # wait for the ack; never cancel blind
        self.transition(o, OrderState.CANCEL_REQ, now, reason=reason)
        ok = bool(o.broker_order_id) and await self.broker.cancel(o.broker_order_id or "")
        if ok:
            self.transition(o, OrderState.CANCELLED, now)
        else:
            # Not working at the broker any more: it either filled or expired.
            self.transition(o, OrderState.EXPIRED, now, note="not open at broker")
        return ok

    async def modify_stop(self, o: Order, trigger: Decimal, now: datetime) -> bool:
        if o.state not in (OrderState.ACKED, OrderState.PARTIALLY_FILLED):
            return False
        ok = await self.broker.modify_trigger(o.broker_order_id or "", trigger)
        if ok:
            old = o.trigger_price
            o.trigger_price = trigger
            o.stop = trigger
            o.updated_at = now
            self._persist(o)
            self._audit(now, "order.stop_moved", {"order_id": o.id, "symbol": o.symbol,
                                                  "from": old, "to": trigger}, o.id)
        return ok

    # ---------------------------------------------------------------- fills
    def on_fill(self, f: Fill, now: datetime) -> Order | None:
        o = self.orders.get(f.order_id)
        if o is None:
            return None
        prev_value = (o.avg_fill_price or Decimal(0)) * o.filled_qty
        o.filled_qty += f.qty
        o.avg_fill_price = (prev_value + f.price * f.qty) / o.filled_qty
        to = OrderState.FILLED if o.filled_qty >= o.qty else OrderState.PARTIALLY_FILLED
        self.transition(o, to, now, fill_qty=f.qty, fill_price=f.price,
                        charges=f.charges.total)
        if self.repo:
            self.repo.save_fill(f)
        return o

    def restore(self, orders: list[Order]) -> None:
        for o in orders:
            self.orders[o.id] = o
            self._keys.add(o.idempotency_key)
