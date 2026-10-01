from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient
from pydantic import SecretStr

from algotrade.api.main import create_app
from algotrade.config.settings import Settings
from algotrade.core.clock import ManualClock
from algotrade.db.session import Database
from algotrade.engine.runtime import Runtime
from tests.conftest import NOW, SESSION_DAY, ist


def client(clock: ManualClock, token: str | None = "t0ken") -> TestClient:
    settings = Settings(data_provider="replay", database_url="sqlite://",
                        api_token=SecretStr(token) if token else None)
    rt = Runtime(settings, db=Database("sqlite://"), clock=clock)
    return TestClient(create_app(settings, rt, autostart=False))


H = {"Authorization": "Bearer t0ken"}


def test_auth_required_and_status() -> None:
    with client(ManualClock(NOW)) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/status").status_code == 401
        s = c.get("/api/status", headers=H).json()
        assert s["mode"] == "PAPER" and s["equity"] == 5000.0
        assert s["provider"]["name"] == "replay"
        for path in ("portfolio", "orders", "decisions", "risk-events", "universe", "news",
                     "config", "trades", "equity", "backtests", "audit"):
            assert c.get(f"/api/{path}", headers=H).status_code == 200, path


def test_kill_switch_and_breaker_endpoints() -> None:
    with client(ManualClock(NOW)) as c:
        r = c.post("/api/kill-switch", json={"action": "block", "reason": "testing"},
                   headers=H)
        assert r.json()["kill_switch"]["active"] is True
        assert c.post("/api/kill-switch", json={"action": "nuke", "reason": "x"},
                      headers=H).status_code == 422
        r = c.post("/api/kill-switch/release", json={"note": "all good"}, headers=H)
        assert r.json()["kill_switch"]["active"] is False
        assert c.post("/api/breakers/drawdown/reset", json={"note": "nothing tripped"},
                      headers=H).status_code == 404
        assert c.get("/api/audit/verify", headers=H).json()["ok"] is True


def test_config_locked_during_market_hours_and_validated() -> None:
    clock = ManualClock(NOW)  # 11:00 IST on a trading day
    with client(clock) as c:
        cfg = c.get("/api/config", headers=H).json()
        body = {"risk": cfg["risk"], "strategy": cfg["strategy"], "reason": "tweak"}
        assert c.put("/api/config", json=body, headers=H).status_code == 409
        clock.set(ist(SESSION_DAY, 18, 0))
        bad = {**body, "risk": {**cfg["risk"], "risk_per_trade_pct": 9}}
        assert c.put("/api/config", json=bad, headers=H).status_code == 422
        ok = c.put("/api/config", json=body, headers=H)
        assert ok.status_code == 200 and ok.json()["version"] == 2


def test_upstox_login_requires_configuration_and_valid_state() -> None:
    with client(ManualClock(NOW + timedelta(hours=10)), token=None) as c:
        assert c.get("/api/auth/upstox/login", follow_redirects=False).status_code == 400
        assert c.get("/api/auth/upstox/callback?code=x&state=forged").status_code == 400


def test_backtest_needs_ready_provider_or_data() -> None:
    with client(ManualClock(NOW)) as c:
        r = c.post("/api/backtest", json={"start": "2025-01-01", "end": "2025-06-01"},
                   headers=H)
        assert r.status_code == 502  # replay provider has no data loaded


def test_backtest_accepts_unsaved_settings_during_market_hours() -> None:
    with client(ManualClock(NOW)) as c:  # market open
        cfg = c.get("/api/config", headers=H).json()
        bad = {"start": "2025-01-01", "end": "2025-06-01",
               "risk": {**cfg["risk"], "risk_per_trade_pct": 9}, "strategy": cfg["strategy"]}
        assert c.post("/api/backtest", json=bad, headers=H).status_code == 422
        trend = {**cfg["strategy"]["strategies"]["trend"], "fast_ema": 50, "slow_ema": 200}
        strategy = {**cfg["strategy"],
                    "strategies": {**cfg["strategy"]["strategies"], "trend": trend}}
        ok = {"start": "2025-01-01", "end": "2025-06-01", "risk": cfg["risk"],
              "strategy": strategy}
        # Passes validation; fails only because the replay provider has no data.
        assert c.post("/api/backtest", json=ok, headers=H).status_code == 502
        # The live config is untouched.
        assert c.get("/api/config", headers=H).json()["latest_version"] == 1
