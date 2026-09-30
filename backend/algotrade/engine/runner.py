"""Live paper-trading loop (docs/runbook.md §1 schedule, automated).

Every ``poll_interval_sec``: heartbeat, kill-switch sync, prices, fills,
pending intents, breakers. On bar close: fetch completed bars and run a
decision cycle. News runs on its own timer in the background. Any
unexpected exception trips the ``unhandled_exception`` breaker and the loop
keeps running in a halted state so the dashboard and stops stay alive.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from datetime import datetime, time, timedelta
from typing import Any

from algotrade.core.clock import IST, to_ist
from algotrade.data.providers.base import ProviderError
from algotrade.data.providers.upstox import UpstoxProvider
from algotrade.engine.runtime import Runtime

log = logging.getLogger("algotrade.runner")
HISTORY_DAYS = 420
DAILY_CYCLE_AT = time(15, 40)


class LiveRunner:
    def __init__(self, rt: Runtime) -> None:
        self.rt = rt
        self.running = False
        self._task: asyncio.Task[None] | None = None
        self._news_task: asyncio.Task[Any] | None = None
        self.history_loaded_for: str = ""
        self.daily_cycle_done_for: str = ""
        self.last_intraday_bar: datetime | None = None
        self.last_housekeeping: datetime | None = None
        self.last_news: datetime | None = None
        self.last_error: str = ""
        self.last_tick_count = 0

    # ------------------------------------------------------------ control
    async def start(self) -> None:
        if self.running:
            return
        await self.rt.engine.resolve_uncertain(self.rt.clock.now())
        self.running = True
        self._task = asyncio.create_task(self._loop(), name="engine-loop")

    async def stop(self) -> None:
        self.running = False
        for t in (self._task, self._news_task):
            if t:
                t.cancel()
        self._task = None

    @property
    def symbols(self) -> list[str]:
        eng = self.rt.engine
        held = list(eng.portfolio.positions)
        extra = [self.rt.config.strategy.regime.index_symbol,
                 self.rt.config.strategy.regime.vix_symbol]
        return sorted(set(self.rt.universe.symbols) | set(held) | set(extra))

    # --------------------------------------------------------------- loop
    async def _loop(self) -> None:
        while self.running:
            now = self.rt.clock.now()
            try:
                await self.step(now)
                self.last_error = ""
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.error("engine step failed\n%s", traceback.format_exc())
                try:
                    self.rt.engine.trip("unhandled_exception", self.last_error[:200], now)
                except Exception:  # never let the loop die; the halt is in memory
                    log.critical("could not record breaker\n%s", traceback.format_exc())
            await asyncio.sleep(self.rt.settings.poll_interval_sec)

    async def step(self, now: datetime) -> None:
        rt = self.rt
        eng = rt.engine
        rt.repo.set_state("engine_heartbeat", {"ts": now.isoformat(), "running": True}, now)
        eng.sync_kill_from_db()
        cal = rt.calendar
        local = to_ist(now)
        today = local.date()
        market_open = cal.is_open(now)

        if not market_open and rt.reload_config():
            eng = rt.engine

        ready = rt.provider.is_ready()
        if ready and cal.is_trading_day(today) and self.history_loaded_for != today.isoformat():
            await self.load_history(now)
            eng = rt.engine
        if cal.is_trading_day(today) and local.time() >= time(9, 0):
            eng.mark_session(now)

        if ready and market_open:
            try:
                ticks = await rt.provider.ltp(self.symbols)
            except ProviderError as exc:
                ticks = []
                self.last_error = str(exc)
            self.last_tick_count = len(ticks)
            for t in ticks:
                eng.on_tick(t, now)
            await eng.process_fills(now)

        await eng.execute_pending(now)
        await eng.process_fills(now)

        if ready and cal.is_trading_day(today):
            await self.maybe_bar_cycle(now)

        if self.last_housekeeping is None or now - self.last_housekeeping >= timedelta(seconds=60):
            self.last_housekeeping = now
            if market_open:
                eng.check_breakers(now)
            await eng.reconcile(now)
            if market_open or local.time() < time(16, 0):
                eng.snapshot_equity(now)

        self.maybe_start_news(now)
        await rt.notifier.flush()

    # ------------------------------------------------------------ history
    async def load_history(self, now: datetime) -> None:
        rt = self.rt
        prov = rt.provider
        if isinstance(prov, UpstoxProvider):
            await prov.load_instruments(rt.universe.symbols)
        end = to_ist(now).date()
        start = end - timedelta(days=HISTORY_DAYS)
        loaded, failed = 0, []
        for sym in self.symbols:
            try:
                bars = await prov.history(sym, "1d", start, end)
            except ProviderError as exc:
                failed.append(f"{sym}: {exc}")
                continue
            # Keep only completed sessions (today's bar arrives after the close).
            bars = [b for b in bars if b.ts + timedelta(hours=15, minutes=30) <= now]
            loaded += rt.engine.load_daily_history(sym, bars, now) > 0
        self.history_loaded_for = to_ist(now).date().isoformat()
        rt.audit.record(now, "runner", "history.loaded", {"symbols": loaded,
                                                          "failed": failed[:20]})
        if failed:
            rt.notifier.send(now, "warning", f"History failed for {len(failed)} symbols")

    # --------------------------------------------------------- bar cycles
    async def maybe_bar_cycle(self, now: datetime) -> None:
        rt = self.rt
        local = to_ist(now)
        tf = rt.config.strategy.timeframe
        if tf == "1d":
            done = self.daily_cycle_done_for == local.date().isoformat()
            if local.time() < DAILY_CYCLE_AT or done:
                return
            bar_ts = datetime.combine(local.date(), time(0, 0), tzinfo=IST)
            got = await self._fetch_bars("1d", now)
            if got == 0:
                return  # try again next loop; the provider may still be publishing
            self.daily_cycle_done_for = local.date().isoformat()
            await rt.engine.decision_cycle(now, bar_ts.astimezone(now.tzinfo))
            return
        # 15-minute bars: run once per completed bar, 20s after it closes.
        if not rt.calendar.is_open(now - timedelta(seconds=20)):
            return
        minutes = (local.hour * 60 + local.minute - 15) // 15 * 15 + 15
        boundary = local.replace(hour=minutes // 60, minute=minutes % 60, second=0,
                                 microsecond=0)
        bar_ts = boundary - timedelta(minutes=15)
        if self.last_intraday_bar and bar_ts <= self.last_intraday_bar:
            return
        if (local - boundary).total_seconds() < 20:
            return
        if await self._fetch_bars("15m", now) == 0:
            return
        self.last_intraday_bar = bar_ts
        await rt.engine.decision_cycle(now, bar_ts)

    async def _fetch_bars(self, timeframe: str, now: datetime) -> int:
        rt = self.rt
        n = 0
        today = to_ist(now).date()
        for sym in self.symbols:
            try:
                bars = await rt.provider.intraday(sym, timeframe)
                if not bars and timeframe == "1d":
                    bars = await rt.provider.history(sym, "1d", today, today)
            except ProviderError:
                continue
            if bars and rt.engine.append_bar(bars[-1], now):
                n += 1
        return n

    # ---------------------------------------------------------------- news
    def maybe_start_news(self, now: datetime) -> None:
        interval = timedelta(minutes=self.rt.settings.news_interval_min)
        if self._news_task and not self._news_task.done():
            return
        if self.last_news and now - self.last_news < interval:
            return
        if not self.rt.config.strategy.sentiment.enabled:
            return
        self.last_news = now
        self._news_task = asyncio.create_task(self._news(now), name="news")

    async def _news(self, now: datetime) -> None:
        rt = self.rt
        try:
            await rt.news.run(rt.universe.instruments(), now, rt.config.strategy.sentiment)
        except Exception as exc:  # news must never break trading
            log.warning("news cycle failed: %s", exc)
            rt.notifier.send(now, "warning", f"News cycle failed: {type(exc).__name__}")

    def status(self) -> dict[str, Any]:
        return {"running": self.running, "last_error": self.last_error,
                "history_loaded_for": self.history_loaded_for,
                "daily_cycle_done_for": self.daily_cycle_done_for,
                "last_tick_count": self.last_tick_count,
                "last_news": self.last_news}
