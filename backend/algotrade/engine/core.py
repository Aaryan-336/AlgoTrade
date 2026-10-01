"""TradingEngine: one decision cycle end to end (docs/architecture.md §5).

The same engine runs live paper trading and backtests; only the data feed,
the clock and persistence differ. Flow:

    bars -> features -> strategies -> Decision Engine -> intents
    intents -> Risk Engine -> Order Manager -> Broker -> fills -> Portfolio

Everything that changes state is audited. Any unexpected exception in the
trading path trips the ``unhandled_exception`` breaker (fail closed).
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from algotrade.audit.logger import AuditLog
from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig
from algotrade.core.clock import MarketCalendar, to_ist
from algotrade.core.types import (
    Bar,
    Fill,
    IntentKind,
    Mode,
    Order,
    OrderIntent,
    OrderState,
    OrderType,
    SentimentAggregate,
    Side,
    Tick,
)
from algotrade.data.universe import Universe
from algotrade.data.validators import QuarantineMonitor, clean_series, validate_tick
from algotrade.db.repository import Repository
from algotrade.decision.engine import DecisionEngine, expiry_for
from algotrade.engine.alerts import Notifier
from algotrade.features.indicators import FeatureSet, correlation
from algotrade.news.aggregator import aggregate
from algotrade.oms.manager import OrderManager
from algotrade.oms.state_machine import WORKING
from algotrade.portfolio.service import ClosedTrade, Portfolio
from algotrade.risk.breakers import BreakerBoard, evaluate_loss_breakers
from algotrade.risk.engine import RiskContext, RiskDecision, RiskEngine
from algotrade.strategies.registry import build_strategies

MAX_TICK_JUMP_PCT = 15.0
MAX_BAR_JUMP_PCT = 25.0
HISTORY_KEEP = 600
FEATURE_BARS = 260  # bars fed to strategies each cycle (covers every default lookback)


@dataclass(slots=True)
class KillSwitch:
    active: bool = False
    action: str = "block"
    reason: str = ""
    ts: datetime | None = None

    def to_state(self) -> dict[str, Any]:
        return {"active": self.active, "action": self.action, "reason": self.reason,
                "ts": self.ts.isoformat() if self.ts else None}

    @classmethod
    def from_state(cls, st: dict[str, Any] | None) -> KillSwitch:
        if not st:
            return cls()
        ts = st.get("ts")
        return cls(bool(st.get("active")), str(st.get("action", "block")),
                   str(st.get("reason", "")), datetime.fromisoformat(ts) if ts else None)


@dataclass(slots=True)
class Marks:
    session_date: str = ""
    day_start: Decimal = Decimal(0)
    week_start: Decimal = Decimal(0)
    week_id: str = ""
    peak: Decimal = Decimal(0)

    def to_state(self) -> dict[str, Any]:
        return {"session_date": self.session_date, "day_start": str(self.day_start),
                "week_start": str(self.week_start), "week_id": self.week_id,
                "peak": str(self.peak)}

    @classmethod
    def from_state(cls, st: dict[str, Any] | None) -> Marks:
        if not st:
            return cls()
        return cls(st.get("session_date", ""), Decimal(st.get("day_start", "0")),
                   Decimal(st.get("week_start", "0")), st.get("week_id", ""),
                   Decimal(st.get("peak", "0")))


class TradingEngine:
    def __init__(self, cfg: AppConfig, config_version: int, universe: Universe,
                 calendar: MarketCalendar, broker: PaperBroker, repo: Repository | None = None,
                 audit: AuditLog | None = None, notifier: Notifier | None = None,
                 mode: Mode = Mode.PAPER) -> None:
        if mode is not Mode.PAPER:
            raise ValueError("only PAPER mode is implemented")
        self.cfg = cfg
        self.config_version = config_version
        self.universe = universe
        self.calendar = calendar
        self.broker = broker
        self.repo = repo
        self.audit = audit
        self.notifier = notifier or Notifier()
        self.mode = mode
        self.costs = CostModel(cfg.strategy.costs)
        self.risk = RiskEngine(cfg.risk, self.costs)
        self.oms = OrderManager(broker, mode, repo, audit, lambda: self.config_version)
        self.decision = DecisionEngine(cfg.strategy, build_strategies(cfg.strategy.strategies),
                                       calendar, cfg.risk.max_open_positions)
        self.portfolio = Portfolio(cfg.strategy.capital)
        self.breakers = BreakerBoard()
        self.kill = KillSwitch()
        self.marks = Marks()
        self.prices: dict[str, Decimal] = {}
        self.price_ts: dict[str, datetime] = {}
        self.prev_close: dict[str, Decimal] = {}
        self.daily: dict[str, list[Bar]] = {}
        self.intraday: dict[str, list[Bar]] = {}
        self.pending: list[OrderIntent] = []
        self.sentiment: dict[str, SentimentAggregate] = {}
        self.regime: dict[str, Any] = {}
        self.quarantine = QuarantineMonitor()
        self.events: deque[dict[str, Any]] = deque(maxlen=300)
        self.last_cycle: datetime | None = None
        self.last_bar_ts: datetime | None = None
        self.trades: list[ClosedTrade] = []

    # ============================================================ utilities
    def _audit(self, now: datetime, event: str, payload: dict[str, Any],
               cid: str | None = None) -> None:
        self.events.appendleft({"ts": now.isoformat(), "event": event, **payload})
        if self.audit:
            self.audit.record(now, "engine", event, payload, correlation_id=cid,
                              config_version=self.config_version)

    def _risk_event(self, now: datetime, name: str, result: str, details: dict[str, Any],
                    decision_id: str | None = None, symbol: str | None = None) -> None:
        if self.repo:
            self.repo.add_risk_event(now, name, result, details, decision_id, symbol)

    def _save_state(self, now: datetime) -> None:
        if not self.repo:
            return
        self.repo.set_state("breakers", self.breakers.to_state(), now)
        self.repo.set_state("marks", self.marks.to_state(), now)
        self.repo.set_state("engine_meta", {"last_cycle": self.last_cycle,
                                            "last_bar_ts": self.last_bar_ts}, now)
        self.repo.set_state("pending_intents", {"items": [self._intent_state(i)
                                                          for i in self.pending]}, now)

    @staticmethod
    def _intent_state(i: OrderIntent) -> dict[str, Any]:
        return {"decision_id": i.decision_id, "symbol": i.symbol, "side": i.side.value,
                "kind": i.kind.value, "strategy": i.strategy, "reason": i.reason,
                "ref_price": str(i.ref_price), "bar_ts": i.bar_ts.isoformat(),
                "created_at": i.created_at.isoformat(), "expires_at": i.expires_at.isoformat(),
                "stop": str(i.stop) if i.stop is not None else None,
                "target": str(i.target) if i.target is not None else None,
                "score": i.score, "size_multiplier": i.size_multiplier}

    @staticmethod
    def _intent_from_state(d: dict[str, Any]) -> OrderIntent:
        return OrderIntent(
            d["decision_id"], d["symbol"], Side(d["side"]), IntentKind(d["kind"]), d["strategy"],
            d["reason"], Decimal(d["ref_price"]), datetime.fromisoformat(d["bar_ts"]),
            datetime.fromisoformat(d["created_at"]), datetime.fromisoformat(d["expires_at"]),
            Decimal(d["stop"]) if d.get("stop") else None,
            Decimal(d["target"]) if d.get("target") else None,
            float(d.get("score", 0)), float(d.get("size_multiplier", 1)))

    def equity(self) -> Decimal:
        return self.portfolio.equity(self.prices)

    # ============================================================== restore
    def restore(self, now: datetime) -> None:
        """Rebuild state from the database after a restart (NFR-05)."""
        if not self.repo:
            return
        from algotrade.core.types import Charges

        rows = self.repo.orders(limit=100_000)
        orders: list[Order] = []
        meta: dict[str, tuple[str, str]] = {}
        for r in rows:
            o = Order(id=r.id, idempotency_key=r.idempotency_key, mode=Mode(r.mode),
                      symbol=r.symbol, side=Side(r.side), qty=r.qty,
                      order_type=OrderType(r.order_type), state=OrderState(r.state),
                      created_at=r.created_at, updated_at=r.updated_at, purpose=r.purpose,
                      decision_id=r.decision_id, strategy=r.strategy, limit_price=r.limit_price,
                      trigger_price=r.trigger_price, stop=r.stop, target=r.target,
                      broker_order_id=r.broker_order_id, filled_qty=r.filled_qty,
                      avg_fill_price=r.avg_fill_price, reason=r.reason,
                      parent_order_id=r.parent_order_id)
            orders.append(o)
            meta[o.id] = (o.strategy or "unknown", self.universe.sector(o.symbol))
        self.oms.restore(orders)
        fills = [Fill(f.order_id, f.broker_order_id, f.symbol, Side(f.side), f.ts, f.qty,
                      f.price, Charges(**{k: Decimal(v) for k, v in f.charges.items()
                                          if k != "total"}))
                 for f in self.repo.fills(limit=1_000_000)]
        stops = {p.symbol: (p.stop, p.target, p.initial_stop, p.highest_close)
                 for p in self.repo.positions()}
        self.portfolio = Portfolio.rebuild(self.cfg.strategy.capital, fills, meta, stops)
        self.breakers = BreakerBoard.from_state(self.repo.get_state("breakers"))
        self.kill = KillSwitch.from_state(self.repo.get_state("kill_switch"))
        self.marks = Marks.from_state(self.repo.get_state("marks"))
        meta = self.repo.get_state("engine_meta") or {}
        if meta.get("last_cycle"):
            self.last_cycle = datetime.fromisoformat(str(meta["last_cycle"]))
        if meta.get("last_bar_ts"):
            self.last_bar_ts = datetime.fromisoformat(str(meta["last_bar_ts"]))
        # Display prices from the last stored bars. Their timestamps are old, so
        # the freshness check still blocks orders until live prices arrive.
        for sym in {*self.portfolio.positions, *self.universe.symbols}:
            last = self.repo.bars(sym, "1d", 1)
            if last:
                self.prices.setdefault(sym, last[-1].close)
                self.price_ts.setdefault(sym, last[-1].ts)
        pend = self.repo.get_state("pending_intents") or {}
        self.pending = [self._intent_from_state(d) for d in pend.get("items", [])]
        self._audit(now, "engine.restored", {
            "positions": len(self.portfolio.positions), "orders": len(orders),
            "working": len(self.oms.working_orders()), "pending_intents": len(self.pending),
            "breakers": list(self.breakers.tripped)})

    # ================================================================ data
    def load_daily_history(self, symbol: str, bars: Iterable[Bar], now: datetime) -> int:
        clean, issues = clean_series(sorted(bars, key=lambda b: b.ts), now, MAX_BAR_JUMP_PCT)
        if issues:
            self.quarantine.add(now, len(issues))
            self._risk_event(now, "data_validation", "INFO",
                             {"symbol": symbol, "issues": [i.kind for i in issues][:20]},
                             symbol=symbol)
        self.daily[symbol] = clean[-HISTORY_KEEP:]
        if clean:
            last = clean[-1]
            # Previous close for the circuit-band check: the last completed day.
            self.prev_close[symbol] = last.close
            if self.repo:
                self.repo.upsert_bars(clean[-5:])
        return len(clean)

    def append_bar(self, bar: Bar, now: datetime) -> bool:
        series = self.daily if bar.timeframe == "1d" else self.intraday
        hist = series.setdefault(bar.symbol, [])
        if hist and bar.ts <= hist[-1].ts:
            if bar.ts == hist[-1].ts:
                hist[-1] = bar  # corrected bar from the provider
                return True
            return False
        _, issues = clean_series(([hist[-1]] if hist else []) + [bar], now, MAX_BAR_JUMP_PCT)
        if issues:
            self.quarantine.add(now, len(issues))
            self._risk_event(now, "data_validation", "REJECT",
                             {"symbol": bar.symbol, "issues": [i.kind for i in issues]},
                             symbol=bar.symbol)
            return False
        hist.append(bar)
        del hist[:-HISTORY_KEEP]
        if bar.timeframe == "1d":
            self.prev_close[bar.symbol] = bar.close
        if self.repo:
            self.repo.upsert_bars([bar])
        return True

    def on_tick(self, tick: Tick, now: datetime) -> list[Fill]:
        issues = validate_tick(tick, self.prices.get(tick.symbol), now, MAX_TICK_JUMP_PCT)
        if issues:
            self.quarantine.add(now)
            self._risk_event(now, "tick_quarantined", "REJECT",
                             {"issues": [i.kind for i in issues], "ltp": tick.ltp},
                             symbol=tick.symbol)
            return []
        self.prices[tick.symbol] = tick.ltp
        self.price_ts[tick.symbol] = tick.ts
        return self.broker.on_market(tick.symbol, tick.ts, tick.ltp, tick.ltp, tick.ltp,
                                     tick.ltp)

    def set_price(self, symbol: str, price: Decimal, ts: datetime) -> None:
        self.prices[symbol] = price
        self.price_ts[symbol] = ts

    # =============================================================== fills
    async def process_fills(self, now: datetime) -> list[Fill]:
        fills = await self.broker.poll_fills()
        for f in fills:
            o = self.oms.on_fill(f, now)
            if o is None:
                self._risk_event(now, "unknown_fill", "BREAKER", {"order_id": f.order_id},
                                 symbol=f.symbol)
                self.trip("reconciliation", f"fill for unknown order {f.order_id}", now)
                continue
            sector = self.universe.sector(f.symbol)
            trade = self.portfolio.apply_fill(f, o.strategy or "unknown", sector, o.stop,
                                              o.target)
            payload = {"symbol": f.symbol, "side": f.side.value, "qty": f.qty,
                       "price": f.price, "charges": f.charges.total, "purpose": o.purpose}
            self._audit(now, "fill", payload, o.decision_id)
            if trade:
                self.trades.append(trade)
                self.notifier.send(now, "info", f"Closed {trade.symbol} x{trade.qty} "
                                                f"P&L ₹{trade.pnl:.2f}")
            if o.purpose == "entry":
                pos = self.portfolio.positions.get(f.symbol)
                if pos and o.stop is not None:
                    await self.oms.place_protective_stop(f.symbol, pos.qty, o.stop, o, now)
                self.notifier.send(now, "info", f"Bought {f.symbol} x{f.qty} @ ₹{f.price}")
            elif (o.purpose == "exit" and f.symbol in self.portfolio.positions
                  and o.state is OrderState.FILLED):
                # Partial exit left shares behind: put protection back on them.
                pos = self.portfolio.positions[f.symbol]
                parent = self._entry_parent(f.symbol)
                if pos.stop is not None and parent is not None:
                    await self.oms.place_protective_stop(f.symbol, pos.qty, pos.stop, parent,
                                                         now)
            elif f.symbol not in self.portfolio.positions:
                # Position closed (exit or stop): cancel whatever still protects it.
                for w in self.oms.working_orders(f.symbol):
                    if w.purpose == "protective_stop":
                        await self.oms.cancel(w, now, reason="position closed")
                if o.purpose == "protective_stop":
                    self.notifier.send(now, "warning", f"Stop hit on {f.symbol} @ ₹{f.price}")
        if fills and self.repo:
            self.repo.replace_positions(self.portfolio.positions.values())
        return fills

    # ======================================================= decision cycle
    def _features(self, bar_ts: datetime) -> dict[str, FeatureSet]:
        """Only symbols whose latest bar is the bar being decided on: a symbol
        with a missing bar is skipped rather than traded on old data."""
        src = self.daily if self.cfg.strategy.timeframe == "1d" else self.intraday
        out: dict[str, FeatureSet] = {}
        for s in self.universe.symbols:
            bars = src.get(s, [])
            if bars and bars[-1].ts == bar_ts:
                out[s] = FeatureSet(bars[-FEATURE_BARS:])
        return out

    def _load_sentiment(self, now: datetime) -> None:
        sc = self.cfg.strategy.sentiment
        if not sc.enabled or not self.repo:
            self.sentiment = {}
            return
        items = self.repo.sentiment_items(now - timedelta(days=sc.lookback_days))
        out: dict[str, SentimentAggregate] = {}
        for sym in self.universe.symbols:
            bars = self.daily.get(sym, [])

            def move_since(ts: datetime, bars: list[Bar] = bars) -> float | None:
                before = [b for b in bars if b.ts <= ts]
                if not before or not bars:
                    return None
                ref = before[-1].close
                return float((bars[-1].close - ref) / ref * 100) if ref else None

            out[sym] = aggregate(sym, [i for i in items if i.symbol == sym], now, sc, move_since)
        self.sentiment = out

    async def decision_cycle(self, now: datetime, bar_ts: datetime) -> int:
        """Run strategies on the latest completed bars and queue intents."""
        self._load_sentiment(now)
        idx_sym = self.cfg.strategy.regime.index_symbol
        vix_bars = self.daily.get(self.cfg.strategy.regime.vix_symbol)
        index_fs = FeatureSet(self.daily[idx_sym]) if self.daily.get(idx_sym) else None
        expires = expiry_for(bar_ts, self.cfg.strategy.timeframe,
                             self.cfg.strategy.decision.intent_ttl_bars, self.calendar)
        res = self.decision.run(now, bar_ts, self._features(bar_ts), self.portfolio.positions,
                                self.sentiment, index_fs,
                                float(vix_bars[-1].close) if vix_bars else None, expires)
        self.regime = res.regime
        if self.repo:
            self.repo.save_signals(now, bar_ts, res.signals, self.config_version)
            for d in res.decisions:
                self.repo.save_decision(d.decision_id, now, d.symbol, d.kind, d.score,
                                        d.components, d.outcome, d.reason, self.config_version)
        # Trailing stops: move the broker-side stop up, never down.
        for sym, new_stop in res.stop_updates.items():
            pos = self.portfolio.positions.get(sym)
            stop_order = self.oms.protective_stop(sym)
            if (pos and stop_order and (pos.stop is None or new_stop > pos.stop)
                    and await self.oms.modify_stop(stop_order, new_stop, now)):
                pos.stop = new_stop
        for sym, hc in res.highest_close.items():
            if sym in self.portfolio.positions:
                self.portfolio.positions[sym].highest_close = hc
        # New intents replace older pending ones for the same symbol and side.
        keys = {(i.symbol, i.side) for i in res.intents}
        self.pending = [p for p in self.pending if (p.symbol, p.side) not in keys]
        self.pending.extend(res.intents)
        self.last_cycle, self.last_bar_ts = now, bar_ts
        if self.repo:
            self.repo.replace_positions(self.portfolio.positions.values())
        self._save_state(now)
        self._audit(now, "decision.cycle", {
            "bar_ts": bar_ts, "intents": [f"{i.side.value} {i.symbol}" for i in res.intents],
            "signals": len(res.signals), "regime": res.regime})
        return len(res.intents)

    # ============================================================ execution
    def _orders_since(self, since: datetime) -> int:
        return sum(1 for o in self.oms.orders.values()
                   if o.created_at >= since and o.purpose != "protective_stop"
                   and o.state is not OrderState.REJECTED)

    def _fresh(self, symbol: str, now: datetime) -> bool:
        ts = self.price_ts.get(symbol)
        return ts is not None and (now - ts).total_seconds() <= self.cfg.risk.max_data_staleness_sec

    def _max_corr(self, symbol: str) -> float:
        rets = FeatureSet(self.daily.get(symbol, [])[-61:]).returns()
        best = 0.0
        for held in self.portfolio.positions:
            other = FeatureSet(self.daily.get(held, [])[-61:]).returns()
            best = max(best, abs(correlation(rets, other)))
        return best

    def risk_context(self, intent: OrderIntent, now: datetime) -> RiskContext:
        rc = self.cfg.risk
        day_start = self.calendar.session_bounds(to_ist(now).date())[0]
        fs = FeatureSet(self.daily.get(intent.symbol, []))
        today = to_ist(now).date()
        blackout = (today in self.cfg.strategy.regime.event_blackout_dates
                    or today in self.cfg.strategy.regime.symbol_blackouts.get(intent.symbol, []))
        inst = self.universe.get(intent.symbol)
        banned = intent.symbol in {s.upper() for s in self.cfg.strategy.universe.ban_list}
        return RiskContext(
            now=now, mode=self.mode, kill_switch_active=self.kill.active,
            halted=self.breakers.halted,
            entry_blocks=tuple(self.breakers.tripped),
            market_open=self.calendar.is_open(now),
            in_entry_window=self.calendar.in_entry_window(now, rc.no_entry_first_minutes,
                                                          rc.no_entry_last_minutes),
            last_price=self.prices.get(intent.symbol),
            last_price_ts=self.price_ts.get(intent.symbol),
            prev_close=self.prev_close.get(intent.symbol),
            tradable=inst is not None and inst.is_active and not banned,
            sector=self.universe.sector(intent.symbol),
            equity=self.equity(), cash=self.portfolio.cash,
            reserved_cash=self.oms.pending_entry_value(self.prices),
            positions=self.portfolio.views(self.prices),
            orders_last_minute=self._orders_since(now - timedelta(minutes=1)),
            orders_today=self._orders_since(day_start),
            idempotency_key_seen=False,  # the OMS enforces this atomically on create
            avg_traded_value=fs.avg_traded_value(20),
            max_correlation=self._max_corr(intent.symbol) if intent.kind is IntentKind.ENTRY
            else 0.0,
            blackout=blackout,
            pending_entries=sum(1 for o in self.oms.working_orders()
                                if o.purpose == "entry"),
            flatten_requested=self.kill.active and self.kill.action == "flatten",
        )

    def _ready(self, intent: OrderIntent, now: datetime) -> bool:
        """Transient conditions keep an intent waiting instead of rejecting it."""
        rc = self.cfg.risk
        if not self.calendar.is_open(now) or not self._fresh(intent.symbol, now):
            return False
        if intent.kind is IntentKind.ENTRY and not self.calendar.in_entry_window(
                now, rc.no_entry_first_minutes, rc.no_entry_last_minutes):
            return False
        if self._orders_since(now - timedelta(minutes=1)) >= rc.max_orders_per_minute:
            return False
        if intent.kind is IntentKind.EXIT:
            # Wait while an earlier exit for this symbol is still working.
            if any(o.purpose == "exit" for o in self.oms.working_orders(intent.symbol)):
                return False
        else:
            # Entries wait for exits (e.g. rotation) to free cash and slots.
            if any(o.purpose == "exit" for o in self.oms.working_orders()):
                return False
            if any(p.kind is IntentKind.EXIT for p in self.pending):
                return False
        return True

    async def execute_pending(self, now: datetime) -> list[Order]:
        placed: list[Order] = []
        if self.kill.active and self.kill.action == "flatten":
            self._queue_flatten(now)
        self.pending.sort(key=lambda i: (i.kind is IntentKind.ENTRY, -i.score))
        keep: list[OrderIntent] = []
        changed = False
        for intent in list(self.pending):
            if now >= intent.expires_at:
                changed = True
                if self.repo:
                    self.repo.save_decision(intent.decision_id, now, intent.symbol,
                                            intent.kind.value.lower(), intent.score, {},
                                            "expired", "intent expired before execution",
                                            self.config_version)
                continue
            if intent.kind is IntentKind.EXIT and intent.symbol not in self.portfolio.positions:
                changed = True
                continue
            if not self._ready(intent, now):
                keep.append(intent)
                continue
            changed = True
            order = await self._execute(intent, now)
            if order is not None:
                placed.append(order)
        self.pending = [i for i in self.pending if i in keep]
        if changed:
            self._save_state(now)
        return placed

    async def _execute(self, intent: OrderIntent, now: datetime) -> Order | None:
        ctx = self.risk_context(intent, now)
        decision: RiskDecision = self.risk.evaluate(intent, ctx)
        order = self.oms.create(intent, decision, now)
        if order.state is OrderState.REJECTED:
            for c in decision.failures:
                self._risk_event(now, c.name, "REJECT", {"detail": c.detail,
                                                         "side": intent.side.value},
                                 intent.decision_id, intent.symbol)
            if self.repo:
                self.repo.save_decision(intent.decision_id, now, intent.symbol,
                                        intent.kind.value.lower(), intent.score, {},
                                        "risk_rejected", order.reason, self.config_version)
            self._audit(now, "risk.rejected", {"symbol": intent.symbol,
                                               "side": intent.side.value,
                                               "reasons": order.reason}, intent.decision_id)
            return order
        if intent.kind is IntentKind.EXIT:
            stop = self.oms.protective_stop(intent.symbol)
            if stop:
                await self.oms.cancel(stop, now, reason="engine exit")
        await self.oms.submit(order, now)
        outcome = "submitted" if order.state is OrderState.ACKED else order.state.value.lower()
        if self.repo:
            self.repo.save_decision(intent.decision_id, now, intent.symbol,
                                    intent.kind.value.lower(), intent.score, {}, outcome,
                                    order.reason if order.state is OrderState.FAILED else "",
                                    self.config_version)
        if order.state is OrderState.FAILED and intent.kind is IntentKind.EXIT:
            # Exit failed: restore protection immediately.
            pos = self.portfolio.positions.get(intent.symbol)
            parent = self._entry_parent(intent.symbol)
            if pos and pos.stop is not None and parent is not None:
                await self.oms.place_protective_stop(intent.symbol, pos.qty, pos.stop, parent,
                                                     now)
        return order

    def _entry_parent(self, symbol: str) -> Order | None:
        entries = [o for o in self.oms.orders.values()
                   if o.symbol == symbol and o.purpose == "entry" and o.filled_qty > 0]
        return max(entries, key=lambda o: o.created_at) if entries else None

    def _queue_flatten(self, now: datetime) -> None:
        queued = {p.symbol for p in self.pending if p.kind is IntentKind.EXIT}
        for sym, pos in self.portfolio.positions.items():
            if sym in queued or any(o.purpose == "exit" for o in self.oms.working_orders(sym)):
                continue
            ref = self.prices.get(sym, pos.avg_price)
            did = f"K{to_ist(now):%Y%m%d%H%M%S}-{sym}-flatten"
            self.pending.append(OrderIntent(did, sym, Side.SELL, IntentKind.EXIT, pos.strategy,
                                            "kill switch flatten", ref, now, now,
                                            now + timedelta(days=3)))

    # ============================================================ breakers
    def trip(self, name: str, reason: str, now: datetime) -> None:
        # In-memory state changes first so a failing database cannot stop the halt.
        if not self.breakers.trip(name, reason, now):
            return
        self.notifier.send(now, "critical", f"Breaker {name}: {reason}")
        try:
            self._risk_event(now, name, "BREAKER", {"reason": reason})
            self._audit(now, "breaker.tripped", {"breaker": name, "reason": reason})
            self._save_state(now)
        except Exception as exc:  # the halt stands even if it cannot be persisted
            self.notifier.send(now, "critical", f"Could not persist breaker {name}: "
                                                f"{type(exc).__name__}")

    def reset_breaker(self, name: str, note: str, now: datetime, actor: str = "owner") -> bool:
        if not note.strip():
            raise ValueError("a written review note is required to reset a breaker")
        ok = self.breakers.reset(name)
        if ok:
            if name == "broker_error":
                self.oms.broker_errors = 0
            if name == "reject_storm":
                self.oms.broker_rejects.clear()
            self._audit(now, "breaker.reset", {"breaker": name, "note": note, "actor": actor})
            self._save_state(now)
        return ok

    def mark_session(self, now: datetime) -> None:
        """Roll day/week marks at the first call of each trading day."""
        local = to_ist(now).date()
        eq = self.equity()
        if self.marks.session_date == local.isoformat():
            return
        self.marks.session_date = local.isoformat()
        self.marks.day_start = eq
        week_id = self.calendar.week_start(local).isoformat()
        if self.marks.week_id != week_id:
            self.marks.week_id = week_id
            self.marks.week_start = eq
        self.marks.peak = max(self.marks.peak, eq)
        if self.cfg.risk.breakers.daily_loss_auto_reset and "daily_loss" in self.breakers.tripped:
            self.breakers.reset("daily_loss")
            self._audit(now, "breaker.reset", {"breaker": "daily_loss", "note": "new day",
                                               "actor": "auto"})
        self._save_state(now)

    def check_breakers(self, now: datetime) -> None:
        rc = self.cfg.risk
        eq = self.equity()
        if self.marks.peak == 0:
            self.marks.peak = eq
        self.marks.peak = max(self.marks.peak, eq)
        day_start = self.marks.day_start or eq
        week_start = self.marks.week_start or eq
        for name, reason in evaluate_loss_breakers(eq, day_start, week_start, self.marks.peak,
                                                   rc):
            self.trip(name, reason, now)
        # Stale feed: in market hours, no fresh price for too long.
        if self.calendar.is_open(now) and self.calendar.minutes_since_open(now) * 60 > \
                rc.breakers.stale_data_halt_sec:
            newest = max(self.price_ts.values(), default=None)
            age = (now - newest).total_seconds() if newest else float("inf")
            if age > rc.breakers.stale_data_halt_sec:
                why = (f"no fresh prices for {age:.0f}s" if newest
                       else "no prices received yet: is the data feed connected?")
                self.trip("stale_data", why, now)
            elif "stale_data" in self.breakers.tripped:
                self.breakers.reset("stale_data")
                self._audit(now, "breaker.reset", {"breaker": "stale_data",
                                                   "note": "feed recovered", "actor": "auto"})
                self._save_state(now)
        if self.oms.broker_errors >= rc.breakers.broker_error_threshold:
            self.trip("broker_error", f"{self.oms.broker_errors} consecutive broker errors", now)
        window = now - timedelta(minutes=rc.breakers.reject_storm_window_min)
        rejects = [t for t in self.oms.broker_rejects if t >= window]
        self.oms.broker_rejects = rejects
        if len(rejects) >= rc.breakers.reject_storm_threshold:
            self.trip("reject_storm", f"{len(rejects)} broker rejects in window", now)
        if self.quarantine.tripped(now):
            self.trip("data_quarantine", "too much invalid market data", now)

    async def resolve_uncertain(self, now: datetime) -> int:
        """After a restart, orders stuck in SUBMITTED are resolved by asking the
        broker, never by resending (rule I-15, chaos scenario 7)."""
        n = 0
        for o in list(self.oms.orders.values()):
            if o.state is not OrderState.SUBMITTED:
                continue
            found = await self.broker.find_order(o.id)
            if found:
                o.broker_order_id = found
                self.oms.transition(o, OrderState.ACKED, now, note="adopted after restart")
            else:
                self.oms.transition(o, OrderState.FAILED, now, note="not at broker after restart")
            n += 1
        return n

    async def reconcile(self, now: datetime) -> bool:
        """Compare the bot's view with the broker's. The broker wins (rule I-08)."""
        broker_pos = {p.symbol: p.qty for p in await self.broker.positions()}
        ours = {s: p.qty for s, p in self.portfolio.positions.items()}
        funds = await self.broker.funds()
        cash_diff = abs(funds.cash - self.portfolio.cash)
        ok = broker_pos == ours and cash_diff <= Decimal("1.00")
        if not ok:
            self.trip("reconciliation",
                      f"positions ours={ours} broker={broker_pos}, cash diff ₹{cash_diff:.2f}",
                      now)
        # Orders the OMS thinks are working but the broker no longer has.
        open_ids = await self.broker.open_order_ids()
        for o in self.oms.working_orders():
            if o.state in WORKING and o.state is not OrderState.SUBMITTED \
                    and o.id not in open_ids and o.remaining_qty > 0:
                self.oms.transition(o, OrderState.EXPIRED, now, note="not open at broker")
        return ok

    # ================================================================ kill
    def activate_kill(self, action: str, reason: str, now: datetime,
                      actor: str = "owner") -> None:
        if action not in ("block", "flatten"):
            raise ValueError("action must be block or flatten")
        self.kill = KillSwitch(True, action, reason, now)
        if self.repo:
            self.repo.set_state("kill_switch", self.kill.to_state(), now)
        self._audit(now, "kill_switch.activated", {"action": action, "reason": reason,
                                                   "actor": actor})
        self.notifier.send(now, "critical", f"Kill switch ({action}): {reason}")
        if action == "flatten":
            self._queue_flatten(now)
            self._save_state(now)

    def release_kill(self, note: str, now: datetime, actor: str = "owner") -> None:
        if not note.strip():
            raise ValueError("a written note is required to release the kill switch")
        self.kill = KillSwitch()
        if self.repo:
            self.repo.set_state("kill_switch", self.kill.to_state(), now)
        self._audit(now, "kill_switch.released", {"note": note, "actor": actor})

    def sync_kill_from_db(self) -> None:
        """The watchdog may set the kill switch directly in the database."""
        if self.repo:
            db_kill = KillSwitch.from_state(self.repo.get_state("kill_switch"))
            if db_kill.active and not self.kill.active:
                self.kill = db_kill
                self._audit(db_kill.ts or datetime.now().astimezone(), "kill_switch.observed",
                            {"action": db_kill.action, "reason": db_kill.reason})
            elif not db_kill.active and self.kill.active:
                self.kill = db_kill

    # ============================================================= reports
    def snapshot_equity(self, now: datetime) -> None:
        if not self.repo:
            return
        eq = self.equity()
        peak = max(self.marks.peak, eq)
        dd = float((peak - eq) / peak * 100) if peak > 0 else 0.0
        self.repo.add_equity_snapshot(now, eq, self.portfolio.cash,
                                      self.portfolio.market_value(self.prices),
                                      self.portfolio.realized, self.portfolio.fees, dd)

    def status(self, now: datetime) -> dict[str, Any]:
        eq = self.equity()
        peak = max(self.marks.peak, eq)
        return {
            "mode": self.mode.value,
            "config_version": self.config_version,
            "market_open": self.calendar.is_open(now),
            "equity": eq,
            "cash": self.portfolio.cash,
            "starting_capital": self.portfolio.starting_cash,
            "day_pnl": eq - (self.marks.day_start or eq),
            "total_pnl": eq - self.portfolio.starting_cash,
            "realized": self.portfolio.realized,
            "fees": self.portfolio.fees,
            "drawdown_pct": float((peak - eq) / peak * 100) if peak > 0 else 0.0,
            "kill_switch": self.kill.to_state(),
            "breakers": self.breakers.to_state(),
            "halted": self.breakers.halted,
            "entries_blocked": self.breakers.entries_blocked or self.kill.active,
            "pending_intents": [self._intent_state(i) for i in self.pending],
            "last_cycle": self.last_cycle,
            "last_bar_ts": self.last_bar_ts,
            "regime": self.regime,
            "timeframe": self.cfg.strategy.timeframe,
        }
