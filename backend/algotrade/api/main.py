"""FastAPI app: REST for the dashboard, plus a WebSocket live snapshot.

Bind to localhost (or a private network). Set API_TOKEN to require a bearer
token on every call. State-changing endpoints are audited.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from algotrade.backtest.engine import run_backtest, warmup_start
from algotrade.config.models import AppConfig, RiskConfig, StrategyConfig
from algotrade.config.settings import Settings, get_settings
from algotrade.core.clock import to_ist
from algotrade.data.providers.base import ProviderError
from algotrade.data.universe import Universe
from algotrade.engine.insights import (
    bot_health,
    change_pct,
    describe,
    last_and_prev,
    market_insights,
)
from algotrade.engine.runner import LiveRunner
from algotrade.engine.runtime import Runtime
from algotrade.oms.state_machine import WORKING

log = logging.getLogger("algotrade.api")


def j(obj: Any) -> Any:
    """JSON for the dashboard: Decimals become floats (display only)."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: j(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): j(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple | set):
        return [j(v) for v in obj]
    return obj


class KillRequest(BaseModel):
    action: str = Field(pattern="^(block|flatten)$")
    reason: str = Field(min_length=3, max_length=300)


class NoteRequest(BaseModel):
    note: str = Field(min_length=3, max_length=1000)


class ConfigRequest(BaseModel):
    risk: dict[str, Any]
    strategy: dict[str, Any]
    reason: str = Field(min_length=3, max_length=300)


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    risk: dict[str, Any]
    strategy: dict[str, Any]


class BacktestRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=60)
    start: date
    end: date
    capital: float | None = Field(default=None, gt=0)
    # Unsaved settings from the Settings page. Used for this backtest only; they
    # never touch the live config, so they are allowed during market hours.
    risk: dict[str, Any] | None = None
    strategy: dict[str, Any] | None = None
    profile_id: int | None = None  # backtest a saved strategy version


def create_app(settings: Settings | None = None, runtime: Runtime | None = None,
               autostart: bool | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        rt = runtime or Runtime(settings)
        runner = LiveRunner(rt)
        app.state.rt = rt
        app.state.runner = runner
        app.state.oauth_state = ""
        start = settings.autostart_engine if autostart is None else autostart
        if start and settings.data_provider != "replay":
            await runner.start()
        rt.audit.record(rt.clock.now(), "system", "api.started",
                        {"mode": settings.mode.value, "provider": settings.data_provider})
        yield
        await runner.stop()
        await rt.aclose()

    app = FastAPI(title="AlgoTrade (paper)", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                       allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["*"])

    def auth(request: Request) -> None:
        if settings.api_token is None:
            return
        header = request.headers.get("authorization", "")
        token = header.removeprefix("Bearer ").strip()
        if not secrets.compare_digest(token, settings.api_token.get_secret_value()):
            raise HTTPException(401, "invalid token")

    def rt_of(request: Request) -> Runtime:
        rt: Runtime = request.app.state.rt
        return rt

    guard = [Depends(auth)]

    # ------------------------------------------------------------- status
    def build_status(rt: Runtime, runner: LiveRunner) -> dict[str, Any]:
        now = rt.clock.now()
        eng = rt.engine
        health = rt.provider.health()
        hb = rt.repo.get_state("engine_heartbeat")
        demo = rt.repo.get_state("demo") or {}
        out: dict[str, Any] = j({
            **eng.status(now),
            "now": now, "now_ist": to_ist(now).strftime("%d %b %Y, %H:%M:%S IST"),
            "provider": {"name": health.name, "connected": health.connected,
                         "last_update": health.last_update, "message": health.message,
                         "ready": rt.provider.is_ready()},
            "upstox": {"configured": rt.upstox_auth.configured,
                       "logged_in": rt.upstox_auth.token is not None,
                       "since": rt.upstox_auth.token_set_at,
                       "kind": rt.upstox_auth.kind,
                       "expires_at": rt.upstox_auth.expires_at,
                       "analytics_rejected": rt.upstox_auth.analytics_rejected},
            "groq": {"configured": rt.llm.configured, "model": rt.llm.model,
                     "last_error": rt.llm.last_error},
            "news": {"last_run": rt.news.last_run, "counts": rt.news.last_counts,
                     "errors": rt.news.last_errors[:5]},
            "runner": runner.status(),
            "health": bot_health(rt, runner, now),
            "heartbeat": hb,
            "telegram": bool(settings.telegram_bot_token and settings.telegram_chat_id),
            "demo": bool(demo.get("active")),
            "alerts": [{"ts": a.ts, "level": a.level, "text": a.text}
                       for a in list(rt.notifier.recent)[:20]],
        })
        return out

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/status", dependencies=guard)
    async def status(request: Request) -> Any:
        return build_status(rt_of(request), request.app.state.runner)

    # ---------------------------------------------------------- portfolio
    def portfolio_view(rt: Runtime) -> dict[str, Any]:
        eng = rt.engine
        prices = eng.prices
        eq = eng.equity()
        rows = []
        for s, p in eng.portfolio.positions.items():
            ltp = prices.get(s, p.avg_price)
            value = ltp * p.qty
            stop_order = eng.oms.protective_stop(s)
            rows.append({
                "symbol": s, "qty": p.qty, "avg_price": p.avg_price, "ltp": ltp,
                "value": value, "pnl": (ltp - p.avg_price) * p.qty,
                "pnl_pct": float((ltp / p.avg_price - 1) * 100) if p.avg_price else 0,
                "weight_pct": float(value / eq * 100) if eq else 0, "stop": p.stop,
                "target": p.target, "strategy": p.strategy, "sector": p.sector,
                "opened_at": p.opened_at, "stop_live": stop_order is not None,
                "sentiment": sent.score if (sent := eng.sentiment.get(s)) else None,
            })
        sectors = {k: {"value": v, "pct": float(v / eq * 100) if eq else 0}
                   for k, v in eng.portfolio.sector_exposure(prices).items()}
        view: dict[str, Any] = j({"equity": eq, "cash": eng.portfolio.cash,
                  "invested": eng.portfolio.market_value(prices),
                  "unrealized": eng.portfolio.unrealized(prices),
                  "realized": eng.portfolio.realized, "fees": eng.portfolio.fees,
                  "positions": sorted(rows, key=lambda r: -r["value"]),
                  "sectors": sectors, "limits": {
                      "max_position_pct": rt.config.risk.max_position_pct,
                      "max_sector_pct": rt.config.risk.max_sector_pct,
                      "max_open_positions": rt.config.risk.max_open_positions,
                      "max_daily_loss_pct": rt.config.risk.max_daily_loss_pct,
                      "max_drawdown_pct": rt.config.risk.max_drawdown_pct}})
        return view

    @app.get("/api/portfolio", dependencies=guard)
    async def portfolio(request: Request) -> Any:
        return portfolio_view(rt_of(request))

    @app.get("/api/equity", dependencies=guard)
    async def equity(request: Request, days: int = Query(90, ge=1, le=3650)) -> Any:
        rt = rt_of(request)
        since = rt.clock.now() - timedelta(days=days)
        rows = rt.repo.equity_curve(since)
        return j([{"ts": r.ts, "equity": r.equity, "cash": r.cash,
                   "drawdown_pct": r.drawdown_pct} for r in rows])

    @app.get("/api/trades", dependencies=guard)
    async def trades(request: Request) -> Any:
        """Closed round trips, rebuilt from fills."""
        rt = rt_of(request)
        return j(list(reversed([dataclasses.asdict(t) for t in
                                rt.engine.portfolio.closed_trades]))[:500])

    # ------------------------------------------------------------- orders
    @app.get("/api/orders", dependencies=guard)
    async def orders(request: Request, limit: int = Query(100, ge=1, le=1000),
                     open_only: bool = False) -> Any:
        rt = rt_of(request)
        states = [s.value for s in WORKING] if open_only else None
        rows = rt.repo.orders(limit, states)
        return j([{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows])

    @app.get("/api/orders/{order_id}/events", dependencies=guard)
    async def order_events(request: Request, order_id: str) -> Any:
        rows = rt_of(request).repo.order_events(order_id)
        return j([{"ts": r.ts, "from": r.from_state, "to": r.to_state, "detail": r.detail}
                  for r in rows])

    @app.get("/api/decisions", dependencies=guard)
    async def decisions(request: Request, limit: int = Query(150, ge=1, le=1000),
                        symbol: str | None = None) -> Any:
        rows = rt_of(request).repo.decisions(limit, symbol)
        return j([{"id": r.id, "ts": r.ts, "symbol": r.symbol, "kind": r.kind, "score": r.score,
                   "components": r.components, "outcome": r.outcome,
                   "reason": r.rejection_reason, "config_version": r.config_version}
                  for r in rows])

    @app.get("/api/risk-events", dependencies=guard)
    async def risk_events(request: Request, limit: int = Query(200, ge=1, le=2000)) -> Any:
        rows = rt_of(request).repo.risk_events(limit)
        return j([{"id": r.id, "ts": r.ts, "symbol": r.symbol, "check": r.check_name,
                   "result": r.result, "details": r.details, "decision_id": r.decision_id}
                  for r in rows])

    # ---------------------------------------------------- market and news
    @app.get("/api/universe", dependencies=guard)
    async def universe(request: Request) -> Any:
        rt = rt_of(request)
        eng = rt.engine
        out = []
        for inst in rt.universe.instruments():
            last, prev = last_and_prev(eng, inst.symbol)
            sent = eng.sentiment.get(inst.symbol)
            out.append({"symbol": inst.symbol, "name": inst.name, "sector": inst.sector,
                        "ltp": last, "change_pct": change_pct(last, prev),
                        "held": inst.symbol in eng.portfolio.positions,
                        "sentiment": sent.score if sent and not sent.missing else None,
                        "news_count": sent.n_items if sent else 0,
                        "negative_material": bool(sent and sent.negative_material)})
        return j(out)

    @app.get("/api/bars/{symbol}", dependencies=guard)
    async def bars(request: Request, symbol: str, timeframe: str = Query("1d",
                                                                         pattern="^(1d|15m)$"),
                   limit: int = Query(300, ge=10, le=2000)) -> Any:
        rt = rt_of(request)
        eng = rt.engine
        series = (eng.daily if timeframe == "1d" else eng.intraday).get(symbol) or \
            rt.repo.bars(symbol, timeframe, limit)
        fills = rt.repo.fills(symbol)
        pos = eng.portfolio.positions.get(symbol)
        return j({
            "symbol": symbol,
            "bars": [{"t": int(b.ts.timestamp()), "o": b.open, "h": b.high, "l": b.low,
                      "c": b.close, "v": b.volume} for b in series[-limit:]],
            "fills": [{"t": int(f.ts.timestamp()), "side": f.side, "qty": f.qty,
                       "price": f.price} for f in fills],
            "position": {"avg_price": pos.avg_price, "stop": pos.stop, "target": pos.target,
                         "qty": pos.qty} if pos else None,
        })

    @app.get("/api/market", dependencies=guard)
    async def market(request: Request) -> Any:
        """Market insights for the Overview: index, VIX, breadth, sectors, movers, news."""
        rt = rt_of(request)
        return j(market_insights(rt, rt.clock.now()))

    @app.get("/api/activity", dependencies=guard)
    async def activity(request: Request, limit: int = Query(40, ge=1, le=200)) -> Any:
        """What the bot has done, in plain words, newest first (from the audit log)."""
        rt = rt_of(request)
        from sqlalchemy import select

        from algotrade.db.models import AuditRow
        out: list[dict[str, Any]] = []
        with rt.db.session() as s:
            rows = s.execute(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit * 15)
                             ).scalars().all()
            for r in rows:
                d = describe(r.event, r.actor, r.payload or {})
                if d is None:
                    continue
                out.append({"id": r.id, "ts": r.ts, "kind": d[0], "level": d[1], "text": d[2]})
                if len(out) >= limit:
                    break
        return j(out)

    @app.get("/api/news", dependencies=guard)
    async def news(request: Request, symbol: str | None = None,
                   limit: int = Query(80, ge=1, le=500)) -> Any:
        return j(rt_of(request).repo.news(limit, symbol))

    @app.post("/api/news/refresh", dependencies=guard)
    async def news_refresh(request: Request) -> Any:
        rt = rt_of(request)
        runner: LiveRunner = request.app.state.runner
        runner.last_news = None
        runner.maybe_start_news(rt.clock.now())
        return {"started": True}

    # ---------------------------------------------------------- controls
    @app.post("/api/kill-switch", dependencies=guard)
    async def kill(request: Request, body: KillRequest) -> Any:
        rt = rt_of(request)
        now = rt.clock.now()
        rt.engine.activate_kill(body.action, body.reason, now)
        await rt.engine.execute_pending(now)
        return build_status(rt, request.app.state.runner)

    @app.post("/api/kill-switch/release", dependencies=guard)
    async def kill_release(request: Request, body: NoteRequest) -> Any:
        rt = rt_of(request)
        rt.engine.release_kill(body.note, rt.clock.now())
        return build_status(rt, request.app.state.runner)

    @app.post("/api/breakers/{name}/reset", dependencies=guard)
    async def breaker_reset(request: Request, name: str, body: NoteRequest) -> Any:
        rt = rt_of(request)
        try:
            ok = rt.engine.reset_breaker(name, body.note, rt.clock.now())
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not ok:
            raise HTTPException(404, "breaker not tripped")
        return build_status(rt, request.app.state.runner)

    @app.post("/api/engine/scan", dependencies=guard)
    async def engine_scan(request: Request) -> Any:
        """Run the decision cycle on the latest completed bars right now."""
        rt = rt_of(request)
        eng = rt.engine
        latest = eng.latest_bar_ts()
        if latest is None:
            raise HTTPException(409, "no price history loaded yet (log in to Upstox or wait "
                                     "for the delayed history to load)")
        now = rt.clock.now()
        n = await eng.decision_cycle(now, latest)
        rt.audit.record(now, "owner", "scan.manual", {"bar_ts": latest, "intents": n})
        return j({"intents": n, "bar_ts": latest, "ranked": len(eng.ranking)})

    @app.get("/api/watchlist", dependencies=guard)
    async def watchlist(request: Request) -> Any:
        """Every stock ranked by the bot's score, plus what it has picked."""
        rt = rt_of(request)
        eng = rt.engine
        now = rt.clock.now()
        if not eng.ranking:
            eng.rank_now(now)
        names = {i.symbol: (i.name, i.sector) for i in rt.universe.instruments()}
        rows = []
        for r in eng.ranking:
            sym = str(r["symbol"])
            live = eng.prices.get(sym)
            rows.append({**r, "name": names.get(sym, (sym, ""))[0],
                         "sector": names.get(sym, ("", "Unknown"))[1],
                         "ltp": live if live is not None else r.get("close")})
        pending = {i.symbol: i.side.value for i in eng.pending}
        return j({
            "ranked_at": eng.ranking_at, "bar_ts": eng.latest_bar_ts(),
            "last_cycle": eng.last_cycle, "regime": eng.regime,
            "max_positions": rt.config.risk.max_open_positions,
            "entry_threshold": rt.config.strategy.decision.entry_threshold,
            "pending": pending, "rows": rows,
        })

    @app.post("/api/engine/{action}", dependencies=guard)
    async def engine_control(request: Request, action: str) -> Any:
        rt = rt_of(request)
        runner: LiveRunner = request.app.state.runner
        if action == "start":
            await runner.start()
        elif action == "stop":
            await runner.stop()
            rt.repo.set_state("engine_heartbeat", {"ts": rt.clock.now().isoformat(),
                                                   "running": False}, rt.clock.now())
        else:
            raise HTTPException(404, "unknown action")
        rt.audit.record(rt.clock.now(), "owner", f"engine.{action}", {})
        return build_status(rt, runner)

    # -------------------------------------------------------------- auth
    @app.get("/api/auth/upstox/login")
    async def upstox_login(request: Request) -> Any:
        rt = rt_of(request)
        state = secrets.token_urlsafe(16)
        request.app.state.oauth_state = state
        try:
            return RedirectResponse(rt.upstox_auth.login_url(state))
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/auth/upstox/callback")
    async def upstox_callback(request: Request, code: str = "", state: str = "") -> Any:
        rt = rt_of(request)
        expected = request.app.state.oauth_state
        if not code or not expected or not secrets.compare_digest(state, expected):
            raise HTTPException(400, "invalid login callback")
        request.app.state.oauth_state = ""
        try:
            await rt.upstox_auth.exchange_code(code, rt.http)
        except ProviderError as exc:
            raise HTTPException(400, str(exc)) from exc
        rt.audit.record(rt.clock.now(), "owner", "upstox.login", {"ok": True})
        runner: LiveRunner = request.app.state.runner
        runner.history_loaded_for = ""
        if not runner.running:
            await runner.start()
        return RedirectResponse(f"{settings.dashboard_url}/?upstox=connected")

    @app.post("/api/auth/upstox/logout", dependencies=guard)
    async def upstox_logout(request: Request) -> Any:
        rt = rt_of(request)
        rt.upstox_auth.clear()
        rt.audit.record(rt.clock.now(), "owner", "upstox.logout", {})
        return {"ok": True}

    # ------------------------------------------------------------ config
    @app.get("/api/config", dependencies=guard)
    async def get_config(request: Request) -> Any:
        rt = rt_of(request)
        latest = rt.repo.latest_config()
        pending = latest is not None and latest[0] != rt.config_version
        return j({"active_version": rt.config_version,
                  "latest_version": latest[0] if latest else rt.config_version,
                  "pending_activation": pending,
                  "risk": (latest[1] if latest else rt.config).risk.model_dump(mode="json"),
                  "strategy": (latest[1] if latest else rt.config).strategy.model_dump(
                      mode="json"),
                  "history": rt.repo.config_history(),
                  "market_open": rt.calendar.is_open(rt.clock.now()),
                  "universe_all": [{"symbol": i.symbol, "name": i.name, "sector": i.sector}
                                   for i in Universe.from_config(
                                       rt.config.strategy.universe.model_copy(
                                           update={"symbols": [], "ban_list": []})
                                   ).instruments()]})

    @app.put("/api/config", dependencies=guard)
    async def put_config(request: Request, body: ConfigRequest) -> Any:
        rt = rt_of(request)
        try:
            cfg = AppConfig(risk=RiskConfig.model_validate(body.risk),
                            strategy=StrategyConfig.model_validate(body.strategy))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            v = rt.save_config(cfg, body.reason, rt.clock.now())
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"version": v, "note": "Takes effect when the engine next reloads "
                                      "(immediately if the market is closed)."}

    # ------------------------------------------------- strategy versions
    def profile_view(rt: Runtime) -> dict[str, Any]:
        live_id = rt.live_profile_id()
        rows = rt.repo.profiles()
        out: dict[str, Any] = j({
            "market_open": rt.calendar.is_open(rt.clock.now()),
            "live_id": live_id,
            "profiles": [{"id": r.id, "name": r.name, "description": r.description,
                          "risk": r.risk, "strategy": r.strategy, "updated_at": r.updated_at,
                          "is_live": r.id == live_id} for r in rows],
        })
        return out

    def parse_profile(body: ProfileRequest) -> AppConfig:
        try:
            return AppConfig(risk=RiskConfig.model_validate(body.risk),
                             strategy=StrategyConfig.model_validate(body.strategy))
        except ValueError as exc:
            raise HTTPException(422, f"invalid settings: {exc}") from exc

    def profile_errors(fn: Any) -> Any:
        try:
            return fn()
        except PermissionError as exc:
            raise HTTPException(409, str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/profiles", dependencies=guard)
    async def profiles(request: Request) -> Any:
        return profile_view(rt_of(request))

    @app.post("/api/profiles", dependencies=guard)
    async def create_profile(request: Request, body: ProfileRequest) -> Any:
        rt = rt_of(request)
        cfg = parse_profile(body)
        pid = profile_errors(lambda: rt.save_profile(body.name, body.description, cfg,
                                                     rt.clock.now()))
        return {"id": pid, **profile_view(rt)}

    @app.put("/api/profiles/{profile_id}", dependencies=guard)
    async def update_profile(request: Request, profile_id: int, body: ProfileRequest) -> Any:
        rt = rt_of(request)
        cfg = parse_profile(body)
        profile_errors(lambda: rt.save_profile(body.name, body.description, cfg,
                                               rt.clock.now(), profile_id))
        return {"id": profile_id, **profile_view(rt)}

    @app.delete("/api/profiles/{profile_id}", dependencies=guard)
    async def delete_profile(request: Request, profile_id: int) -> Any:
        rt = rt_of(request)
        profile_errors(lambda: rt.delete_profile(profile_id, rt.clock.now()))
        return profile_view(rt)

    @app.post("/api/profiles/{profile_id}/activate", dependencies=guard)
    async def activate_profile(request: Request, profile_id: int) -> Any:
        rt = rt_of(request)
        version = profile_errors(lambda: rt.activate_profile(profile_id, rt.clock.now()))
        return {"config_version": version, **profile_view(rt)}

    # ----------------------------------------------------------- backtest
    @app.post("/api/backtest", dependencies=guard)
    async def backtest(request: Request, body: BacktestRequest) -> Any:
        rt = rt_of(request)
        if body.end <= body.start:
            raise HTTPException(422, "end must be after start")
        cfg = rt.config
        draft = body.risk is not None and body.strategy is not None
        label = f"live config v{rt.config_version}"
        if body.profile_id is not None:
            row = rt.repo.profile(body.profile_id)
            if row is None:
                raise HTTPException(404, "version not found")
            cfg = rt.profile_config(row)
            label = row.name
        elif draft:
            label = "unsaved edits"
            try:
                cfg = AppConfig(risk=RiskConfig.model_validate(body.risk),
                                strategy=StrategyConfig.model_validate(body.strategy))
            except ValueError as exc:
                raise HTTPException(422, f"invalid settings: {exc}") from exc
        if body.capital:
            cfg = cfg.model_copy(update={"strategy": cfg.strategy.model_copy(
                update={"capital": Decimal(str(body.capital))})})
        uni_cfg = cfg.strategy.universe.model_copy(update={"symbols": body.symbols}) \
            if body.symbols else cfg.strategy.universe
        uni = Universe.from_config(uni_cfg)
        wanted = [*uni.symbols, cfg.strategy.regime.index_symbol,
                  cfg.strategy.regime.vix_symbol]
        data: dict[str, Any] = {}
        failed: list[str] = []
        prov = rt.history_provider()
        if prov is None:
            raise HTTPException(409, "data provider not ready (log in to Upstox first)")
        for sym in wanted:
            try:
                data[sym] = await prov.history(sym, "1d", warmup_start(body.start), body.end)
            except ProviderError as exc:
                failed.append(f"{sym}: {exc}")
        if not any(data.get(s) for s in uni.symbols):
            raise HTTPException(502, "no historical data returned")
        result = await asyncio.to_thread(run_backtest, cfg, uni, data, body.start, body.end,
                                         rt.calendar)
        params = {"symbols": uni.symbols, "start": body.start, "end": body.end,
                  "capital": float(cfg.strategy.capital),
                  "config_version": "draft" if draft else rt.config_version,
                  "version_name": label,
                  # Exact settings used, so runs can be compared later.
                  "settings": {"risk": cfg.risk.model_dump(mode="json"),
                               "strategy": cfg.strategy.model_dump(mode="json")},
                  "provider": prov.name, "failed": failed}
        run_id = rt.repo.save_backtest(rt.clock.now(), params, result.metrics,
                                       result.equity_curve, result.trades)
        return j({"id": run_id, "params": params, "metrics": result.metrics,
                  "equity_curve": result.equity_curve, "trades": result.trades,
                  "rejections": result.rejections})

    @app.get("/api/backtests", dependencies=guard)
    async def backtests(request: Request, limit: int = Query(1000, ge=1, le=5000)) -> Any:
        """Every saved run, newest first (summary only; open one for its trades)."""
        rows = rt_of(request).repo.backtests(limit)
        return j([{"id": r.id, "ts": r.ts, "params": r.params, "metrics": r.metrics}
                  for r in rows])

    @app.get("/api/backtests/{run_id}", dependencies=guard)
    async def backtest_run(request: Request, run_id: int) -> Any:
        r = rt_of(request).repo.backtest(run_id)
        if r is None:
            raise HTTPException(404, "backtest not found")
        return j({"id": r.id, "ts": r.ts, "params": r.params, "metrics": r.metrics,
                  "equity_curve": r.equity_curve, "trades": r.trades})

    @app.delete("/api/backtests/{run_id}", dependencies=guard)
    async def delete_backtest(request: Request, run_id: int) -> Any:
        rt = rt_of(request)
        if not rt.repo.delete_backtest(run_id):
            raise HTTPException(404, "backtest not found")
        rt.audit.record(rt.clock.now(), "owner", "backtest.deleted", {"id": run_id})
        return {"ok": True}

    # ------------------------------------------------------------- audit
    @app.get("/api/audit", dependencies=guard)
    async def audit(request: Request, limit: int = Query(100, ge=1, le=1000)) -> Any:
        rt = rt_of(request)
        from sqlalchemy import select

        from algotrade.db.models import AuditRow
        with rt.db.session() as s:
            rows = s.execute(select(AuditRow).order_by(AuditRow.id.desc()).limit(limit)
                             ).scalars().all()
            return j([{"id": r.id, "ts": r.ts, "actor": r.actor, "event": r.event,
                       "payload": r.payload, "hash": r.hash_self[:12]} for r in rows])

    @app.get("/api/audit/verify", dependencies=guard)
    async def audit_verify(request: Request) -> Any:
        ok, bad = rt_of(request).audit.verify()
        return {"ok": ok, "first_bad_row": bad}

    # --------------------------------------------------------- websocket
    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        if settings.api_token is not None:
            token = websocket.query_params.get("token", "")
            if not secrets.compare_digest(token, settings.api_token.get_secret_value()):
                await websocket.close(code=4401)
                return
        await websocket.accept()
        try:
            while True:
                rt: Runtime = websocket.app.state.rt
                await websocket.send_json({"status": build_status(rt, websocket.app.state.runner),
                                           "portfolio": portfolio_view(rt),
                                           "sent_at": datetime.now(UTC).isoformat()})
                await asyncio.sleep(2)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app


def run() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    s = get_settings()
    uvicorn.run("algotrade.api.main:create_app", factory=True, host=s.api_host, port=s.api_port)
