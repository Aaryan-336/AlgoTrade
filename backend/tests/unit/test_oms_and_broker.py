from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from algotrade.brokers.base import BrokerError
from algotrade.brokers.costs import CostModel
from algotrade.brokers.paper import PaperBroker, round_to_tick
from algotrade.config.models import AppConfig, CostConfig, PaperBrokerConfig
from algotrade.core.types import BrokerAck, Mode, Order, OrderState, OrderType, Side
from algotrade.db.repository import Repository
from algotrade.oms.manager import OrderManager
from algotrade.oms.state_machine import TRANSITIONS, IllegalTransition, check_transition
from algotrade.risk.engine import RiskDecision
from tests.conftest import NOW, SESSION_DAY, entry_intent, exit_intent, ist


def run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def approved(qty: int) -> RiskDecision:
    return RiskDecision(True, qty, [])


# ------------------------------------------------------------------ states
def test_every_listed_transition_is_allowed_and_others_raise() -> None:
    for frm, allowed in TRANSITIONS.items():
        for to in OrderState:
            if to in allowed:
                check_transition(frm, to)
            else:
                with pytest.raises(IllegalTransition):
                    check_transition(frm, to)


# ------------------------------------------------------------------- costs
def test_delivery_costs_match_hand_calculation() -> None:
    c = CostModel(CostConfig())
    buy = c.charges(Side.BUY, 10, Decimal("1000"))
    assert buy.stt == Decimal("10.00")  # 0.1% of 10,000
    assert buy.stamp_duty == Decimal("1.50")
    assert buy.dp == 0
    sell = c.charges(Side.SELL, 10, Decimal("1000"))
    assert sell.dp == Decimal("15.93") and sell.stamp_duty == 0
    assert c.charges(Side.SELL, 10, Decimal("1000"), dp_applies=False).dp == 0
    assert c.round_trip(10, Decimal("1000"), Decimal("1000")) == buy.total + sell.total


# ------------------------------------------------------------------ broker
def test_tick_rounding() -> None:
    assert round_to_tick(Decimal("100.01"), up=True) == Decimal("100.05")
    assert round_to_tick(Decimal("100.04"), up=False) == Decimal("100.00")


def _order(side: Side = Side.BUY, qty: int = 10, otype: OrderType = OrderType.MARKET,
           trigger: str | None = None, limit: str | None = None, oid: str = "C1") -> Order:
    return Order(oid, f"k{oid}", Mode.PAPER, "INFY", side, qty, otype, OrderState.SUBMITTED,
                 NOW, NOW, "entry", trigger_price=Decimal(trigger) if trigger else None,
                 limit_price=Decimal(limit) if limit else None)


def test_market_buy_pays_slippage_and_costs(broker: PaperBroker) -> None:
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    ack = run(broker.place(_order()))
    assert ack.accepted
    fills = broker.on_market("INFY", NOW + timedelta(seconds=5), *([Decimal("1000")] * 4))
    assert len(fills) == 1
    f = fills[0]
    assert f.price == Decimal("1001.00")  # 10 bps pessimistic
    assert broker.cash == Decimal(100000) - f.price * 10 - f.charges.total
    assert run(broker.positions())[0].qty == 10


def test_no_fill_before_placement_time(broker: PaperBroker) -> None:
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    o = _order()
    o.updated_at = NOW + timedelta(minutes=1)
    run(broker.place(o))
    assert broker.on_market("INFY", NOW, *([Decimal("1000")] * 4)) == []


def test_rejects_short_selling_and_insufficient_funds(broker: PaperBroker) -> None:
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    assert not run(broker.place(_order(Side.SELL))).accepted
    assert not run(broker.place(_order(qty=1000))).accepted
    assert not run(broker.place(_order(qty=0))).accepted


def test_no_price_means_no_buy(broker: PaperBroker) -> None:
    ack = run(broker.place(_order()))
    assert not ack.accepted and "no market price" in ack.message


def _hold(broker: PaperBroker, qty: int = 10) -> None:
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    run(broker.place(_order(qty=qty, oid="B")))
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))


def test_stop_gap_fills_at_open(broker: PaperBroker) -> None:
    _hold(broker)
    run(broker.place(_order(Side.SELL, otype=OrderType.STOP, trigger="950", oid="S")))
    day2 = ist(SESSION_DAY + timedelta(days=1), 9, 15)
    fills = broker.on_market("INFY", day2 + timedelta(hours=6), Decimal("900"), Decimal("920"),
                             Decimal("880"), Decimal("910"), session_start=day2)
    assert fills and fills[0].price < Decimal("900")  # gap fill, not the stop price


def test_stop_intraday_fills_near_trigger(broker: PaperBroker) -> None:
    _hold(broker)
    run(broker.place(_order(Side.SELL, otype=OrderType.STOP, trigger="950", oid="S")))
    fills = broker.on_market("INFY", NOW + timedelta(hours=1), Decimal("990"), Decimal("995"),
                             Decimal("940"), Decimal("960"))
    assert fills and Decimal("948") < fills[0].price <= Decimal("950")


def test_limit_orders_need_trade_through(broker: PaperBroker) -> None:
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    run(broker.place(_order(otype=OrderType.LIMIT, limit="990")))
    later = NOW + timedelta(minutes=1)
    assert broker.on_market("INFY", later, Decimal("995"), Decimal("999"), Decimal("990"),
                            Decimal("995")) == []
    f = broker.on_market("INFY", later, Decimal("995"), Decimal("999"), Decimal("985"),
                         Decimal("988"))
    assert f and f[0].price == Decimal("990")


def test_partial_fill_on_thin_volume(broker: PaperBroker) -> None:
    broker.on_market("INFY", NOW, *([Decimal("100")] * 4))
    run(broker.place(_order(qty=50)))
    f = broker.on_market("INFY", NOW, *([Decimal("100")] * 4), volume=100)
    assert f[0].qty == 10
    assert broker.working  # rest still working


def test_dp_charged_once_per_symbol_per_day(broker: PaperBroker) -> None:
    _hold(broker, qty=20)
    run(broker.place(_order(Side.SELL, qty=10, oid="S1")))
    f1 = broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    run(broker.place(_order(Side.SELL, qty=10, oid="S2")))
    f2 = broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    assert f1[0].charges.dp > 0 and f2[0].charges.dp == 0


def test_cancel_modify_and_persistence(cfg: AppConfig, costs: CostModel) -> None:
    saved: dict[str, object] = {}
    b = PaperBroker(cfg.strategy.paper_broker, costs, Decimal(100000),
                    persist=lambda s: saved.update(s))
    b.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    run(b.place(_order(oid="B")))
    b.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    ack = run(b.place(_order(Side.SELL, otype=OrderType.STOP, trigger="950", oid="S")))
    assert run(b.modify_trigger(ack.broker_order_id, "970"))
    assert not run(b.modify_trigger("nope", "1"))
    restored = PaperBroker(cfg.strategy.paper_broker, costs, Decimal(0), state=saved)
    assert restored.cash == b.cash
    assert run(restored.find_order("S")) == ack.broker_order_id
    assert next(iter(restored.working.values())).trigger_price == Decimal("970")
    assert run(b.cancel(ack.broker_order_id))
    assert not run(b.cancel(ack.broker_order_id))


def test_random_rejects_are_seeded(costs: CostModel) -> None:
    b = PaperBroker(PaperBrokerConfig(random_reject_prob=0.5, seed=1), costs, Decimal(10**6))
    b.on_market("INFY", NOW, *([Decimal("100")] * 4))
    results = [run(b.place(_order(oid=f"R{i}"))).accepted for i in range(20)]
    assert not all(results) and any(results)


@settings(max_examples=150, deadline=None)
@given(prices=st.lists(st.decimals(Decimal("50"), Decimal("200"), places=2), min_size=2,
                       max_size=30), buys=st.lists(st.integers(1, 30), min_size=1, max_size=5))
def test_broker_never_overdraws_or_shorts(prices: list[Decimal], buys: list[int]) -> None:
    b = PaperBroker(PaperBrokerConfig(), CostModel(CostConfig()), Decimal(5000))
    for i, p in enumerate(prices):
        t = NOW + timedelta(minutes=i)
        b.on_market("INFY", t, p, p, p, p)
        if i < len(buys):
            o = _order(qty=buys[i], oid=f"B{i}")
            o.updated_at = t
            run(b.place(o))
        held = sum(h.qty for h in b.holdings.values())
        if held and i % 3 == 0:
            o = _order(Side.SELL, qty=held + 1, oid=f"S{i}")
            o.updated_at = t
            assert not run(b.place(o)).accepted
        assert b.cash >= 0
        assert all(h.qty > 0 for h in b.holdings.values())


# --------------------------------------------------------------------- OMS
def test_oms_happy_path_and_audit(repo: Repository, broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER, repo)
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    o = oms.create(entry_intent(), approved(5), NOW)
    assert o.state is OrderState.RISK_APPROVED
    run(oms.submit(o, NOW))
    assert o.state is OrderState.ACKED
    fills = broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    oms.on_fill(fills[0], NOW)
    assert o.state is OrderState.FILLED and o.avg_fill_price == fills[0].price
    events = [e.to_state for e in repo.order_events(o.id)]
    assert events == ["RISK_APPROVED", "SUBMITTED", "ACKED", "FILLED"]
    assert len(repo.fills()) == 1


def test_oms_rejected_intent_never_reaches_broker(repo: Repository, broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER, repo)
    o = oms.create(entry_intent(), RiskDecision(False, 0, []), NOW)
    assert o.state is OrderState.REJECTED
    with pytest.raises(RuntimeError):
        run(oms.submit(o, NOW))
    assert broker.working == {}


def test_oms_duplicate_intent_creates_one_order(repo: Repository, broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER, repo)
    first = oms.create(entry_intent(), approved(5), NOW)
    second = oms.create(entry_intent(), approved(5), NOW)
    assert first.state is OrderState.RISK_APPROVED
    assert second.state is OrderState.REJECTED and second.reason == "duplicate intent"
    # Survives restart: a new OMS sees the key in the database.
    oms2 = OrderManager(broker, Mode.PAPER, repo)
    assert oms2.create(entry_intent(), approved(5), NOW).state is OrderState.REJECTED


class FlakyBroker(PaperBroker):
    """Raises on place but may or may not have accepted the order."""

    def __init__(self, *a: object, accepted: bool, **k: object) -> None:
        super().__init__(*a, **k)  # type: ignore[arg-type]
        self.accepted = accepted
        self.place_calls = 0

    async def place(self, order: Order) -> BrokerAck:
        self.place_calls += 1
        if self.accepted:
            await super().place(order)
        raise BrokerError("timeout")


@pytest.mark.parametrize("accepted", [True, False])
def test_uncertain_placement_queries_broker_never_resends(cfg: AppConfig, costs: CostModel,
                                                          accepted: bool) -> None:
    b = FlakyBroker(cfg.strategy.paper_broker, costs, Decimal(100000), accepted=accepted)
    b.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    oms = OrderManager(b, Mode.PAPER)
    o = oms.create(entry_intent(), approved(5), NOW)
    run(oms.submit(o, NOW))
    assert b.place_calls == 1
    assert o.state is (OrderState.ACKED if accepted else OrderState.FAILED)
    assert oms.broker_errors == 1


def test_broker_reject_marks_failed(repo: Repository, broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER, repo)
    o = oms.create(entry_intent(), approved(5), NOW)
    run(oms.submit(o, NOW))  # no market price -> broker rejects
    assert o.state is OrderState.FAILED and oms.broker_rejects


def test_protective_stop_resize_cancel_and_modify(broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER)
    broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))
    parent = oms.create(entry_intent(), approved(10), NOW)
    run(oms.submit(parent, NOW))
    oms.on_fill(broker.on_market("INFY", NOW, *([Decimal("1000")] * 4))[0], NOW)
    s1 = run(oms.place_protective_stop("INFY", 10, Decimal("960"), parent, NOW))
    assert s1 is not None and s1.state is OrderState.ACKED
    assert run(oms.place_protective_stop("INFY", 10, Decimal("960"), parent, NOW)) is s1
    assert run(oms.modify_stop(s1, Decimal("975"), NOW)) and s1.trigger_price == Decimal("975")
    s2 = run(oms.place_protective_stop("INFY", 8, Decimal("975"), parent, NOW))
    assert s2 is not s1 and s1.state is OrderState.CANCELLED
    assert run(oms.cancel(s2, NOW)) and not run(oms.cancel(s2, NOW))
    assert not run(oms.modify_stop(s2, Decimal("990"), NOW))


def test_exit_order_purpose(broker: PaperBroker) -> None:
    oms = OrderManager(broker, Mode.PAPER)
    o = oms.create(exit_intent(), approved(3), NOW)
    assert o.purpose == "exit" and o.side is Side.SELL
