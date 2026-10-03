"""Property tests: no input combination makes the Risk Engine exceed a limit."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from algotrade.brokers.costs import CostModel
from algotrade.config.models import default_config
from algotrade.risk.engine import PositionView, RiskEngine
from tests.conftest import entry_intent, exit_intent, risk_ctx

CFG = default_config()
ENGINE = RiskEngine(CFG.risk, CostModel(CFG.strategy.costs))
SECTORS = ["IT", "Banks", "Pharma", "Auto"]

money = st.integers(min_value=1_000, max_value=5_000_000)
price = st.decimals(min_value=Decimal("5"), max_value=Decimal("20000"), places=2)


@st.composite
def portfolios(draw: st.DrawFn) -> dict[str, PositionView]:
    n = draw(st.integers(0, 6))
    out = {}
    for i in range(n):
        out[f"S{i}"] = PositionView(draw(st.integers(1, 500)),
                                    Decimal(draw(st.integers(0, 400_000))),
                                    draw(st.sampled_from(SECTORS)))
    return out


@settings(max_examples=400, deadline=None)
@given(equity=money, cash_frac=st.floats(0, 1), ltp=price,
       stop_pct=st.floats(0.1, 30), rr=st.floats(0.1, 6), mult=st.floats(0, 5),
       positions=portfolios(), sector=st.sampled_from(SECTORS),
       adv=st.floats(0, 1e10), reserved=st.integers(0, 100_000))
def test_approved_entries_respect_every_cap(equity: int, cash_frac: float, ltp: Decimal,
                                            stop_pct: float, rr: float, mult: float,
                                            positions: dict[str, PositionView], sector: str,
                                            adv: float, reserved: int) -> None:
    eq = Decimal(equity)
    cash = (eq * Decimal(str(cash_frac))).quantize(Decimal(1))
    stop = (ltp * (1 - Decimal(str(stop_pct)) / 100)).quantize(Decimal("0.05"))
    target = (ltp + (ltp - stop) * Decimal(str(rr))).quantize(Decimal("0.05"))
    intent = entry_intent(ref=str(ltp), stop=str(stop), target=str(target), mult=mult)
    ctx = risk_ctx(equity=eq, cash=cash, last_price=ltp, prev_close=ltp, positions=positions,
                   sector=sector, avg_traded_value=adv, reserved_cash=Decimal(reserved))
    d = ENGINE.evaluate(intent, ctx)
    rc = CFG.risk
    if not d.approved:
        assert d.qty == 0
        return
    value = ltp * d.qty
    assert d.qty > 0
    assert value <= eq * Decimal(str(rc.max_position_pct)) / 100
    assert value <= rc.max_order_value
    sector_now = sum((p.market_value for p in positions.values() if p.sector == sector),
                     Decimal(0))
    assert sector_now + value <= eq * Decimal(str(rc.max_sector_pct)) / 100
    free = cash - Decimal(reserved) - eq * Decimal(str(rc.min_cash_buffer_pct)) / 100
    assert value <= free
    risk_taken = (ltp - stop) * d.qty
    assert risk_taken <= eq * Decimal(str(rc.risk_per_trade_pct)) / 100 * Decimal("1.25")
    assert value <= Decimal(str(adv)) * Decimal(str(rc.max_adv_participation_pct)) / 100
    assert len(positions) < rc.max_open_positions
    assert "INFY" not in positions


@settings(max_examples=200, deadline=None)
@given(halted=st.booleans(), kill=st.booleans(), blocks=st.lists(st.sampled_from(
    ["daily_loss", "weekly_loss", "stale_data"]), max_size=3), open_=st.booleans())
def test_no_entry_approved_under_any_block(halted: bool, kill: bool, blocks: list[str],
                                           open_: bool) -> None:
    ctx = risk_ctx(halted=halted, kill_switch_active=kill, entry_blocks=tuple(blocks),
                   market_open=open_)
    d = ENGINE.evaluate(entry_intent(), ctx)
    if halted or kill or blocks or not open_:
        assert not d.approved


@settings(max_examples=200, deadline=None)
@given(qty=st.integers(1, 10_000), kill=st.booleans(), blocks=st.booleans())
def test_exit_quantity_is_exactly_the_position(qty: int, kill: bool, blocks: bool) -> None:
    ctx = risk_ctx(kill_switch_active=kill, entry_blocks=("daily_loss",) if blocks else (),
                   positions={"INFY": PositionView(qty, Decimal(qty * 1000), "IT")})
    d = ENGINE.evaluate(exit_intent(), ctx)
    assert d.approved and d.qty == qty
