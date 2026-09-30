from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from algotrade.brokers.costs import CostModel
from algotrade.config.models import AppConfig
from algotrade.core.types import Mode, Side
from algotrade.risk.engine import RiskEngine
from tests.conftest import NOW, entry_intent, exit_intent, pos_view, risk_ctx


@pytest.fixture
def engine(cfg: AppConfig, costs: CostModel) -> RiskEngine:
    return RiskEngine(cfg.risk, costs)


def failed(decision: object) -> set[str]:
    return {c.name for c in decision.failures}  # type: ignore[attr-defined]


def test_approves_clean_entry_and_sizes_by_risk(engine: RiskEngine) -> None:
    d = engine.evaluate(entry_intent(), risk_ctx())
    assert d.approved, d.summary()
    # 1% of 100k = 1000 risk / 40 per share = 25 shares; 20% cap = 20 shares.
    assert d.qty == 20
    assert d.binding_cap == "max_position_pct"


def test_risk_per_trade_binds_with_wide_stop(engine: RiskEngine) -> None:
    d = engine.evaluate(entry_intent(stop="900", target="1300"), risk_ctx())
    assert d.approved
    assert d.qty == 10  # 1000 / 100
    assert d.binding_cap == "risk_per_trade"


def test_size_multiplier_is_capped(engine: RiskEngine) -> None:
    big = engine.evaluate(entry_intent(stop="900", target="1300", mult=50), risk_ctx())
    assert big.qty == 12  # 1000 * 1.25 / 100, never more


@pytest.mark.parametrize(("over", "check"), [
    ({"mode": Mode.LIVE}, "mode"),
    ({"halted": True}, "halted"),
    ({"market_open": False}, "market_open"),
    ({"kill_switch_active": True}, "kill_switch"),
    ({"entry_blocks": ("daily_loss",)}, "breakers"),
    ({"in_entry_window": False}, "entry_window"),
    ({"tradable": False}, "tradable"),
    ({"blackout": True}, "blackout"),
    ({"idempotency_key_seen": True}, "duplicate"),
    ({"orders_last_minute": 5}, "order_rate_minute"),
    ({"orders_today": 20}, "order_rate_day"),
    ({"last_price_ts": NOW - timedelta(minutes=10)}, "data_fresh"),
    ({"last_price": None}, "data_fresh"),
    ({"prev_close": Decimal("800")}, "price_sane"),
    ({"last_price": Decimal("1030")}, "price_sane"),
    ({"max_correlation": 0.95}, "correlation"),
    ({"positions": {"INFY": pos_view(5, "5000")}}, "no_averaging"),
    ({"pending_entries": 5}, "max_open_positions"),
    ({"cash": Decimal("5000")}, "size"),
    ({"avg_traded_value": 1000.0}, "size"),
    ({"equity": Decimal(0)}, "equity_positive"),
])
def test_each_entry_check_rejects(engine: RiskEngine, over: dict[str, object],
                                  check: str) -> None:
    d = engine.evaluate(entry_intent(), risk_ctx(**over))
    assert not d.approved
    assert d.qty == 0
    assert check in failed(d), d.summary()


@pytest.mark.parametrize(("kwargs", "detail"), [
    ({"stop": None}, "no stop"),
    ({"stop": "1001"}, "not below"),
    ({"stop": "998"}, "too tight"),
    ({"stop": "700", "target": "2000"}, "too wide"),
])
def test_stop_rules(engine: RiskEngine, kwargs: dict[str, str | None], detail: str) -> None:
    d = engine.evaluate(entry_intent(**kwargs), risk_ctx())  # type: ignore[arg-type]
    assert not d.approved
    assert any(c.name == "stop" and detail in c.detail for c in d.failures)


def test_expected_edge_rejects_tiny_targets(engine: RiskEngine) -> None:
    d = engine.evaluate(entry_intent(target="1001"), risk_ctx())
    assert "expected_edge" in failed(d)
    d2 = engine.evaluate(entry_intent(target=None), risk_ctx())
    assert "expected_edge" in failed(d2)


def test_small_capital_dp_charges_block_trade(cfg: AppConfig, costs: CostModel) -> None:
    """At ₹5,000 the fixed DP charge can swamp the reward (docs §7)."""
    eng = RiskEngine(cfg.risk, costs)
    ctx = risk_ctx(equity=Decimal(5000), cash=Decimal(5000), last_price=Decimal("100"),
                   prev_close=Decimal("100"))
    d = eng.evaluate(entry_intent(ref="100", stop="97", target="101"), ctx)
    assert "expected_edge" in failed(d)


def test_sector_cap_limits_quantity(engine: RiskEngine) -> None:
    ctx = risk_ctx(positions={"TCS": pos_view(10, "30000"), "WIPRO": pos_view(10, "3000")})
    d = engine.evaluate(entry_intent(), ctx)
    assert d.approved
    assert d.qty == 2  # 35k sector cap - 33k held = 2k -> 2 shares
    assert d.binding_cap == "max_sector_pct"


def test_cash_buffer_and_reserved_cash(engine: RiskEngine) -> None:
    ctx = risk_ctx(cash=Decimal(15000), reserved_cash=Decimal(4000))
    d = engine.evaluate(entry_intent(), ctx)
    # free = 15000 - 4000 - 10000 buffer = 1000 -> 0 shares after cost buffer
    assert not d.approved and "size" in failed(d)


def test_entries_must_buy(engine: RiskEngine) -> None:
    i = entry_intent()
    i.side = Side.SELL
    assert "side" in failed(engine.evaluate(i, risk_ctx()))


def test_exit_allowed_with_kill_switch_and_breakers(engine: RiskEngine) -> None:
    ctx = risk_ctx(kill_switch_active=True, entry_blocks=("weekly_loss",), in_entry_window=False,
                   positions={"INFY": pos_view(7, "7000")})
    d = engine.evaluate(exit_intent(), ctx)
    assert d.approved and d.qty == 7


def test_exit_blocked_when_halted_unless_owner_flattens(engine: RiskEngine) -> None:
    held = {"INFY": pos_view(7, "7000")}
    assert not engine.evaluate(exit_intent(), risk_ctx(halted=True, positions=held)).approved
    d = engine.evaluate(exit_intent(), risk_ctx(halted=True, positions=held,
                                                flatten_requested=True))
    assert d.approved and d.qty == 7


def test_exit_needs_position_and_fresh_data(engine: RiskEngine) -> None:
    assert "has_position" in failed(engine.evaluate(exit_intent(), risk_ctx()))
    d = engine.evaluate(exit_intent(), risk_ctx(positions={"INFY": pos_view(1, "1000")},
                                                last_price_ts=NOW - timedelta(hours=1)))
    assert "data_fresh" in failed(d)


def test_exit_is_not_blocked_by_price_deviation(engine: RiskEngine) -> None:
    ctx = risk_ctx(positions={"INFY": pos_view(3, "3000")}, last_price=Decimal("950"),
                   prev_close=Decimal("1000"))
    assert engine.evaluate(exit_intent(ref="1000"), ctx).approved


def test_exit_must_sell(engine: RiskEngine) -> None:
    i = exit_intent()
    i.side = Side.BUY
    d = engine.evaluate(i, risk_ctx(positions={"INFY": pos_view(1, "1000")}))
    assert "side" in failed(d)


def test_invalid_reference_price(engine: RiskEngine) -> None:
    d = engine.evaluate(entry_intent(ref="0", stop="-1"), risk_ctx())
    assert not d.approved


def test_summary_text(engine: RiskEngine) -> None:
    ok = engine.evaluate(entry_intent(), risk_ctx())
    assert ok.summary().startswith("approved")
    bad = engine.evaluate(entry_intent(), risk_ctx(market_open=False))
    assert "market_open" in bad.summary()
