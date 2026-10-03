"""End-to-end: intents -> risk -> OMS -> paper broker -> fills -> portfolio,
plus persistence, restart and backtest reproducibility."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from decimal import Decimal

import pytest

from algotrade.audit.logger import AuditLog
from algotrade.backtest.engine import run_backtest
from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig
from algotrade.core.clock import MarketCalendar
from algotrade.core.types import OrderState, Tick
from algotrade.data.providers.replay import synthetic_daily_bars
from algotrade.data.universe import Universe
from algotrade.db.repository import Repository
from algotrade.db.session import Database
from algotrade.engine.core import TradingEngine
from tests.conftest import NOW, SESSION_DAY, entry_intent, exit_intent, ist


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def make_engine(cfg: AppConfig, db: Database, state: dict[str, object] | None = None
                ) -> TradingEngine:
    repo = Repository(db)
    uni = Universe.from_config(cfg.strategy.universe.model_copy(
        update={"symbols": ["INFY", "TCS", "SBIN"]}))

    def persist(snap: dict[str, object]) -> None:
        repo.set_state("paper_ledger", snap, NOW)  # type: ignore[arg-type]

    broker = PaperBroker(cfg.strategy.paper_broker, CostModel(cfg.strategy.costs),
                         cfg.strategy.capital, persist, repo.get_state("paper_ledger"))
    eng = TradingEngine(cfg, 1, uni, MarketCalendar(), broker, repo, AuditLog(db))
    eng.restore(NOW)
    for s in uni.symbols:
        bars = synthetic_daily_bars(s, date(2025, 6, 1), 120, seed=4, start_price=1000)
        eng.load_daily_history(s, bars, NOW)
        eng.prev_close[s] = Decimal("995")
    return eng


def tick(eng: TradingEngine, sym: str, px: str, when=NOW) -> None:  # type: ignore[no-untyped-def]
    eng.on_tick(Tick(sym, when, Decimal(px)), when)


def open_position(eng: TradingEngine) -> None:
    tick(eng, "INFY", "1000")
    eng.pending.append(entry_intent())
    placed = run(eng.execute_pending(NOW))
    assert placed and placed[0].state is OrderState.ACKED, placed[0].reason if placed else ""
    tick(eng, "INFY", "1000", NOW + timedelta(seconds=5))
    run(eng.process_fills(NOW + timedelta(seconds=5)))


def test_entry_fill_places_exchange_side_stop(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    pos = eng.portfolio.positions["INFY"]
    assert pos.qty == 20 and pos.stop == Decimal("960")
    stop = eng.oms.protective_stop("INFY")
    assert stop is not None and stop.trigger_price == Decimal("960")
    assert run(eng.reconcile(NOW))


def test_stop_trigger_closes_position_and_records_trade(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    later = NOW + timedelta(minutes=30)
    tick(eng, "INFY", "955", later)
    run(eng.process_fills(later))
    assert "INFY" not in eng.portfolio.positions
    assert eng.trades and eng.trades[0].pnl < 0
    assert eng.oms.protective_stop("INFY") is None
    assert run(eng.reconcile(later))


def test_engine_exit_cancels_stop_then_sells(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    t = NOW + timedelta(minutes=10)
    tick(eng, "INFY", "1010", t)
    eng.pending.append(exit_intent(ref="1010"))
    run(eng.execute_pending(t))
    stop_states = [o.state for o in eng.oms.orders.values() if o.purpose == "protective_stop"]
    assert stop_states == [OrderState.CANCELLED]
    t2 = t + timedelta(seconds=5)
    tick(eng, "INFY", "1010", t2)
    run(eng.process_fills(t2))
    assert "INFY" not in eng.portfolio.positions
    assert run(eng.reconcile(t2))


def test_restart_rebuilds_identical_state(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    eng.trip("weekly_loss", "test", NOW)
    before = ({s: (p.qty, p.avg_price, p.stop) for s, p in eng.portfolio.positions.items()},
              eng.portfolio.cash)
    eng2 = make_engine(cfg, db)
    after = ({s: (p.qty, p.avg_price, p.stop) for s, p in eng2.portfolio.positions.items()},
             eng2.portfolio.cash)
    assert before == after
    assert "weekly_loss" in eng2.breakers.tripped
    assert eng2.oms.protective_stop("INFY") is not None
    assert run(eng2.reconcile(NOW))
    assert AuditLog(db).verify() == (True, None)


def test_every_order_has_a_risk_approved_event(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    repo = Repository(db)
    for o in repo.orders(1000):
        events = [e.to_state for e in repo.order_events(o.id)]
        if "SUBMITTED" in events:
            assert events.index("RISK_APPROVED") < events.index("SUBMITTED")


def test_rejection_is_recorded_with_reason(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    tick(eng, "INFY", "1000")
    eng.pending.append(entry_intent(stop="999.5"))  # too tight
    placed = run(eng.execute_pending(NOW))
    assert placed[0].state is OrderState.REJECTED
    events = Repository(db).risk_events()
    assert any(e.check_name == "stop" for e in events)
    assert not eng.pending


def test_intent_waits_outside_entry_window_then_expires(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    early = ist(SESSION_DAY, 9, 16)
    tick(eng, "INFY", "1000", early)
    intent = entry_intent(now=early)
    intent.expires_at = early + timedelta(minutes=30)
    eng.pending.append(intent)
    assert run(eng.execute_pending(early)) == []
    assert eng.pending  # still waiting: first 5 minutes are blocked
    run(eng.execute_pending(early + timedelta(hours=1)))
    assert not eng.pending


def test_kill_switch_flatten_sells_everything(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    t = NOW + timedelta(minutes=5)
    tick(eng, "INFY", "1001", t)
    eng.activate_kill("flatten", "test", t)
    run(eng.execute_pending(t))
    tick(eng, "INFY", "1001", t + timedelta(seconds=5))
    run(eng.process_fills(t + timedelta(seconds=5)))
    assert not eng.portfolio.positions
    eng.pending.append(entry_intent(decision="D2"))
    placed = run(eng.execute_pending(t + timedelta(seconds=10)))
    assert placed and placed[0].state is OrderState.REJECTED  # entries blocked
    with pytest.raises(ValueError):
        eng.release_kill("", t)
    eng.release_kill("reviewed", t)
    assert not eng.kill.active


def test_loss_breakers_trip_and_need_note_to_reset(cfg: AppConfig, db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    eng.mark_session(NOW)
    crash = NOW + timedelta(minutes=1)
    tick(eng, "INFY", "990", crash)  # small move, no breaker
    eng.check_breakers(crash)
    assert "daily_loss" not in eng.breakers.tripped
    eng.marks.peak = eng.equity() * Decimal("1.2")
    eng.check_breakers(crash)
    assert eng.breakers.halted and "drawdown" in eng.breakers.tripped
    with pytest.raises(ValueError):
        eng.reset_breaker("drawdown", " ", crash)
    assert eng.reset_breaker("drawdown", "reviewed: test", crash)


def test_backtest_is_reproducible(cfg: AppConfig) -> None:
    uni = Universe.from_config(cfg.strategy.universe.model_copy(
        update={"symbols": ["INFY", "TCS", "SBIN", "ITC"]}))
    bars = {s: synthetic_daily_bars(s, date(2024, 1, 1), 420, seed=i, start_price=500 + 100 * i,
                                    drift=0.001) for i, s in enumerate(uni.symbols)}
    bars["NIFTY50"] = synthetic_daily_bars("NIFTY50", date(2024, 1, 1), 420, seed=99,
                                           start_price=20000, drift=0.0008, vol=0.008)
    a = run_backtest(cfg, uni, bars, date(2025, 1, 1), date(2025, 6, 30))
    b = run_backtest(cfg, uni, bars, date(2025, 1, 1), date(2025, 6, 30))
    assert a.metrics == b.metrics and a.trades == b.trades
    assert a.metrics["days"] > 100
    assert a.trades, "fixture should produce trades"
    # Every trade is costed; no trade fills at the signal bar's close.
    for t in a.trades:
        assert t["fees"] > 0
        assert "09:20" in t["opened"]
