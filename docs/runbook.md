# Runbook

Operational procedures for running the bot. Keep a printed or offline copy of Sections 3 and 4.

## 1. Daily schedule (IST, trading days)

| Time | Action |
|------|--------|
| 08:30 | Pre-market checks: services up, disk space, clock sync, DB backup status, alerts reachable |
| 08:45 | Review overnight news summary and any regulatory/event flags |
| 09:00 | Broker login (manual); confirm token loaded; confirm **mode** shown on dashboard |
| 09:05 | Load universe, holdings and corporate-action adjustments; run reconciliation |
| 09:10 | Confirm feed healthy; run a dry self-test (risk checks, kill switch ping) |
| 09:15 | Market opens; no new entries for the configured first minutes |
| 09:15 to 15:30 | Bot runs; watch alerts only; no manual tinkering with config |
| 15:15 | Intraday positions (if any) squared off per rules |
| 15:30 | Market closes; stop new entries |
| 15:45 | Reconcile positions and orders with the broker |
| 16:00 | Generate daily report; review rejected orders, risk events, slippage |
| 16:30 | Config changes (if any) applied now, never during market hours |

## 2. Weekly checklist

- [ ] Test the kill switch (paper) and confirm alerts arrive.
- [ ] Review the week's P&L, drawdown, exposure and breaker events.
- [ ] Compare paper (or live) vs backtest expectations; note divergence.
- [ ] Review sentiment scores against what actually happened; look for systematic errors.
- [ ] Check dependency and security alerts; apply updates in non-market hours.
- [ ] Verify backups by restoring to a scratch database.
- [ ] Re-check SEBI, exchange and broker circulars.
- [ ] Rotate anything due for rotation.

## 3. Incident procedures

### 3.1 Feed stale or down
1. Bot halts new entries automatically. Confirm alert.
2. Check provider status and network from the VPS.
3. Existing positions remain protected by exchange-side stops (live). Confirm stops exist in the broker app.
4. Resume only after N healthy ticks; log the event.

### 3.2 Broker API errors or outage
1. Do not retry order placement manually through scripts.
2. Open the broker's own app and verify positions and open orders.
3. Let the Reconciler run; if it reports a mismatch, leave trading halted.
4. Resolve differences manually in the broker app, then restart the bot after reconciliation passes.

### 3.3 Order stuck (SUBMITTED without ack)
1. The Order Manager queries the broker; do not resend.
2. If the broker shows the order, the bot adopts it; if not, it marks it failed after the timeout.
3. Review the log entry for the cause.

### 3.4 Position mismatch
1. Trading is halted. Do nothing in the bot yet.
2. Compare the dashboard with the broker app; the broker is the truth.
3. Identify the cause (manual trade, corporate action, missed fill, bug).
4. Correct internal state via the reconciliation tool, write an incident note, add a regression test.

### 3.5 Daily loss limit hit
1. Bot blocks new entries (and flattens if configured).
2. Do not raise limits that day.
3. Review in the evening; write a short note before the next session.

### 3.6 Drawdown or weekly breaker
1. Bot halts. Revert to `PAPER` if in live.
2. Mandatory review: is this normal variance, a regime change, a bug, or strategy decay?
3. Restart only after a written decision.

### 3.7 Unhandled exception
1. Bot halts automatically.
2. Collect logs, correlation IDs and the failing inputs.
3. Verify positions in the broker app; ensure stops are in place.
4. Fix, add a regression test, run replay tests, resume paper first.

### 3.8 VPS or app down
1. Watchdog alert arrives; check the VPS provider console.
2. Open the broker app; confirm stops and positions.
3. If stops are missing, place them or flatten manually in the broker app.
4. Restore service, reconcile, resume only after confirmation.

### 3.9 Suspected compromise
Follow `security.md`, section 9. First actions: kill switch, rotate broker secret, verify positions in the broker app.

## 4. Emergency manual steps (broker app)

1. Open the broker's app or website directly.
2. Cancel all open orders.
3. Check each position's stop; place stops or close positions as needed.
4. Revoke the API session or rotate the API secret in the broker console if compromise is possible.
5. Disable the bot's live mode (stop the containers).

## 5. Startup and shutdown

**Start (live):** start services, log in to the broker, verify mode is what you intend, run reconciliation, confirm the live-mode prompt, then enable trading.

**Stop:** disable new entries, let orders settle, confirm exchange-side stops exist, then stop services. Never stop the app with unprotected live positions.

## 6. Change procedure

1. Change made in a branch; tests and replay regression pass.
2. Deployed to paper after market close.
3. Observed for at least one full trading day in paper.
4. Promoted to live only if it touches nothing in the risk or execution path, or if it passed the extended gates in `testing-and-go-live.md`.
5. Config and code versions noted in the daily report.

## 7. Contacts and references (fill in)

- Broker support and emergency number: ________
- VPS provider console: ________
- Alert channels (Telegram, email): ________
- Backup location and restore steps: ________
- Accountant or tax advisor: ________
