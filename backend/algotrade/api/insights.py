"""Read-only views for the Overview page: market insights, bot health and a
plain-language activity feed. Nothing here changes engine state."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal

from algotrade.core.clock import ist_datetime, to_ist
from algotrade.features.indicators import ema, is_valid

if TYPE_CHECKING:
    from algotrade.engine.core import TradingEngine
    from algotrade.engine.runner import LiveRunner
    from algotrade.engine.runtime import Runtime

State = Literal["ok", "idle", "warn", "error"]
SEVERITY: dict[str, int] = {"ok": 0, "idle": 0, "warn": 1, "error": 2}
DAILY_CYCLE_AT = time(15, 40)


# ------------------------------------------------------------------ prices
def last_and_prev(eng: TradingEngine, symbol: str) -> tuple[Decimal | None, Decimal | None]:
    """Latest price and the close it should be compared with.

    During the session the latest daily bar is yesterday's, so a live price is
    compared with it. After the close today's bar is in, so compare the last
    two bars."""
    bars = eng.daily.get(symbol, [])
    live = eng.prices.get(symbol)
    live_ts = eng.price_ts.get(symbol)
    if not bars:
        return live, None
    last_bar = bars[-1]
    if live is not None and live_ts is not None and \
            to_ist(live_ts).date() > to_ist(last_bar.ts).date():
        return live, last_bar.close
    prev = bars[-2].close if len(bars) > 1 else None
    return last_bar.close, prev


def change_pct(last: Decimal | None, prev: Decimal | None) -> float | None:
    if last is None or not prev:
        return None
    return float((last / prev - 1) * 100)


# ---------------------------------------------------------------- market
def market_insights(rt: Runtime, now: datetime) -> dict[str, Any]:
    eng = rt.engine
    rc = rt.config.strategy.regime
    out: dict[str, Any] = {"index": None, "vix": None, "breadth": None, "sectors": [],
                           "gainers": [], "losers": [], "sentiment": None}

    idx_bars = eng.daily.get(rc.index_symbol, [])
    if idx_bars:
        closes = [float(b.close) for b in idx_bars]
        last, prev = last_and_prev(eng, rc.index_symbol)
        e = ema(closes, rc.index_ema)[-1] if len(closes) >= rc.index_ema else float("nan")
        level = float(last) if last is not None else closes[-1]
        ref20 = closes[-21] if len(closes) > 20 else None
        out["index"] = {
            "symbol": rc.index_symbol, "last": level, "change_pct": change_pct(last, prev),
            "ema": e if is_valid(e) else None, "ema_period": rc.index_ema,
            "above_ema": level > e if is_valid(e) else None,
            "ema_gap_pct": (level / e - 1) * 100 if is_valid(e) and e else None,
            "change_20d_pct": (level / ref20 - 1) * 100 if ref20 else None,
            "spark": [{"t": int(b.ts.timestamp()), "v": float(b.close)} for b in idx_bars[-90:]],
            "live": eng.price_ts.get(rc.index_symbol) is not None
            and to_ist(eng.price_ts[rc.index_symbol]).date() == to_ist(now).date(),
        }
    vix_bars = eng.daily.get(rc.vix_symbol, [])
    if vix_bars:
        last, prev = last_and_prev(eng, rc.vix_symbol)
        level = float(last) if last is not None else float(vix_bars[-1].close)
        out["vix"] = {"last": level, "change_pct": change_pct(last, prev),
                      "reduce_above": rc.vix_reduce_above,
                      "elevated": level > rc.vix_reduce_above}

    rows: list[dict[str, Any]] = []
    above50 = with50 = highs = 0
    for inst in rt.universe.instruments():
        bars = eng.daily.get(inst.symbol, [])
        if not bars:
            continue
        last, prev = last_and_prev(eng, inst.symbol)
        ch = change_pct(last, prev)
        closes = [float(b.close) for b in bars]
        price = float(last) if last is not None else closes[-1]
        if len(closes) >= 50:
            e50 = ema(closes, 50)[-1]
            if is_valid(e50):
                with50 += 1
                above50 += price > e50
        if len(closes) > 20 and price >= max(float(b.high) for b in bars[-21:-1]):
            highs += 1
        rows.append({"symbol": inst.symbol, "name": inst.name, "sector": inst.sector,
                     "ltp": price, "change_pct": ch,
                     "held": inst.symbol in eng.portfolio.positions})
    priced = [r for r in rows if r["change_pct"] is not None]
    if priced:
        adv = sum(1 for r in priced if r["change_pct"] > 0.05)
        dec = sum(1 for r in priced if r["change_pct"] < -0.05)
        out["breadth"] = {"advancers": adv, "decliners": dec,
                          "unchanged": len(priced) - adv - dec, "total": len(priced),
                          "pct_above_50ema": above50 / with50 * 100 if with50 else None,
                          "at_20d_high": highs}
        by_sector: dict[str, list[float]] = {}
        for r in priced:
            by_sector.setdefault(r["sector"], []).append(r["change_pct"])
        out["sectors"] = sorted(
            ({"sector": s, "change_pct": sum(v) / len(v), "count": len(v)}
             for s, v in by_sector.items()), key=lambda x: -x["change_pct"])
        ranked = sorted(priced, key=lambda r: -r["change_pct"])
        out["gainers"] = [r for r in ranked[:5] if r["change_pct"] > 0]
        out["losers"] = [r for r in reversed(ranked[-5:]) if r["change_pct"] < 0]

    scored = [(s, a) for s, a in eng.sentiment.items() if not a.missing]
    if scored:
        avg = sum(a.score for _, a in scored) / len(scored)
        ordered = sorted(scored, key=lambda x: -x[1].score)

        def row(sym: str, agg: Any) -> dict[str, Any]:
            return {"symbol": sym, "score": agg.score, "news": agg.n_items}

        out["sentiment"] = {
            "average": avg, "covered": len(scored),
            "positive": sum(1 for _, a in scored if a.score > 0.1),
            "negative": sum(1 for _, a in scored if a.score < -0.1),
            "most_positive": [row(s, a) for s, a in ordered[:3] if a.score > 0],
            "most_negative": [row(s, a) for s, a in reversed(ordered[-3:]) if a.score < 0],
            "vetoed": sorted(s for s, a in eng.sentiment.items() if a.negative_material),
        }
    bar_ts = eng.latest_bar_ts()
    out["as_of"] = {"bar_date": to_ist(bar_ts).date().isoformat() if bar_ts else None,
                    "live": rt.calendar.is_open(now) and bool(eng.price_ts)}
    return out


# ---------------------------------------------------------------- health
def _ago(now: datetime, ts: datetime | None) -> str:
    if ts is None:
        return "never"
    s = max(0, int((now - ts).total_seconds()))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60} min ago"
    if s < 86400:
        return f"{s // 3600} h ago"
    return f"{s // 86400} d ago"


def _fmt(ts: datetime, now: datetime) -> str:
    local, today = to_ist(ts), to_ist(now).date()
    if local.date() == today:
        return local.strftime("%H:%M")
    if local.date() == today + timedelta(days=1):
        return local.strftime("tomorrow %H:%M")
    return local.strftime("%a %d %b %H:%M")


def _next_session_time(rt: Runtime, now: datetime, at: time) -> datetime:
    """The next time ``at`` IST falls on a trading day, from ``now``."""
    cal = rt.calendar
    d: date = to_ist(now).date()
    if not cal.is_trading_day(d) or to_ist(now).time() >= at:
        d = cal.next_trading_day(d)
    return ist_datetime(d, at)


def _step(key: str, label: str, state: State, detail: str, last: datetime | None = None,
          nxt: datetime | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "state": state, "detail": detail,
            "last": last, "next": nxt}


def bot_health(rt: Runtime, runner: LiveRunner, now: datetime) -> dict[str, Any]:
    """Each stage of the pipeline with a plain status, plus one overall verdict."""
    eng = rt.engine
    cal = rt.calendar
    market_open = cal.is_open(now)
    steps: list[dict[str, Any]] = []
    poll = rt.settings.poll_interval_sec

    # 1. Engine loop
    hb = rt.repo.get_state("engine_heartbeat") or {}
    hb_ts = datetime.fromisoformat(hb["ts"]) if hb.get("ts") else None
    if not runner.running:
        steps.append(_step("engine", "Engine loop", "error",
                           "Stopped. Start it from Settings or restart the API.", hb_ts))
    elif hb_ts is None or (now - hb_ts).total_seconds() > max(3 * poll, 30):
        steps.append(_step("engine", "Engine loop", "error",
                           f"Running but its heartbeat is late (last {_ago(now, hb_ts)}).",
                           hb_ts))
    else:
        detail = f"Running, checks every {poll:g}s."
        if runner.last_error:
            detail += f" Last error: {runner.last_error[:120]}"
        steps.append(_step("engine", "Engine loop", "warn" if runner.last_error else "ok",
                           detail, hb_ts))

    # 2. Live prices
    health = rt.provider.health()
    ready = rt.provider.is_ready()
    if not ready:
        msg = ("Upstox is not logged in, so there are no live prices and no trades."
               if health.name == "upstox" else f"{health.name}: {health.message or 'not ready'}")
        steps.append(_step("prices", "Live prices", "error", msg, health.last_update))
    elif market_open:
        fresh = [ts for ts in eng.price_ts.values() if (now - ts).total_seconds() < 120]
        age = (now - health.last_update).total_seconds() if health.last_update else None
        if age is not None and age < max(6 * poll, 60):
            steps.append(_step("prices", "Live prices", "ok",
                               f"{runner.last_tick_count} prices in the last poll · "
                               f"{len(fresh)} symbols fresh.", health.last_update))
        else:
            steps.append(_step("prices", "Live prices", "warn",
                               f"No fresh prices for {_ago(now, health.last_update)}.",
                               health.last_update))
    else:
        steps.append(_step("prices", "Live prices", "idle",
                           f"{health.name} connected. Market closed; prices resume at 09:15.",
                           health.last_update,
                           _next_session_time(rt, now, time(9, 15))))

    # 3. Price history
    have = sum(1 for s in rt.universe.symbols if eng.daily.get(s))
    total = len(rt.universe.symbols)
    bar_ts = eng.latest_bar_ts()
    if have == 0:
        steps.append(_step("history", "Price history", "error" if ready else "warn",
                           "Not loaded yet." + ("" if ready else " Log in to Upstox; "
                                                "delayed Yahoo history loads meanwhile.")))
    else:
        src = runner.history_source or "database"
        bar_day = to_ist(bar_ts).strftime("%d %b") if bar_ts else "—"
        steps.append(_step("history", "Price history",
                           "ok" if have >= total * 0.9 else "warn",
                           f"{have}/{total} stocks, latest close {bar_day} · source {src}."))

    # 4. News and sentiment
    sc = rt.config.strategy.sentiment
    interval = timedelta(minutes=rt.settings.news_interval_min)
    if not sc.enabled:
        steps.append(_step("news", "News & sentiment", "idle", "Turned off in the strategy."))
    else:
        last = rt.news.last_run
        nxt = (last + interval) if last else None
        counts = rt.news.last_counts
        parts = [f"{counts.get('added', 0)} new headlines, {counts.get('scored', 0)} scored"] \
            if last else ["First scan pending"]
        state: State = "ok"
        if not rt.llm.configured:
            state, parts = "warn", [*parts, "Groq off: news counts as neutral"]
        elif rt.llm.last_error:
            state, parts = "warn", [*parts, f"Groq: {rt.llm.last_error[:80]}"]
        if last and now - last > interval * 3:
            state = "warn"
        steps.append(_step("news", "News & sentiment", state, " · ".join(parts) + ".",
                           last, nxt))

    # 5. Decision cycle
    nxt_cycle = _next_session_time(rt, now, DAILY_CYCLE_AT)
    if rt.config.strategy.timeframe != "1d":
        nxt_cycle = now + timedelta(minutes=15 - to_ist(now).minute % 15)
    if eng.last_cycle is None:
        steps.append(_step("decision", "Decision cycle", "warn" if have else "idle",
                           "No cycle yet. It runs after history loads, then daily at 15:40.",
                           None, nxt_cycle))
    else:
        buys = sum(1 for i in eng.pending if i.side.value == "BUY")
        sells = len(eng.pending) - buys
        bar_day = to_ist(eng.last_bar_ts).strftime("%d %b") if eng.last_bar_ts else "—"
        candidates = sum(1 for r in eng.ranking if r.get("status") == "buy_candidate")
        detail = (f"Scored {len(eng.ranking)} stocks on the {bar_day} close · "
                  f"{candidates} candidates · planned {buys} buys, {sells} sells.")
        stale = bar_ts is not None and eng.last_bar_ts is not None and bar_ts > eng.last_bar_ts
        steps.append(_step("decision", "Decision cycle", "warn" if stale else "ok",
                           detail + (" A newer close is waiting to be analysed." if stale
                                     else ""), eng.last_cycle, nxt_cycle))

    # 6. Execution
    rc = rt.config.risk
    entry_at = (datetime.combine(date.min, time(9, 15))
                + timedelta(minutes=rc.no_entry_first_minutes)).time()
    in_window = market_open and cal.in_entry_window(now, rc.no_entry_first_minutes,
                                                    rc.no_entry_last_minutes)
    orders = rt.repo.orders(1)
    last_order = orders[0].created_at if orders else None
    if eng.pending:
        names = ", ".join(f"{i.side.value} {i.symbol}" for i in eng.pending[:4])
        more = f" +{len(eng.pending) - 4}" if len(eng.pending) > 4 else ""
        if in_window:
            steps.append(_step("execution", "Order execution", "ok",
                               f"Working on {names}{more}: waiting for fresh prices and the "
                               "final risk check.", last_order))
        else:
            steps.append(_step("execution", "Order execution", "idle",
                               f"{len(eng.pending)} queued ({names}{more}). Placed after a "
                               "final risk check when the entry window opens.", last_order,
                               now if market_open else _next_session_time(rt, now, entry_at)))
    else:
        steps.append(_step("execution", "Order execution", "idle",
                           "Nothing queued. New plans come from the next decision cycle.",
                           last_order))

    # 7. Safety
    tripped = eng.breakers.to_state()
    if eng.kill.active:
        steps.append(_step("safety", "Risk & safety", "error",
                           f"Kill switch on ({eng.kill.action}): {eng.kill.reason}", eng.kill.ts))
    elif tripped:
        halt = any(b["severity"] == "halt" for b in tripped.values())
        steps.append(_step("safety", "Risk & safety", "error" if halt else "warn",
                           "Breaker tripped: " + ", ".join(n.replace("_", " ") for n in tripped)
                           + (" (trading halted)." if halt else " (new buys blocked).")))
    else:
        n = len(eng.portfolio.positions)
        stops = sum(1 for s in eng.portfolio.positions if eng.oms.protective_stop(s))
        steps.append(_step("safety", "Risk & safety", "warn" if stops < n else "ok",
                           f"No breakers tripped · {stops}/{n} positions have a live stop."))

    worst = max(steps, key=lambda s: SEVERITY[s["state"]])
    # Next thing the bot will do on its own.
    upcoming = sorted((s for s in steps if s["next"] is not None and s["next"] > now),
                      key=lambda s: s["next"])
    nxt_step = upcoming[0] if upcoming else None
    if SEVERITY[worst["state"]] == 2:
        verdict, headline = "stopped", f"{worst['label']}: {worst['detail']}"
    elif SEVERITY[worst["state"]] == 1:
        verdict, headline = "degraded", f"{worst['label']}: {worst['detail']}"
    elif market_open:
        verdict = "active"
        headline = (f"Working. Watching {have} stocks live"
                    + (f", {len(eng.portfolio.positions)} positions protected by stops."
                       if eng.portfolio.positions else "."))
    else:
        verdict = "waiting"
        headline = "Working. Market closed, the bot is waiting for its next scheduled step."
    return {
        "verdict": verdict, "headline": headline, "market_open": market_open,
        "next": {"label": nxt_step["label"], "at": nxt_step["next"],
                 "when": _fmt(nxt_step["next"], now)} if nxt_step else None,
        "steps": steps,
    }


# ---------------------------------------------------------------- activity
def _money(x: Any) -> str:
    try:
        return f"₹{float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def describe(event: str, actor: str, p: dict[str, Any]) -> tuple[str, str, str] | None:
    """Audit event -> (kind, level, sentence). None hides noisy internals."""
    sym = p.get("symbol", "")
    if event == "decision.cycle":
        intents = p.get("intents") or []
        day = str(p.get("bar_ts", ""))[:10]
        scored = p.get("scored")
        what = f"Scored {scored} stocks" if scored is not None else "Analysed the market"
        plan = ", ".join(intents) if intents else "no trades planned"
        reg = p.get("regime") or {}
        extra = " New buys blocked by the market filter." if reg.get("entries_blocked") else ""
        return "decision", "info", f"{what} on the {day} close: {plan}.{extra}"
    if event == "fill":
        side = "Bought" if p.get("side") == "BUY" else "Sold"
        why = {"protective_stop": " (stop-loss hit)", "exit": "", "entry": ""}.get(
            str(p.get("purpose")), "")
        level = "warning" if p.get("purpose") == "protective_stop" else "info"
        return "trade", level, (f"{side} {p.get('qty')} {sym} at {_money(p.get('price'))}{why}"
                                f" · charges {_money(p.get('charges'))}")
    if event == "risk.rejected":
        return "risk", "warning", (f"Risk engine blocked {p.get('side')} {sym}: "
                                   f"{p.get('reasons')}")
    if event == "order.stop_moved":
        return "trade", "info", (f"Trailing stop on {sym} raised {_money(p.get('from'))} → "
                                 f"{_money(p.get('to'))}")
    if event in ("order.rejected", "order.cancelled", "order.expired"):
        word = event.split(".")[1]
        return "trade", "warning" if word == "rejected" else "info", \
            f"Order {p.get('side')} {sym} {word}" + (f": {p['reason']}" if p.get("reason") else "")
    if event == "breaker.tripped":
        return "safety", "critical", f"Breaker {p.get('breaker')} tripped: {p.get('reason')}"
    if event == "breaker.reset":
        who = "automatically" if p.get("actor") == "auto" else "by you"
        return "safety", "info", f"Breaker {p.get('breaker')} reset {who} ({p.get('note')})"
    if event in ("kill_switch.activated", "kill_switch.observed"):
        return "safety", "critical", f"Kill switch on ({p.get('action')}): {p.get('reason')}"
    if event == "kill_switch.released":
        return "safety", "info", "Kill switch released"
    if event == "news.cycle":
        err = " Groq error, unscored items stay neutral." if p.get("llm_error") else ""
        return "news", "warning" if err else "info", \
            f"News scan: {p.get('added', 0)} new headlines, {p.get('scored', 0)} scored.{err}"
    if event == "history.loaded":
        failed = p.get("failed") or []
        return "data", "warning" if failed else "info", \
            (f"Loaded price history for {p.get('symbols')} symbols from {p.get('source')}"
             + (f" ({len(failed)} failed)" if failed else ""))
    if event == "upstox.login":
        return "data", "info", "Logged in to Upstox"
    if event == "upstox.logout":
        return "data", "info", "Logged out of Upstox"
    if event in ("engine.start", "engine.stop"):
        return "system", "info", ("Engine started by you" if event == "engine.start"
                                  else "Engine stopped by you")
    if event == "api.started":
        return "system", "info", "Backend started"
    if event == "engine.restored":
        return "system", "info", (f"Restored state: {p.get('positions', 0)} positions, "
                                  f"{p.get('pending_intents', 0)} queued plans")
    if event == "config.activated":
        return "system", "info", (f"Settings v{p.get('version', '?')} took effect"
                                  if p.get("version") else "New strategy settings took effect")
    return None
