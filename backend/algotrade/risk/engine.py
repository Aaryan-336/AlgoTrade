"""Pre-trade Risk Engine (docs/risk-management.md §3).

The final authority on every engine order. It is deliberately simple:
every check runs on every intent, any failure rejects the whole intent,
and the approved quantity is the minimum allowed by every cap. It never
rounds a quantity up. Strategy code cannot reach this module's limits.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_FLOOR, Decimal

from algotrade.brokers.costs import CostModel
from algotrade.config.models import RiskConfig
from algotrade.core.types import IntentKind, Mode, OrderIntent, Side

_HUNDRED = Decimal(100)
MAX_SIZE_MULTIPLIER = 1.25


@dataclass(frozen=True, slots=True)
class PositionView:
    qty: int
    market_value: Decimal
    sector: str


@dataclass(frozen=True, slots=True)
class RiskContext:
    now: datetime
    mode: Mode
    kill_switch_active: bool
    halted: bool
    entry_blocks: tuple[str, ...]  # tripped breakers that block entries
    market_open: bool
    in_entry_window: bool
    last_price: Decimal | None
    last_price_ts: datetime | None
    prev_close: Decimal | None
    tradable: bool
    sector: str
    equity: Decimal
    cash: Decimal
    reserved_cash: Decimal  # value of pending buy orders
    positions: Mapping[str, PositionView]
    orders_last_minute: int
    orders_today: int
    idempotency_key_seen: bool
    avg_traded_value: float  # 20-bar average daily traded value, rupees
    max_correlation: float = 0.0  # vs existing holdings
    blackout: bool = False
    pending_entries: int = 0  # entries submitted but not yet filled
    flatten_requested: bool = False  # owner pressed kill switch "flatten"


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(slots=True)
class RiskDecision:
    approved: bool
    qty: int
    checks: list[CheckResult] = field(default_factory=list)
    binding_cap: str = ""

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]

    def summary(self) -> str:
        if self.approved:
            return f"approved qty={self.qty}"
        return "; ".join(f"{c.name}: {c.detail}" for c in self.failures)


def _floor(x: Decimal) -> int:
    if x <= 0:
        return 0
    return int(x.to_integral_value(rounding=ROUND_FLOOR))


class RiskEngine:
    def __init__(self, cfg: RiskConfig, costs: CostModel) -> None:
        self.cfg = cfg
        self.costs = costs

    # ------------------------------------------------------------------ API
    def evaluate(self, intent: OrderIntent, ctx: RiskContext) -> RiskDecision:
        if intent.kind is IntentKind.EXIT:
            return self._evaluate_exit(intent, ctx)
        return self._evaluate_entry(intent, ctx)

    # ------------------------------------------------------------- common
    def _common(self, intent: OrderIntent, ctx: RiskContext) -> list[CheckResult]:
        c = self.cfg
        out = [
            CheckResult("mode", ctx.mode is Mode.PAPER, f"mode {ctx.mode.value} not permitted"),
            # An owner-requested flatten only reduces risk, so a halt does not block it.
            CheckResult("halted",
                        not ctx.halted or (intent.kind is IntentKind.EXIT
                                           and ctx.flatten_requested),
                        "trading halted by a circuit breaker"),
            CheckResult("market_open", ctx.market_open, "market closed"),
            CheckResult("duplicate", not ctx.idempotency_key_seen, "idempotency key already used"),
            CheckResult("order_rate_minute", ctx.orders_last_minute < c.max_orders_per_minute,
                        f"{ctx.orders_last_minute} orders in the last minute"),
            CheckResult("order_rate_day", ctx.orders_today < c.max_orders_per_day,
                        f"{ctx.orders_today} orders today"),
        ]
        fresh = (ctx.last_price is not None and ctx.last_price_ts is not None
                 and (ctx.now - ctx.last_price_ts).total_seconds() <= c.max_data_staleness_sec)
        out.append(CheckResult("data_fresh", fresh, "no fresh last traded price"))
        out.append(self._price_sane(intent, ctx))
        return out

    def _price_sane(self, intent: OrderIntent, ctx: RiskContext) -> CheckResult:
        ltp = ctx.last_price
        if ltp is None or ltp <= 0:
            return CheckResult("price_sane", False, "no valid last price")
        if ctx.prev_close and ctx.prev_close > 0:
            band = abs(ltp - ctx.prev_close) / ctx.prev_close * _HUNDRED
            if band >= Decimal(str(self.cfg.max_circuit_band_pct)):
                return CheckResult("price_sane", False,
                                   f"price {band:.1f}% from previous close (circuit risk)")
        if intent.ref_price <= 0:
            return CheckResult("price_sane", False, "invalid reference price")
        dev = abs(ltp - intent.ref_price) / intent.ref_price * _HUNDRED
        if intent.kind is IntentKind.ENTRY and dev > Decimal(str(self.cfg.max_price_deviation_pct)):
            return CheckResult("price_sane", False,
                               f"LTP {ltp} is {dev:.2f}% from decision price {intent.ref_price}")
        return CheckResult("price_sane", True)

    # --------------------------------------------------------------- exits
    def _evaluate_exit(self, intent: OrderIntent, ctx: RiskContext) -> RiskDecision:
        checks = self._common(intent, ctx)
        pos = ctx.positions.get(intent.symbol)
        held = pos.qty if pos else 0
        checks.append(CheckResult("side", intent.side is Side.SELL, "exits must sell"))
        checks.append(CheckResult("has_position", held > 0, "no position to exit"))
        ok = all(ch.passed for ch in checks)
        return RiskDecision(ok, held if ok else 0, checks)

    # ------------------------------------------------------------- entries
    def _evaluate_entry(self, intent: OrderIntent, ctx: RiskContext) -> RiskDecision:
        c = self.cfg
        checks = self._common(intent, ctx)
        checks += [
            CheckResult("side", intent.side is Side.BUY, "entries must buy (no shorting)"),
            CheckResult("kill_switch", not ctx.kill_switch_active, "kill switch active"),
            CheckResult("breakers", not ctx.entry_blocks,
                        "entries blocked: " + ", ".join(ctx.entry_blocks)),
            CheckResult("entry_window", ctx.in_entry_window,
                        f"outside entry window (first {c.no_entry_first_minutes} / "
                        f"last {c.no_entry_last_minutes} minutes)"),
            CheckResult("tradable", ctx.tradable, "instrument not tradable or banned"),
            CheckResult("blackout", not ctx.blackout, "event blackout day"),
            CheckResult("no_averaging", intent.symbol not in ctx.positions,
                        "already holding; averaging is disabled"),
            CheckResult("max_open_positions",
                        len(ctx.positions) + ctx.pending_entries < c.max_open_positions,
                        f"{len(ctx.positions)} open + {ctx.pending_entries} pending "
                        f">= {c.max_open_positions}"),
            CheckResult("correlation", ctx.max_correlation <= c.max_correlation,
                        f"correlation {ctx.max_correlation:.2f} with a holding "
                        f"> {c.max_correlation}"),
            CheckResult("equity_positive", ctx.equity > 0, "equity is not positive"),
        ]

        entry = ctx.last_price or intent.ref_price
        stop_check, per_share_risk = self._stop_check(intent, entry)
        checks.append(stop_check)

        qty, binding = 0, ""
        if stop_check.passed and entry > 0 and ctx.equity > 0:
            qty, binding = self._size(intent, ctx, entry, per_share_risk)
        checks.append(CheckResult("size", qty > 0,
                                  f"size rounds to zero (binding cap: {binding})"))

        if qty > 0:
            checks.append(self._edge_check(intent, qty, entry))
            checks.append(self._exposure_check(intent, ctx, qty, entry))

        ok = all(ch.passed for ch in checks)
        return RiskDecision(ok, qty if ok else 0, checks, binding)

    def _stop_check(self, intent: OrderIntent, entry: Decimal) -> tuple[CheckResult, Decimal]:
        c = self.cfg
        if intent.stop is None:
            return CheckResult("stop", False, "no stop defined"), Decimal(0)
        if intent.stop >= entry or intent.stop <= 0:
            return CheckResult("stop", False, f"stop {intent.stop} not below entry {entry}"), \
                Decimal(0)
        dist_pct = (entry - intent.stop) / entry * _HUNDRED
        if dist_pct < Decimal(str(c.min_stop_distance_pct)):
            return CheckResult("stop", False, f"stop too tight ({dist_pct:.2f}%)"), Decimal(0)
        if dist_pct > Decimal(str(c.max_stop_distance_pct)):
            return CheckResult("stop", False, f"stop too wide ({dist_pct:.2f}%)"), Decimal(0)
        return CheckResult("stop", True), entry - intent.stop

    def _size(self, intent: OrderIntent, ctx: RiskContext, entry: Decimal,
              per_share_risk: Decimal) -> tuple[int, str]:
        c = self.cfg
        eq = ctx.equity
        mult = Decimal(str(max(0.0, min(intent.size_multiplier, MAX_SIZE_MULTIPLIER))))
        est_cost_rate = Decimal("0.003")  # buffer for buy-side charges when checking cash
        sector_value = sum((p.market_value for p in ctx.positions.values()
                            if p.sector == ctx.sector), Decimal(0))
        cash_free = (ctx.cash - ctx.reserved_cash
                     - eq * Decimal(str(c.min_cash_buffer_pct)) / _HUNDRED)
        caps = {
            "risk_per_trade": _floor(eq * Decimal(str(c.risk_per_trade_pct)) / _HUNDRED * mult
                                     / per_share_risk),
            "max_position_pct": _floor(eq * Decimal(str(c.max_position_pct)) / _HUNDRED / entry),
            "max_sector_pct": _floor((eq * Decimal(str(c.max_sector_pct)) / _HUNDRED
                                      - sector_value) / entry),
            "cash_after_buffer": _floor(cash_free / (entry * (1 + est_cost_rate))),
            "max_order_value": _floor(c.max_order_value / entry),
            "adv_participation": _floor(Decimal(str(ctx.avg_traded_value))
                                        * Decimal(str(c.max_adv_participation_pct)) / _HUNDRED
                                        / entry),
        }
        binding = min(caps, key=lambda k: caps[k])
        return max(0, caps[binding]), binding

    def _edge_check(self, intent: OrderIntent, qty: int, entry: Decimal) -> CheckResult:
        if intent.target is None or intent.target <= entry:
            return CheckResult("expected_edge", False, "no target above entry")
        reward = (intent.target - entry) * qty
        cost = self.costs.round_trip(qty, entry, intent.target)
        need = cost * Decimal(str(self.cfg.min_edge_to_cost_ratio))
        return CheckResult("expected_edge", reward >= need,
                           f"reward {reward:.2f} < {self.cfg.min_edge_to_cost_ratio}x "
                           f"round-trip costs {cost:.2f}")

    def _exposure_check(self, intent: OrderIntent, ctx: RiskContext, qty: int,
                        entry: Decimal) -> CheckResult:
        c = self.cfg
        value = entry * qty
        eq = ctx.equity
        sector_after = sum((p.market_value for p in ctx.positions.values()
                            if p.sector == ctx.sector), Decimal(0)) + value
        if value > eq * Decimal(str(c.max_position_pct)) / _HUNDRED:
            return CheckResult("exposure", False, "position cap exceeded")
        if sector_after > eq * Decimal(str(c.max_sector_pct)) / _HUNDRED:
            return CheckResult("exposure", False, f"sector {ctx.sector} cap exceeded")
        if value > c.max_order_value:
            return CheckResult("exposure", False, "order value cap exceeded")
        return CheckResult("exposure", True)
