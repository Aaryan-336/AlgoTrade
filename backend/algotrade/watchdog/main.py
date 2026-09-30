"""Watchdog: an independent process (docs/risk-management.md §6).

It shares nothing with the API process except the database. During market
hours it checks the engine heartbeat; if the heartbeat goes silent it sets
the kill switch directly in the database (which the engine obeys on its
next loop) and raises a critical alert. In live mode it would also talk to
the broker directly; in paper mode the simulated broker's stops keep
working inside the engine.

Also provides a CLI kill switch that works when the API is down:
    algotrade-watchdog kill --reason "..."      (block new entries)
    algotrade-watchdog kill --flatten --reason "..."
    algotrade-watchdog status
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from algotrade.audit.logger import AuditLog
from algotrade.config.settings import get_settings
from algotrade.core.clock import MarketCalendar
from algotrade.db.repository import Repository
from algotrade.db.session import Database

log = logging.getLogger("algotrade.watchdog")


def heartbeat_age(repo: Repository, now: datetime) -> float | None:
    hb = repo.get_state("engine_heartbeat")
    if not hb or not hb.get("ts"):
        return None
    return (now - datetime.fromisoformat(hb["ts"])).total_seconds()


def set_kill(repo: Repository, audit: AuditLog, action: str, reason: str, now: datetime,
             actor: str) -> None:
    current = repo.get_state("kill_switch") or {}
    if current.get("active"):
        return
    repo.set_state("kill_switch", {"active": True, "action": action, "reason": reason,
                                   "ts": now.isoformat()}, now)
    audit.record(now, actor, "kill_switch.activated", {"action": action, "reason": reason})


def check_once(repo: Repository, audit: AuditLog, calendar: MarketCalendar, now: datetime,
               timeout_sec: int, trigger_kill: bool) -> dict[str, Any]:
    """One watchdog pass. Returns what it saw (for logs and tests)."""
    if not calendar.is_open(now):
        return {"market_open": False}
    hb = repo.get_state("engine_heartbeat") or {}
    age = heartbeat_age(repo, now)
    if hb.get("running") is False:
        return {"market_open": True, "engine": "stopped by owner", "age": age}
    stale = age is None or age > timeout_sec
    result: dict[str, Any] = {"market_open": True, "heartbeat_age": age, "stale": stale}
    if stale:
        reason = (f"engine heartbeat lost ({age:.0f}s)" if age is not None
                  else "no engine heartbeat")
        result["alert"] = reason
        if trigger_kill:
            set_kill(repo, audit, "block", reason, now, "watchdog")
            result["kill_switch"] = True
    return result


def _telegram(text: str) -> None:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{s.telegram_bot_token.get_secret_value()}"
                   "/sendMessage", json={"chat_id": s.telegram_chat_id,
                                         "text": f"🛑 [WATCHDOG] {text}"}, timeout=10)
    except httpx.HTTPError as exc:
        log.warning("telegram failed: %s", type(exc).__name__)


def loop(interval: float = 15.0) -> None:
    s = get_settings()
    db = Database(s.database_url)
    repo, audit = Repository(db), AuditLog(db)
    latest = repo.latest_config()
    ks = latest[1].risk.kill_switch if latest else None
    timeout = ks.watchdog_heartbeat_timeout_sec if ks else 90
    trigger = ks.watchdog_triggers_kill_switch if ks else True
    cal = MarketCalendar()
    alerted = False
    log.info("watchdog started (timeout %ss, kill switch on loss: %s)", timeout, trigger)
    while True:
        now = datetime.now(UTC)
        if not db.ping():
            log.critical("database unreachable")
            if not alerted:
                _telegram("database unreachable; engine cannot trade (fails closed)")
                alerted = True
        else:
            res = check_once(repo, audit, cal, now, timeout, trigger)
            if res.get("alert"):
                log.critical("%s", res["alert"])
                if not alerted:
                    _telegram(str(res["alert"]))
                    alerted = True
            else:
                alerted = False
        time.sleep(interval)


def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="algotrade-watchdog")
    sub = p.add_subparsers(dest="cmd")
    k = sub.add_parser("kill", help="activate the kill switch")
    k.add_argument("--reason", required=True)
    k.add_argument("--flatten", action="store_true", help="also sell all positions")
    sub.add_parser("status", help="show heartbeat and kill switch")
    args = p.parse_args()
    s = get_settings()
    db = Database(s.database_url)
    repo, audit = Repository(db), AuditLog(db)
    now = datetime.now(UTC)
    if args.cmd == "kill":
        set_kill(repo, audit, "flatten" if args.flatten else "block", args.reason, now, "cli")
        print("kill switch set; the engine applies it on its next loop")
        return
    if args.cmd == "status":
        print({"heartbeat_age_sec": heartbeat_age(repo, now),
               "kill_switch": repo.get_state("kill_switch"),
               "breakers": repo.get_state("breakers")})
        return
    try:
        loop()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    run()
