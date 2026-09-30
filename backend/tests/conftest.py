from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig, default_config
from algotrade.core.clock import IST
from algotrade.core.types import IntentKind, Mode, OrderIntent, Side
from algotrade.db.repository import Repository
from algotrade.db.session import Database
from algotrade.risk.engine import PositionView, RiskContext

# A Wednesday in the middle of the session.
SESSION_DAY = date(2026, 9, 16)


def ist(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=IST).astimezone(UTC)


NOW = ist(SESSION_DAY, 11, 0)


@pytest.fixture
def cfg() -> AppConfig:
    c = default_config()
    return c.model_copy(update={"strategy": c.strategy.model_copy(
        update={"capital": Decimal(100000)})})


@pytest.fixture
def costs(cfg: AppConfig) -> CostModel:
    return CostModel(cfg.strategy.costs)


@pytest.fixture
def db() -> Database:
    d = Database("sqlite://")
    d.create_all()
    return d


@pytest.fixture
def repo(db: Database) -> Repository:
    return Repository(db)


@pytest.fixture
def broker(cfg: AppConfig, costs: CostModel) -> PaperBroker:
    return PaperBroker(cfg.strategy.paper_broker, costs, cfg.strategy.capital)


def entry_intent(symbol: str = "INFY", ref: str = "1000", stop: str | None = "960",
                 target: str | None = "1080", decision: str = "D1", mult: float = 1.0,
                 now: datetime = NOW) -> OrderIntent:
    return OrderIntent(decision, symbol, Side.BUY, IntentKind.ENTRY, "trend", "test",
                       Decimal(ref), now, now, now.replace(year=now.year + 1),
                       Decimal(stop) if stop else None, Decimal(target) if target else None,
                       0.7, mult)


def exit_intent(symbol: str = "INFY", ref: str = "1000", decision: str = "X1",
                now: datetime = NOW) -> OrderIntent:
    return OrderIntent(decision, symbol, Side.SELL, IntentKind.EXIT, "trend", "test exit",
                       Decimal(ref), now, now, now.replace(year=now.year + 1))


def risk_ctx(**over: object) -> RiskContext:
    base: dict[str, object] = dict(
        now=NOW, mode=Mode.PAPER, kill_switch_active=False, halted=False, entry_blocks=(),
        market_open=True, in_entry_window=True, last_price=Decimal("1000"), last_price_ts=NOW,
        prev_close=Decimal("995"), tradable=True, sector="Information Technology",
        equity=Decimal(100000), cash=Decimal(100000), reserved_cash=Decimal(0), positions={},
        orders_last_minute=0, orders_today=0, idempotency_key_seen=False,
        avg_traded_value=5e9, max_correlation=0.0, blackout=False, pending_entries=0,
    )
    base.update(over)
    return RiskContext(**base)  # type: ignore[arg-type]


def pos_view(qty: int, value: str, sector: str = "Information Technology") -> PositionView:
    return PositionView(qty, Decimal(value), sector)
