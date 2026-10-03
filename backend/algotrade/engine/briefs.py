"""Phone-friendly messages: a morning readiness check, an after-close summary,
and alerts when the bot's health changes during market hours. Sent through
the Notifier (Telegram when configured). Texts never contain secrets."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from algotrade.core.clock import to_ist
from algotrade.engine.alerts import Level
from algotrade.engine.insights import bot_health, market_insights

if TYPE_CHECKING:
    from algotrade.engine.runner import LiveRunner
    from algotrade.engine.runtime import Runtime


def _inr(x: Any) -> str:
    v = float(x)
    return f"{'-' if v < 0 else ''}₹{abs(v):,.0f}"


def _signed(x: Any) -> str:
    v = float(x)
    return f"{'+' if v >= 0 else '-'}₹{abs(v):,.0f}"


def _market_line(rt: Runtime, now: datetime) -> str | None:
    m = market_insights(rt, now)
    parts = []
    if idx := m["index"]:
        ch = f" ({idx['change_pct']:+.2f}%)" if idx["change_pct"] is not None else ""
        trend = ""
        if idx["above_ema"] is not None:
            trend = (", above 200-day avg: buys allowed" if idx["above_ema"]
                     else ", below 200-day avg: new buys paused")
        parts.append(f"Nifty {idx['last']:,.0f}{ch}{trend}")
    if vix := m["vix"]:
        parts.append(f"VIX {vix['last']:.1f}" + (" (high: half-size buys)" if vix["elevated"]
                                                 else ""))
    if b := m["breadth"]:
        parts.append(f"breadth {b['advancers']}:{b['decliners']}")
    return " · ".join(parts) or None


def _plans(rt: Runtime) -> str:
    pend = rt.engine.pending
    if not pend:
        return "No trades queued."
    return "Queued: " + ", ".join(f"{i.side.value} {i.symbol}" for i in pend[:8]) + \
        (f" +{len(pend) - 8} more" if len(pend) > 8 else "")


def morning_brief(rt: Runtime, runner: LiveRunner, now: datetime) -> tuple[Level, str]:
    eng = rt.engine
    h = bot_health(rt, runner, now)
    head = {"stopped": "NOT READY", "degraded": "Ready, with a problem"}.get(h["verdict"],
                                                                            "Ready for today")
    lines = [f"Good morning · {to_ist(now):%a %d %b}", f"{head}."]
    if h["verdict"] in ("stopped", "degraded"):
        lines.append(h["headline"])
    lines.append(f"Equity {_inr(eng.equity())} · {len(eng.portfolio.positions)} open positions.")
    lines.append(_plans(rt) + (" Placed from 09:20 after the final risk check."
                               if eng.pending else ""))
    if mk := _market_line(rt, now):
        lines.append(f"Last close: {mk}.")
    level: Level = "info"
    if h["verdict"] == "stopped":
        level = "critical"
    elif h["verdict"] == "degraded":
        level = "warning"
    return level, "\n".join(lines)


def evening_brief(rt: Runtime, now: datetime) -> str:
    eng = rt.engine
    st = eng.status(now)
    today = to_ist(now).date()
    day_pnl, total = st["day_pnl"], st["total_pnl"]
    base = float(st["equity"]) - float(day_pnl)
    day_pct = float(day_pnl) / base * 100 if base else 0.0
    lines = [f"Day summary · {to_ist(now):%a %d %b}",
             f"Equity {_inr(st['equity'])} · today {_signed(day_pnl)} ({day_pct:+.2f}%) · "
             f"total {_signed(total)}."]
    closed = [t for t in eng.portfolio.closed_trades if to_ist(t.closed_at).date() == today]
    bought = {p.symbol for p in eng.portfolio.positions.values()
              if to_ist(p.opened_at).date() == today}
    bought |= {t.symbol for t in closed if to_ist(t.opened_at).date() == today}
    if bought:
        lines.append("Bought: " + ", ".join(sorted(bought)) + ".")
    if closed:
        lines.append("Sold: " + ", ".join(f"{t.symbol} {_signed(t.pnl)}" for t in closed) + ".")
    if not bought and not closed:
        lines.append("No trades today.")
    lines.append(f"Holding {len(eng.portfolio.positions)} positions, all with stop-losses."
                 if all(eng.oms.protective_stop(s) for s in eng.portfolio.positions)
                 else f"Holding {len(eng.portfolio.positions)} positions. Some have no live stop: "
                      "check the dashboard.")
    if st["breakers"]:
        lines.append("Breakers tripped: " + ", ".join(st["breakers"]) + ".")
    lines.append("Tomorrow: " + _plans(rt))
    if mk := _market_line(rt, now):
        lines.append(f"Market: {mk}.")
    return "\n".join(lines)


class HealthWatch:
    """Alert once when the bot stops working during market hours, and once when it
    recovers. A state must hold for two checks in a row, so blips stay quiet."""

    def __init__(self) -> None:
        self.reported = "active"
        self._candidate: str | None = None

    def check(self, rt: Runtime, runner: LiveRunner, now: datetime) -> None:
        h = bot_health(rt, runner, now)
        verdict = "ok" if h["verdict"] in ("active", "waiting") else h["verdict"]
        prev = "ok" if self.reported in ("active", "waiting") else self.reported
        if verdict == prev:
            self._candidate = None
            return
        if self._candidate != verdict:
            self._candidate = verdict
            return
        self._candidate = None
        self.reported = verdict
        if verdict == "stopped":
            rt.notifier.send(now, "critical", f"Bot is NOT trading: {h['headline']}")
        elif verdict == "degraded":
            rt.notifier.send(now, "warning", f"Bot needs attention: {h['headline']}")
        else:
            rt.notifier.send(now, "info", "Bot is working normally again.")
