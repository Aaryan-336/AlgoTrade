# Testing and Go-Live Gates

## 1. Test pyramid

| Level | What | Notes |
|-------|------|-------|
| Unit | Indicators, scoring, sizing, cost model, state transitions | Fast, deterministic |
| Property-based | Risk Engine and Order Manager invariants | Use Hypothesis; thousands of random cases |
| Integration | Data provider to bars to strategy to risk to paper broker | Uses replay data |
| Replay / regression | Full historical days with expected outputs | Any change in output must be explained |
| Chaos / fault injection | Kill feed, DB, broker, duplicate messages, clock skew | Must fail closed |
| Security | Secret scan, dependency audit, prompt-injection suite | Runs in CI |
| Live-paper | Real market hours, simulated orders | The validation period |

## 2. Risk Engine and OMS property tests (examples)

- No sequence of order intents can exceed position, sector, exposure or order-count limits.
- An order never reaches a broker adapter without a prior `RISK_APPROVED` event.
- Duplicate intents produce one order.
- Quantity is never rounded up past a cap.
- After any breaker trips, no order is approved until manual reset.
- State machine rejects every illegal transition.
- On restart, state rebuilt from the database equals state before shutdown.

## 3. Chaos scenarios (must all pass)

1. Feed stops mid-session: entries halt, alert fires, existing stops remain.
2. Feed sends an outlier tick (for example a 50% jump): tick quarantined, no trade.
3. Broker API returns errors or timeouts: no blind retries, reconcile, halt if needed.
4. Order ack lost: bot queries the broker, does not resend.
5. Duplicate signal within one bar: one order only.
6. Database connection drops: trading halts.
7. Process is killed mid-order: on restart, reconcile and resolve.
8. Clock skew injected: bot detects and halts.
9. LLM returns garbage or an injection attempt: discarded, sentiment neutral.
10. Watchdog loses heartbeat from the app: alert and, if configured, kill switch.

## 4. Backtesting standards

- Same strategy, scoring and risk code as live; same cost and slippage model as `PaperBroker`.
- Include all charges (brokerage, STT, exchange fees, GST, stamp duty, DP charges; verify values) and pessimistic slippage.
- Use survivorship-bias-free data where possible; adjust for corporate actions.
- No look-ahead: signals use only data available at the decision time; fills occur after it.
- Split data: in-sample (develop), validation (select), out-of-sample (touch once).
- Walk-forward analysis across multiple regimes (bull, bear, sideways, high volatility).
- Report: CAGR, volatility, Sharpe and Sortino, max drawdown, win rate, average win/loss, expectancy per trade, turnover, exposure, cost as a share of gross profit.
- Sensitivity: results must survive small parameter changes and higher costs.
- Record run metadata (commit, config version, data range).

## 5. Paper-validation requirements

- Runs in **real market hours** with the production code path and config.
- Minimum **8 to 12 weeks** and at least **100 completed trades** (or fewer trades over a longer period for low-turnover strategies; the owner sets this before starting, not after seeing results).
- Must include varied conditions (at least one sharp market drop or high-volatility period if it occurs; otherwise extend).
- Daily review of logs, rejected orders and risk events.

## 6. Go-live gates (all must pass before `LIVE_APPROVAL`)

| # | Gate | Criterion |
|---|------|-----------|
| 1 | Process safety | Zero risk-limit breaches and zero unhandled exceptions in market hours during validation |
| 2 | Reconciliation | Internal state equals broker-simulated state at every check; live adapter passed reconciliation tests in sandbox or with dry runs |
| 3 | Performance | Positive expectancy after costs over the validation period; max drawdown within the configured limit |
| 4 | Consistency | Paper results vs backtest divergence explained and within pre-agreed tolerance |
| 5 | Robustness | All chaos scenarios pass; kill switch tested within the last 7 days |
| 6 | Security | Security checklist complete; secrets and VPS hardening verified; static IP registered |
| 7 | Compliance | Checklist in `security.md` reviewed and confirmed |
| 8 | Capital | Amount at risk is money the owner can afford to lose; per-order and per-day caps set |
| 9 | Readiness | Runbook rehearsed; alerts confirmed reaching the owner |

Passing gates means "safe enough to try with tiny money", not "profitable forever".

## 7. Promotion ladder

| Step | Mode | Conditions to move up |
|------|------|-----------------------|
| 1 | `PAPER` | Gates 1 to 9 pass |
| 2 | `LIVE_APPROVAL` (tiny caps, e.g. one share or a small rupee cap per order) | At least 4 weeks, no incidents, every live fill within expected slippage of paper |
| 3 | `LIVE_LIMITED` (hard daily caps, no per-order approval) | At least 4 to 8 more weeks, live tracking paper within tolerance |
| 4 | `LIVE` (size increased in small steps, reviewed monthly) | Ongoing review; any breach returns to an earlier step |

F&O repeats this ladder separately, starting from paper, after the equity ladder is stable.

## 8. Demotion triggers

Return to `PAPER` (or `LIVE_APPROVAL`) immediately if: a risk limit is breached, a reconciliation mismatch occurs, an unhandled exception happens in market hours, live results diverge materially from paper, or a rule or regulation changes.

## 9. CI pipeline

1. Lint, format and type check.
2. Unit and property tests.
3. Replay regression tests.
4. Dependency and secret scans.
5. Build images.
6. Deploy to paper environment only; promotion to live infrastructure is manual.
