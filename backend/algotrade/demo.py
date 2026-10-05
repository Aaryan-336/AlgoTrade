"""Offline demo: fills the database with SYNTHETIC data so the dashboard can
be explored without an Upstox login. Prices are a random walk and carry no
meaning; the dashboard shows a "synthetic demo data" banner while active.

    python -m algotrade.demo --days 120          # seed
    python -m algotrade.demo --clear             # remove the demo flag

Use a separate DATABASE_URL (e.g. sqlite:///./demo.db) so demo data never
mixes with real paper-trading history.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, time, timedelta

from algotrade.backtest.engine import SimStats, group_by_day, simulate_day
from algotrade.config.settings import get_settings
from algotrade.core.clock import IST, ManualClock
from algotrade.data.providers.replay import synthetic_daily_bars
from algotrade.engine.runtime import Runtime


async def seed(days: int, capital: float | None) -> None:
    settings = get_settings().model_copy(update={"data_provider": "replay"})
    today = datetime.now(IST).date()
    clock = ManualClock(datetime.now(UTC))
    rt = Runtime(settings, clock=clock)
    if capital:
        from decimal import Decimal

        cfg = rt.config.model_copy(update={"strategy": rt.config.strategy.model_copy(
            update={"capital": Decimal(str(capital))})})
        rt.repo.save_config(cfg, clock.now(), "demo", "demo capital")
        rt.config_version, rt.config = rt.repo.latest_config() or (rt.config_version, cfg)
        rt.engine = rt.build_engine()
    total = days + 320
    start = today - timedelta(days=int(total * 1.45))
    syms = rt.universe.symbols
    bars = {s: synthetic_daily_bars(s, start, total, seed=11, start_price=150 + (i * 97) % 2400,
                                    drift=0.0007) for i, s in enumerate(syms)}
    reg = rt.config.strategy.regime
    bars[reg.index_symbol] = synthetic_daily_bars(reg.index_symbol, start, total, seed=5,
                                                  start_price=22000, drift=0.0005, vol=0.009)
    bars[reg.vix_symbol] = synthetic_daily_bars(reg.vix_symbol, start, total, seed=6,
                                                start_price=14, drift=0.0, vol=0.03)
    by_day = group_by_day(bars)
    all_days = sorted(d for d in by_day if d < today)
    sim_days = all_days[-days:]
    eng = rt.engine
    first = sim_days[0]
    for sym, series in bars.items():
        eng.load_daily_history(sym, [b for b in series if b.ts.astimezone(IST).date() < first],
                               datetime.combine(first, time(8, 0), tzinfo=IST))
    stats = SimStats()
    for d in sim_days:
        clock.set(datetime.combine(d, time(15, 45), tzinfo=IST).astimezone(UTC))
        await simulate_day(eng, eng.broker, d, by_day[d], stats)
    clock.set(datetime.now(UTC))
    rt.repo.set_state("demo", {"active": True, "days": days, "seeded_at": clock.now()},
                      clock.now())
    print(f"Seeded {len(sim_days)} synthetic days: equity ₹{eng.equity():.2f}, "
          f"{len(eng.trades)} closed trades, {len(eng.portfolio.positions)} open positions.")
    await rt.aclose()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=120)
    p.add_argument("--capital", type=float, default=None)
    p.add_argument("--clear", action="store_true")
    a = p.parse_args()
    if a.clear:
        rt = Runtime(get_settings().model_copy(update={"data_provider": "replay"}))
        rt.repo.set_state("demo", {"active": False}, datetime.now(UTC))
        print("demo flag cleared")
        return
    asyncio.run(seed(a.days, a.capital))


if __name__ == "__main__":
    main()
