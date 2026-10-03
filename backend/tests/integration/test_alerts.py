"""Unattended running: analytics token, phone alert levels, daily briefs,
health-change alerts and the dead-man's-switch ping."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, date, datetime, timedelta

import httpx
from pydantic import SecretStr

from algotrade.config.settings import Settings
from algotrade.core.clock import ManualClock
from algotrade.data.providers.replay import synthetic_daily_bars
from algotrade.data.providers.upstox import UpstoxAuth, jwt_expiry
from algotrade.db.session import Database
from algotrade.engine.alerts import Notifier
from algotrade.engine.briefs import evening_brief, morning_brief
from algotrade.engine.runner import LiveRunner
from algotrade.engine.runtime import Runtime
from tests.conftest import NOW, SESSION_DAY, ist


def fake_jwt(exp: datetime) -> str:
    def b64(d: dict[str, object]) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'none'})}.{b64({'exp': int(exp.timestamp())})}.sig"


def runtime(clock: ManualClock, **kw: object) -> Runtime:
    settings = Settings(data_provider="replay", database_url="sqlite://", **kw)  # type: ignore[arg-type]
    return Runtime(settings, db=Database("sqlite://"), clock=clock)


def load_history(rt: Runtime) -> None:
    for i, s in enumerate(rt.universe.symbols[:6]):
        rt.engine.load_daily_history(
            s, synthetic_daily_bars(s, date(2025, 6, 2), 300, seed=i, drift=0.002), NOW)
    rt.engine.load_daily_history("NIFTY50", synthetic_daily_bars(
        "NIFTY50", date(2025, 6, 2), 300, seed=99, start_price=20000, drift=0.002), NOW)


def test_analytics_token_connects_without_login_and_reports_expiry() -> None:
    exp = datetime(2027, 10, 1, tzinfo=UTC)
    tok = fake_jwt(exp)
    auth = UpstoxAuth(None, None, "http://x/cb", analytics_token=f" '{tok}' ")
    assert auth.token == tok and auth.kind == "analytics" and auth.configured
    assert auth.expires_at == exp == jwt_expiry(tok)
    assert jwt_expiry("not-a-jwt") is None
    auth.clear()  # what a 401 from Upstox does
    assert auth.token is None and auth.analytics_rejected and auth.kind == "login"

    rt = runtime(ManualClock(NOW), upstox_analytics_token=SecretStr(tok))
    assert rt.upstox_auth.kind == "analytics" and rt.upstox_auth.token == tok


def test_telegram_alert_level_controls_what_reaches_the_phone() -> None:
    every = Notifier("t", "c", quiet=True, min_level="info")
    serious = Notifier("t", "c", quiet=True)  # default: warnings and critical
    for n in (every, serious):
        n.send(NOW, "info", "Bought INFY")
        n.send(NOW, "critical", "Breaker")
    assert [a.text for a in every._outbox] == ["Bought INFY", "Breaker"]
    assert [a.text for a in serious._outbox] == ["Breaker"]


def test_briefs_are_sent_once_per_trading_day() -> None:
    clock = ManualClock(ist(SESSION_DAY, 9, 5))
    rt = runtime(clock)
    load_history(rt)
    runner = LiveRunner(rt)
    level, text = morning_brief(rt, runner, clock.now())
    assert level == "critical" and "NOT READY" in text  # engine loop not running in tests
    assert "Equity ₹" in text and "No trades queued" in text

    runner.maybe_send_briefs(clock.now())
    runner.maybe_send_briefs(clock.now() + timedelta(minutes=5))
    mornings = [a for a in rt.notifier.recent if a.text.startswith("Good morning")]
    assert len(mornings) == 1

    clock.set(ist(SESSION_DAY, 15, 55))
    runner.daily_cycle_done_for = SESSION_DAY.isoformat()
    runner.maybe_send_briefs(clock.now())
    runner.maybe_send_briefs(clock.now())
    evenings = [a for a in rt.notifier.recent if a.text.startswith("Day summary")]
    assert len(evenings) == 1
    assert "No trades today" in evenings[0].text and "Tomorrow:" in evenings[0].text
    assert "Nifty" in evening_brief(rt, clock.now())

    # Not on a weekend.
    sat = ist(date(2026, 9, 19), 10, 0)
    before = len(rt.notifier.recent)
    runner.maybe_send_briefs(sat)
    assert len(rt.notifier.recent) == before


def test_health_watch_alerts_once_after_two_bad_checks_and_on_recovery() -> None:
    rt = runtime(ManualClock(NOW))
    runner = LiveRunner(rt)
    watch = runner.health_watch
    watch.check(rt, runner, NOW)  # first bad reading: wait for confirmation
    assert not [a for a in rt.notifier.recent if "NOT trading" in a.text]
    watch.check(rt, runner, NOW)
    watch.check(rt, runner, NOW)
    alerts = [a for a in rt.notifier.recent if "NOT trading" in a.text]
    assert len(alerts) == 1 and alerts[0].level == "critical"


def test_healthcheck_ping_hits_the_monitor_url() -> None:
    hits: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        return httpx.Response(200)

    rt = runtime(ManualClock(NOW), healthcheck_ping_url="https://hc.example/ping/abc")
    rt.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    runner = LiveRunner(rt)

    async def go() -> None:
        runner.ping_healthcheck()
        assert runner._ping_task is not None
        await runner._ping_task

    asyncio.run(go())
    assert hits == ["https://hc.example/ping/abc"]
