"""Live data during the session: today's forming candle, the live re-score, the
price stream, and intraday exits on bad news (which still face the Risk Engine)."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from fastapi.testclient import TestClient
from pydantic import SecretStr

from algotrade.api.main import create_app
from algotrade.config.models import AppConfig
from algotrade.config.settings import Settings
from algotrade.core.clock import ManualClock
from algotrade.core.types import Bar, IntentKind, OrderState, SentimentAggregate
from algotrade.db.session import Database
from algotrade.engine.runtime import Runtime
from tests.conftest import NOW
from tests.integration.test_engine_flow import make_engine, open_position, run, tick


def test_ticks_build_todays_candle_until_the_real_bar_arrives(cfg: AppConfig,
                                                              db: Database) -> None:
    eng = make_engine(cfg, db)
    tick(eng, "TCS", "1000")
    tick(eng, "TCS", "1012", NOW + timedelta(seconds=5))
    tick(eng, "TCS", "996", NOW + timedelta(seconds=10))
    tick(eng, "TCS", "1004", NOW + timedelta(seconds=15))
    b = eng.live_bars["TCS"]
    assert (b.open, b.high, b.low, b.close) == (Decimal("1000"), Decimal("1012"),
                                                Decimal("996"), Decimal("1004"))
    assert eng.daily["TCS"][-1].ts < b.ts  # history itself is untouched

    # The broker's intraday candle supplies the true open/high/low/volume.
    eng.merge_live_candle(Bar("TCS", "1d", b.ts, Decimal("990"), Decimal("1015"),
                              Decimal("985"), Decimal("1001"), 123456))
    m = eng.live_bars["TCS"]
    assert (m.open, m.high, m.low, m.close, m.volume) == (
        Decimal("990"), Decimal("1015"), Decimal("985"), Decimal("1004"), 123456)

    # After the close the real bar is appended; the forming candle goes away.
    eng.append_bar(Bar("TCS", "1d", b.ts, Decimal("990"), Decimal("1015"), Decimal("985"),
                       Decimal("1003"), 200000), NOW)
    tick(eng, "TCS", "1003", NOW + timedelta(seconds=20))
    assert "TCS" not in eng.live_bars


def test_live_rescore_uses_todays_candle_and_creates_no_intents(cfg: AppConfig,
                                                                db: Database) -> None:
    eng = make_engine(cfg, db)
    for s in eng.universe.symbols:
        tick(eng, s, str(eng.daily[s][-1].close))
    rows = eng.rank_live(NOW)
    assert {r["symbol"] for r in rows} == set(eng.universe.symbols)
    assert all(r["bar_ts"] == eng.live_bars[str(r["symbol"])].ts for r in rows)
    assert eng.pending == [] and eng.live_ranking_at == NOW


def test_bad_news_on_a_holding_queues_one_exit_through_risk(cfg: AppConfig,
                                                            db: Database) -> None:
    eng = make_engine(cfg, db)
    open_position(eng)
    bad = SentimentAggregate("INFY", -0.8, 3, True, False, NOW, ["fraud probe reported"])

    def fake_load(now: object) -> None:
        eng.sentiment = {"INFY": bad}

    eng._load_sentiment = fake_load  # type: ignore[method-assign]
    later = NOW + timedelta(minutes=30)
    assert eng.news_exits(later) == 1
    assert eng.news_exits(later) == 0  # no duplicate while one is pending
    intent = eng.pending[0]
    assert intent.kind is IntentKind.EXIT and "fraud probe" in intent.reason

    tick(eng, "INFY", "1001", later)
    placed = run(eng.execute_pending(later))
    assert placed and placed[0].state is not OrderState.REJECTED
    tick(eng, "INFY", "1001", later + timedelta(seconds=5))
    run(eng.process_fills(later + timedelta(seconds=5)))
    assert "INFY" not in eng.portfolio.positions


def test_api_streams_prices_and_serves_the_live_candle() -> None:
    from datetime import date

    from algotrade.core.types import Tick
    from algotrade.data.providers.replay import synthetic_daily_bars

    settings = Settings(data_provider="replay", database_url="sqlite://",
                        api_token=SecretStr("t0ken"))
    rt = Runtime(settings, db=Database("sqlite://"), clock=ManualClock(NOW))
    H = {"Authorization": "Bearer t0ken"}
    with TestClient(create_app(settings, rt, autostart=False)) as c:
        sym = rt.universe.symbols[0]
        rt.engine.load_daily_history(sym, synthetic_daily_bars(sym, date(2025, 6, 2), 300,
                                                               seed=1), NOW)
        prev = rt.engine.daily[sym][-1].close
        rt.engine.on_tick(Tick(sym, NOW, prev * Decimal("1.02")), NOW)

        prices = c.get("/api/prices", headers=H).json()
        assert round(prices[sym]["chg"], 2) == 2.0 and prices[sym]["ts"]

        bars = c.get(f"/api/bars/{sym}?limit=50", headers=H).json()
        assert bars["live"] is True
        assert bars["bars"][-1]["c"] == float(prev * Decimal("1.02"))

        with c.websocket_connect("/ws?token=t0ken") as ws:
            msg = ws.receive_json()
            assert sym in msg["prices"]
