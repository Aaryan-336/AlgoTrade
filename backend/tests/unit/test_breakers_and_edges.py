from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker
from algotrade.config.models import AppConfig
from algotrade.core.types import Charges, Fill, Mode, OrderState, Side
from algotrade.oms.manager import OrderManager
from algotrade.risk.breakers import (
    BreakerBoard,
    Severity,
    evaluate_loss_breakers,
    loss_pct,
)
from algotrade.risk.engine import RiskDecision, RiskEngine
from tests.conftest import NOW, entry_intent, pos_view, risk_ctx


def test_breaker_board_roundtrip_and_severity() -> None:
    b = BreakerBoard()
    assert b.trip("daily_loss", "x", NOW)
    assert not b.trip("daily_loss", "again", NOW)
    assert b.entries_blocked and not b.halted
    b.trip("drawdown", "y", NOW)
    assert b.halted and b.tripped["drawdown"].severity is Severity.HALT
    restored = BreakerBoard.from_state({**b.to_state(), "bogus": {"tripped_at": "x"}})
    assert set(restored.tripped) == {"daily_loss", "drawdown"}
    assert restored.reasons()
    assert restored.reset("drawdown") and not restored.reset("drawdown")
    with pytest.raises(KeyError):
        b.trip("not_a_breaker", "z", NOW)
    assert BreakerBoard.from_state(None).tripped == {}


def test_loss_breaker_thresholds(cfg: AppConfig) -> None:
    r = cfg.risk
    assert evaluate_loss_breakers(Decimal(100), Decimal(100), Decimal(100), Decimal(100), r) == []
    names = {n for n, _ in evaluate_loss_breakers(Decimal(89), Decimal(100), Decimal(100),
                                                  Decimal(100), r)}
    assert names == {"daily_loss", "weekly_loss", "drawdown"}
    assert loss_pct(Decimal(0), Decimal(5)) == 0.0


def test_exposure_check_is_defense_in_depth(cfg: AppConfig) -> None:
    """Sizing already respects these caps; the independent re-check still
    rejects if a quantity ever exceeded them."""
    eng = RiskEngine(cfg.risk, CostModel(cfg.strategy.costs))
    i = entry_intent()
    ctx = risk_ctx()
    assert not eng._exposure_check(i, ctx, 25, Decimal(1000)).passed  # 25% > 20%
    crowded = risk_ctx(positions={"TCS": pos_view(1, "34000")})
    assert "sector" in eng._exposure_check(i, crowded, 5, Decimal(1000)).detail
    rich = risk_ctx(equity=Decimal(10_000_000))
    assert "order value" in eng._exposure_check(i, rich, 30, Decimal(1000)).detail


def test_oms_cancel_edge_cases(cfg: AppConfig) -> None:
    b = PaperBroker(cfg.strategy.paper_broker, CostModel(cfg.strategy.costs), Decimal(10**6))
    oms = OrderManager(b, Mode.PAPER)
    o = oms.create(entry_intent(), RiskDecision(True, 1, []), NOW)
    o.state = OrderState.SUBMITTED
    assert not asyncio.run(oms.cancel(o, NOW))  # never cancel before the ack
    o.state = OrderState.ACKED
    o.broker_order_id = "missing"
    assert not asyncio.run(oms.cancel(o, NOW))
    assert o.state is OrderState.EXPIRED
    stray = Fill("unknown", "b", "INFY", Side.BUY, NOW, 1, Decimal(1), Charges())
    assert oms.on_fill(stray, NOW) is None
