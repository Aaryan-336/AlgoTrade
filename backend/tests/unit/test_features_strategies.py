from __future__ import annotations

import math
from datetime import date, timedelta
from decimal import Decimal

from algotrade.config.models import AppConfig, BreakoutParams, TrendParams
from algotrade.core.clock import MarketCalendar
from algotrade.core.types import Bar, Position, SentimentAggregate
from algotrade.data.providers.replay import daily_ts, synthetic_daily_bars
from algotrade.decision.engine import DecisionEngine, expiry_for
from algotrade.features import indicators as ind
from algotrade.strategies.base import StrategyContext, price
from algotrade.strategies.breakout import BreakoutStrategy
from algotrade.strategies.trend import TrendStrategy
from tests.conftest import NOW


def test_sma_ema_rsi_atr_known_values() -> None:
    assert ind.sma([1, 2, 3, 4], 2)[1:] == [1.5, 2.5, 3.5]
    e = ind.ema([1.0] * 10, 3)
    assert all(abs(x - 1.0) < 1e-12 for x in e[2:]) and math.isnan(e[0])
    r = ind.rsi([float(i) for i in range(30)], 14)
    assert r[-1] == 100.0
    flat = ind.rsi([5.0] * 30, 14)
    assert flat[-1] == 50.0
    a = ind.atr([11.0] * 20, [9.0] * 20, [10.0] * 20, 14)
    assert abs(a[-1] - 2.0) < 1e-9
    assert math.isnan(ind.ema([1.0], 5)[0])


def test_rolling_std_matches_naive() -> None:
    vals = [float(x % 7) + x * 0.1 for x in range(60)]
    fast = ind.rolling_std(vals, 20)
    for i in range(19, 60):
        w = vals[i - 19 : i + 1]
        m = sum(w) / 20
        naive = math.sqrt(sum((x - m) ** 2 for x in w) / 20)
        assert abs(fast[i] - naive) < 1e-9


def test_correlation_bounds() -> None:
    a = [math.sin(i) for i in range(50)]
    assert abs(ind.correlation(a, a) - 1) < 1e-9
    assert abs(ind.correlation(a, [-x for x in a]) + 1) < 1e-9
    assert ind.correlation([1.0] * 5, [2.0] * 5) == 0.0


def _trend_bars(n: int = 120, symbol: str = "INFY") -> list[Bar]:
    bars, d, p = [], date(2025, 1, 1), 100.0
    cal = MarketCalendar()
    while len(bars) < n:
        if cal.is_trading_day(d):
            # Steady uptrend with a pullback every 3rd bar keeps RSI below 70.
            step = -0.9 if len(bars) % 3 == 0 else 0.6
            o, c = p, p + step
            vol = 3_000_000 if len(bars) == n - 1 else 1_000_000
            bars.append(Bar(symbol, "1d", daily_ts(d), Decimal(str(round(o, 2))),
                            Decimal(str(round(max(o, c) + 0.3, 2))),
                            Decimal(str(round(min(o, c) - 0.3, 2))),
                            Decimal(str(round(c, 2))), vol))
            p = c
        d += timedelta(days=1)
    return bars


def test_trend_strategy_buy_and_exit() -> None:
    s = TrendStrategy(TrendParams())
    fs = ind.FeatureSet(_trend_bars())
    sig = s.evaluate("INFY", fs, StrategyContext(NOW, holding=False))
    assert sig.action == "BUY", sig.reasons
    assert sig.suggested_stop is not None and sig.suggested_stop < Decimal(str(fs.close))
    assert sig.suggested_target is not None and sig.suggested_target > Decimal(str(fs.close))
    assert s.evaluate("INFY", fs, StrategyContext(NOW, holding=True)).action == "HOLD"
    short = ind.FeatureSet(_trend_bars(20))
    assert s.evaluate("INFY", short, StrategyContext(NOW, False)).action == "HOLD"


def test_trend_exit_when_close_below_fast_ema() -> None:
    bars = _trend_bars()
    last = bars[-1]
    crash = Bar(last.symbol, "1d", last.ts + timedelta(days=1), last.close, last.close,
                last.close - 20, last.close - 18, 1_000_000)
    fs = ind.FeatureSet([*bars, crash])
    assert TrendStrategy(TrendParams()).evaluate("INFY", fs,
                                                 StrategyContext(NOW, True)).action == "SELL"


def test_breakout_strategy_runs_and_is_pure() -> None:
    s = BreakoutStrategy(BreakoutParams())
    fs = ind.FeatureSet(synthetic_daily_bars("X", date(2024, 1, 1), 200, seed=3))
    a = s.evaluate("X", fs, StrategyContext(NOW, False))
    b = s.evaluate("X", fs, StrategyContext(NOW, False))
    assert a == b and a.action in ("BUY", "HOLD")


def test_price_rounds_to_tick() -> None:
    assert price(101.02) == Decimal("101.00")
    assert price(101.03) == Decimal("101.05")


# ------------------------------------------------------------ decisions
def _engine(cfg: AppConfig, max_pos: int = 5) -> DecisionEngine:
    return DecisionEngine(cfg.strategy, [TrendStrategy(cfg.strategy.strategies.trend)],
                          MarketCalendar(), max_pos)


def _index(up: bool = True) -> ind.FeatureSet:
    bars = synthetic_daily_bars("NIFTY50", date(2024, 1, 1), 260, seed=1, start_price=20000,
                                drift=0.004 if up else -0.004, vol=0.002)
    return ind.FeatureSet(bars)


def test_decision_entry_intent_and_regime_block(cfg: AppConfig) -> None:
    fs = ind.FeatureSet(_trend_bars())
    bar_ts = fs.last.ts
    exp = expiry_for(bar_ts, "1d", 1, MarketCalendar())
    res = _engine(cfg).run(NOW, bar_ts, {"INFY": fs}, {}, {}, _index(True), 14.0, exp)
    assert [i.symbol for i in res.intents] == ["INFY"]
    it = res.intents[0]
    assert it.stop is not None and it.ref_price == Decimal(str(fs.close))
    blocked = _engine(cfg).run(NOW, bar_ts, {"INFY": fs}, {}, {}, _index(False), 14.0, exp)
    assert not blocked.intents and blocked.decisions[0].outcome == "vetoed"
    unknown = _engine(cfg).run(NOW, bar_ts, {"INFY": fs}, {}, {}, None, None, exp)
    assert not unknown.intents  # fail closed without index data
    vix = _engine(cfg).run(NOW, bar_ts, {"INFY": fs}, {}, {}, _index(True), 40.0, exp)
    assert vix.intents[0].size_multiplier == 0.5


def test_negative_material_news_vetoes_entry(cfg: AppConfig) -> None:
    fs = ind.FeatureSet(_trend_bars())
    bad = SentimentAggregate("INFY", -0.8, 3, True, False)
    res = _engine(cfg).run(NOW, fs.last.ts, {"INFY": fs}, {}, {"INFY": bad}, _index(), None,
                           NOW + timedelta(days=1))
    assert not res.intents
    assert res.decisions[0].reason == "negative material news"


def _position(symbol: str, fs: ind.FeatureSet, **kw: object) -> Position:
    base: dict[str, object] = dict(symbol=symbol, qty=10,
                                   avg_price=Decimal(str(fs.closes[-30])),
                                   opened_at=NOW - timedelta(days=10), strategy="trend",
                                   sector="IT",
                                   stop=Decimal(str(fs.closes[-30] - 5)),
                                   initial_stop=Decimal(str(fs.closes[-30] - 5)))
    base.update(kw)
    return Position(**base)  # type: ignore[arg-type]


def test_exit_rules_target_time_and_trailing(cfg: AppConfig) -> None:
    fs = ind.FeatureSet(_trend_bars())
    eng = _engine(cfg)
    exp = NOW + timedelta(days=1)
    tgt = _position("INFY", fs, target=Decimal("1"))
    res = eng.run(NOW, fs.last.ts, {"INFY": fs}, {"INFY": tgt}, {}, _index(), None, exp)
    assert res.intents and "target" in res.intents[0].reason
    old = _position("INFY", fs, opened_at=fs.bars[0].ts)
    res = eng.run(NOW, fs.last.ts, {"INFY": fs}, {"INFY": old}, {}, _index(), None, exp)
    assert "time stop" in res.intents[0].reason
    entry = Decimal(str(fs.closes[-30]))
    trail = _position("INFY", fs, stop=entry - 2, initial_stop=entry - 2)
    res = eng.run(NOW, fs.last.ts, {"INFY": fs}, {"INFY": trail}, {}, _index(), None, exp)
    assert not res.intents
    assert res.stop_updates["INFY"] > trail.stop  # type: ignore[operator]


def test_rotation_swaps_weakest_for_much_better_candidate(cfg: AppConfig) -> None:
    good = ind.FeatureSet(_trend_bars(symbol="GOOD"))
    weak_bars = synthetic_daily_bars("WEAK", date(2025, 1, 1), 120, seed=9, drift=-0.003)
    weak_bars = [Bar("WEAK", b.timeframe, g.ts, b.open, b.high, b.low, b.close, b.volume)
                 for b, g in zip(weak_bars, good.bars, strict=True)]
    weak = ind.FeatureSet(weak_bars)
    pos = _position("WEAK", weak, stop=Decimal("1"), initial_stop=Decimal("1"))
    eng = _engine(cfg, max_pos=1)
    res = eng.run(NOW, good.last.ts, {"GOOD": good, "WEAK": weak}, {"WEAK": pos}, {},
                  _index(), None, NOW + timedelta(days=1))
    kinds = [(i.symbol, i.side.value) for i in res.intents]
    assert ("WEAK", "SELL") in kinds and ("GOOD", "BUY") in kinds


def test_expiry_for_timeframes() -> None:
    cal = MarketCalendar()
    ts = daily_ts(date(2026, 9, 18))  # Friday
    exp = expiry_for(ts, "1d", 1, cal)
    assert exp.astimezone().date() >= date(2026, 9, 21)
    assert expiry_for(NOW, "15m", 1, cal) == NOW + timedelta(minutes=30)
