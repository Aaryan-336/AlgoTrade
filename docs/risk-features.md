# Risk Features: What Is Built

The checklist of risk controls an algo trading system needs, and where each one lives in this codebase. Status is as of the first paper build (2026-09-30).

✅ built and tested · 🟡 partial · ⏳ planned (phase in brackets)

## 1. Pre-trade checks (Risk Engine, `backend/algotrade/risk/engine.py`)

Every order passes every check; any failure rejects it and writes a `risk_events` row.

| # | Check | Status |
|---|-------|--------|
| 1 | Mode is PAPER (live modes refused) | ✅ |
| 2 | Not halted by a circuit breaker | ✅ |
| 3 | Market open | ✅ |
| 4 | Entry window (no entries in first 5 / last 15 minutes) | ✅ |
| 5 | Kill switch off (entries) | ✅ |
| 6 | No entry-blocking breaker tripped | ✅ |
| 7 | Fresh last traded price (staleness limit) | ✅ |
| 8 | Price sanity: circuit-band proxy vs previous close; price moved less than 2% since the decision | ✅ |
| 9 | Instrument tradable (in universe, not banned) | ✅ |
| 10 | Event blackout days (budget, RBI, results) | ✅ |
| 11 | No averaging down / pyramiding | ✅ |
| 12 | Max open positions (including pending entries) | ✅ |
| 13 | Correlation with existing holdings | ✅ |
| 14 | Stop defined, below entry, not too tight or too wide | ✅ |
| 15 | Position sizing by risk-to-stop, capped by position %, sector %, cash after buffer, order value, liquidity (% of ADV) | ✅ |
| 16 | Expected edge: reward at target must exceed round-trip costs by a multiple | ✅ |
| 17 | Exposure re-check after sizing (defense in depth) | ✅ |
| 18 | Order rate per minute and per day | ✅ |
| 19 | Duplicate / idempotency key | ✅ |
| 20 | Long-only: entries buy, exits sell only what is held | ✅ |
| 21 | Equity positive | ✅ |
| 22 | Owner approval per order (`LIVE_APPROVAL`) | ⏳ (P4) |

## 2. Position protection

| Feature | Status |
|---------|--------|
| Stop order placed at the broker immediately after every entry fill | ✅ (paper broker holds it like an exchange) |
| Gap-through stops fill at the gap price in simulation | ✅ |
| Trailing stop after 1R, moves up only | ✅ |
| Profit target, time stop, strategy exit, score exit | ✅ |
| Exit on negative material news | ✅ |
| Protection restored if an exit fails or partially fills | ✅ |
| GTT / SL-M orders on Kite | ⏳ (P4) |

## 3. Circuit breakers (`risk/breakers.py`, `engine/core.py`)

| Breaker | Effect | Reset | Status |
|---------|--------|-------|--------|
| Daily loss | Block entries | Next day (auto) | ✅ |
| Weekly loss | Block entries | Manual, with note | ✅ |
| Drawdown from peak | Halt | Manual, with note | ✅ |
| Stale data feed | Block entries | Auto on recovery | ✅ |
| Too much quarantined data (bad ticks, clock skew) | Block entries | Manual | ✅ |
| Consecutive broker errors | Halt | Manual | ✅ |
| Broker reject storm | Halt | Manual | ✅ |
| Reconciliation mismatch | Halt | Manual | ✅ |
| Unhandled exception in the trading path | Halt | Manual | ✅ |

## 4. Order safety (`oms/`)

| Feature | Status |
|---------|--------|
| Strict order state machine; illegal transitions raise | ✅ |
| Idempotency keys, enforced across restarts | ✅ |
| No blind retries: uncertain placement is resolved by asking the broker | ✅ |
| Orders stuck in SUBMITTED after a crash are resolved on restart | ✅ |
| Never cancel an order before its ack | ✅ |

## 5. Data integrity (`data/validators.py`)

| Feature | Status |
|---------|--------|
| OHLC consistency, positive prices, non-negative volume | ✅ |
| Bad-tick jump filter (ticks 15%, daily bars 25%) | ✅ |
| Future timestamps / clock skew rejected | ✅ |
| Duplicate and out-of-order bars rejected | ✅ |
| Decisions skip symbols missing the current bar | ✅ |
| Regime filter fails closed without index data | ✅ |
| Corporate-action adjustment | 🟡 (relies on provider-adjusted history; verify) |
| Exchange ban / ASM / GSM lists | 🟡 (manual `ban_list`; automated feed ⏳) |

## 6. Independent controls

| Feature | Status |
|---------|--------|
| Kill switch: block, or block and flatten (dashboard, CLI) | ✅ |
| Watchdog process: sets kill switch if the engine heartbeat is lost | ✅ |
| Kill switch via Telegram command | ⏳ |
| Reconciliation of positions and cash every minute | ✅ |
| Risk config locked during market hours, versioned, bounded | ✅ |

## 7. LLM and news safety (`news/`)

| Feature | Status |
|---------|--------|
| LLM has no tools and no order authority | ✅ |
| Headlines passed as quoted data with an injection-resistant system prompt | ✅ |
| Strict schema validation; unknown ids, bad enums and out-of-range values dropped | ✅ |
| Missing sentiment is neutral, never positive | ✅ |
| Confidence, credibility-tier and time-decay weighting | ✅ |
| One headline cannot dominate; thin evidence shrinks to neutral | ✅ |
| Novelty check: news already priced in gets half weight | ✅ |
| No account data ever sent to the LLM | ✅ |

## 8. Audit and security

| Feature | Status |
|---------|--------|
| Hash-chained audit log with verification | ✅ |
| Append-only triggers on audit_log, order_events, fills (PostgreSQL) | ✅ |
| Secrets only in environment; Upstox token in memory only | ✅ |
| Optional API token; ports bound to localhost; non-root containers | ✅ |
| Every decision stores its config version | ✅ |
| Dashboard 2FA, VPN, static IP | ⏳ (P4, before live) |

## 9. Validation tooling

| Feature | Status |
|---------|--------|
| Backtester on the same engine, costs and slippage | ✅ |
| Realistic Zerodha delivery costs (STT, stamp, exchange, SEBI, GST, DP) | ✅ (verify values) |
| Walk-forward / parameter sweep harness | ⏳ |
| Paper vs backtest divergence report | ⏳ |
| Daily report (Telegram) | 🟡 (alerts only) |
