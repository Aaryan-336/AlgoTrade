"""Event-driven backtester (docs/design.md §11).

Replays daily bars through the *same* TradingEngine, Risk Engine, Order
Manager and PaperBroker used for live paper trading. Per day:

  09:20  pending intents from yesterday's close are risk-checked and sent;
         market orders fill at today's open plus slippage (never at the
         signal bar's close: no look-ahead).
  15:29  stops and limits are checked against today's high/low; stops that
         were live before the open and gapped through fill at the open.
  15:45  today's bar closes; strategies run and queue tomorrow's intents.

Sentiment is not replayed (no reliable history), so it is neutral here.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any

from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig
from algotrade.core.clock import IST, MarketCalendar
from algotrade.core.types import Bar
from algotrade.data.universe import Universe
from algotrade.engine.alerts import Notifier
from algotrade.engine.core import TradingEngine


def _at(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


@dataclass
class BacktestResult:
    metrics: dict[str, Any]
    equity_curve: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    rejections: dict[str, int] = field(default_factory=dict)


def group_by_day(bars: dict[str, list[Bar]]) -> dict[date, dict[str, Bar]]:
    by_day: dict[date, dict[str, Bar]] = {}
    for sym, series in bars.items():
        for b in series:
            by_day.setdefault(b.ts.astimezone(IST).date(), {})[sym] = b
    return by_day


@dataclass
class SimStats:
    curve: list[dict[str, Any]] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    exposure_days: int = 0


async def simulate_day(engine: TradingEngine, broker: PaperBroker, d: date,
                       todays: dict[str, Bar], stats: SimStats) -> None:
    """One trading day of the daily-bar schedule described in the module doc."""
    t_open, t_exec = _at(d, time(9, 15)), _at(d, time(9, 20))
    t_close, t_eod = _at(d, time(15, 29)), _at(d, time(15, 45))
    engine.mark_session(t_exec)
    for sym, b in todays.items():
        engine.set_price(sym, b.open, t_exec)
        adv = sum(x.volume for x in engine.daily.get(sym, [])[-20:]) / 20 or None
        if adv:
            broker.adv_volume[sym] = adv
    for o in await engine.execute_pending(t_exec):
        if o.state.value in ("REJECTED", "FAILED"):
            key = (o.reason.split(":")[0] or "rejected")[:40]
            stats.rejections[key] = stats.rejections.get(key, 0) + 1
    for sym, b in todays.items():
        broker.on_market(sym, t_exec, b.open, b.open, b.open, b.open)
    await engine.process_fills(t_exec)
    for sym, b in todays.items():
        broker.on_market(sym, t_close, b.open, b.high, b.low, b.close, b.volume,
                         session_start=t_open)
    await engine.process_fills(t_close)
    for sym, b in todays.items():
        engine.set_price(sym, b.close, t_eod)
        engine.append_bar(b, t_eod)
    engine.check_breakers(t_close)
    if engine.portfolio.positions:
        stats.exposure_days += 1
    stats.curve.append({"date": d.isoformat(), "equity": float(engine.equity()),
                        "cash": float(engine.portfolio.cash),
                        "positions": len(engine.portfolio.positions)})
    engine.snapshot_equity(t_eod)
    await engine.decision_cycle(t_eod, next(iter(todays.values())).ts)


def run_backtest(cfg: AppConfig, universe: Universe, bars: dict[str, list[Bar]],
                 start: date, end: date, calendar: MarketCalendar | None = None) -> BacktestResult:
    cal = calendar or MarketCalendar()
    sc = cfg.strategy.model_copy(update={"timeframe": "1d"})
    sc = sc.model_copy(update={"sentiment": sc.sentiment.model_copy(update={"enabled": False})})
    run_cfg = AppConfig(risk=cfg.risk, strategy=sc)
    costs = CostModel(sc.costs)
    broker = PaperBroker(sc.paper_broker, costs, sc.capital)
    engine = TradingEngine(run_cfg, 0, universe, cal, broker, notifier=Notifier(quiet=True))

    by_day = group_by_day(bars)
    days = sorted(d for d in by_day if start <= d <= end)
    for sym, series in bars.items():
        hist = [b for b in series if b.ts.astimezone(IST).date() < start]
        engine.load_daily_history(sym, hist, _at(start, time(8, 0)))

    stats = SimStats()

    async def _run() -> None:
        for d in days:
            await simulate_day(engine, broker, d, by_day[d], stats)

    asyncio.run(_run())
    curve, rejections, exposure_days = stats.curve, stats.rejections, stats.exposure_days

    trades = [{"symbol": t.symbol, "strategy": t.strategy, "opened": t.opened_at.isoformat(),
               "closed": t.closed_at.isoformat(), "qty": t.qty, "entry": float(t.entry),
               "exit": float(t.exit), "pnl": float(t.pnl), "fees": float(t.fees)}
              for t in engine.trades]
    metrics = compute_metrics(curve, trades, float(sc.capital), exposure_days)
    metrics["breakers_tripped"] = list(engine.breakers.tripped)
    metrics["open_positions_at_end"] = len(engine.portfolio.positions)
    return BacktestResult(metrics, curve, trades, rejections)


def compute_metrics(curve: list[dict[str, Any]], trades: list[dict[str, Any]],
                    capital: float, exposure_days: int) -> dict[str, Any]:
    if not curve:
        return {"days": 0, "trades": 0}
    eq = [c["equity"] for c in curve]
    rets = [(eq[i] / eq[i - 1] - 1) for i in range(1, len(eq)) if eq[i - 1] > 0]
    n = len(eq)
    years = max(n / 252, 1 / 252)
    final = eq[-1]
    cagr = (final / capital) ** (1 / years) - 1 if final > 0 else -1.0
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1) if len(rets) > 1 else 0.0
    vol = math.sqrt(var) * math.sqrt(252)
    downside = [r for r in rets if r < 0]
    dvar = sum(r * r for r in downside) / len(downside) if downside else 0.0
    sharpe = (mean * 252) / vol if vol > 0 else 0.0
    sortino = (mean * 252) / (math.sqrt(dvar) * math.sqrt(252)) if dvar > 0 else 0.0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak if peak else 0.0)
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    fees = sum(t["fees"] for t in trades)
    gross = sum(t["pnl"] + t["fees"] for t in trades)
    turnover = sum(t["entry"] * t["qty"] + t["exit"] * t["qty"] for t in trades)
    return {
        "days": n,
        "start_equity": capital,
        "final_equity": round(final, 2),
        "total_return_pct": round((final / capital - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "volatility_pct": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown_pct": round(mdd * 100, 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0.0,
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0.0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0.0,
        "expectancy": round(sum(t["pnl"] for t in trades) / len(trades), 2) if trades else 0.0,
        "fees_total": round(fees, 2),
        "fees_share_of_gross_pct": round(fees / gross * 100, 1) if gross > 0 else None,
        "turnover": round(turnover, 2),
        "exposure_pct": round(exposure_days / n * 100, 1),
    }


def warmup_start(start: date, warmup_bars: int = 260) -> date:
    """Calendar start date that yields about ``warmup_bars`` trading days."""
    return start - timedelta(days=int(warmup_bars * 1.5))
