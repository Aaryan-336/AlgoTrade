# Risk Management

The Risk Engine is the most important component. It is deliberately simple, heavily tested and independent of strategies.

## 1. Defense in depth

| Layer | What it does |
|-------|--------------|
| 1. Strategy filters | Liquidity, regime and event filters reduce bad ideas |
| 2. Decision vetoes | Sentiment, event and exposure vetoes |
| 3. **Pre-trade Risk Engine** | Hard checks on every order; final authority |
| 4. Order Manager | Idempotency, rate limits, state machine |
| 5. Exchange-side protection | Stop-loss orders live at the broker |
| 6. Circuit breakers | Daily/weekly/drawdown halts, stale-data halts |
| 7. Watchdog + kill switch | Independent process that can stop everything |
| 8. Reconciliation | Detects divergence from the broker |
| 9. Human review | Daily and weekly review; approval mode for early live |

## 2. Default limits (`risk_config.yaml`)

All values are **starting defaults for paper trading**. They are percentages so they scale with capital. Tighten, never loosen, for live.

| Limit | Default | Notes |
|-------|---------|-------|
| `risk_per_trade_pct` | 0.5 to 1.0% of equity | Risk to stop, not position size |
| `max_position_pct` | 20% of equity | Single stock cap |
| `max_sector_pct` | 35% of equity | Sector concentration |
| `max_open_positions` | 5 | Reduce further at very small capital |
| `max_daily_loss_pct` | 2% | Breaker: halt new orders for the day |
| `max_weekly_loss_pct` | 4% | Breaker: halt until manual restart |
| `max_drawdown_pct` | 10% from equity peak | Breaker: halt and mandatory review |
| `max_orders_per_minute` | 5 | Also bound by broker limits (verify) |
| `max_orders_per_day` | 20 | Prevents runaway loops |
| `max_order_value` | Configurable absolute cap | Fat-finger protection |
| `max_price_deviation_pct` | 2% from last traded price | Reject limit orders outside this |
| `max_adv_participation_pct` | 1% of average daily volume | Liquidity cap |
| `min_cash_buffer_pct` | 10% | Always keep some cash free |
| `max_data_staleness_sec` | 10 (bar-based: 2 bar intervals) | Breaker for stale feed |
| `no_entry_first_minutes` | 5 | Avoid opening noise |
| `no_entry_last_minutes` | 15 | Avoid closing noise |
| `max_holding_days` | Per strategy | Time stop |

## 3. Pre-trade checks (every order, in order)

1. Mode and kill-switch state permit trading.
2. Market open and within allowed time window.
3. Data fresh and validated for this symbol.
4. Instrument tradable (not banned, not circuit-locked, not restricted).
5. Price sane: inside price band and within deviation of last traded price.
6. Size: quantity valid (lot size, tick size), positive after all caps.
7. Value: within `max_order_value` and cash/margin availability, after estimated costs.
8. Exposure: position, sector and portfolio limits respected after the trade.
9. Open positions count and order-rate limits respected.
10. Loss limits: daily, weekly, drawdown breakers not tripped.
11. Stop defined, and stop distance sensible (not zero, not absurdly wide).
12. Duplicate check: idempotency key not seen.
13. For F&O: defined-risk only, premium-at-risk and lot caps respected, time-to-expiry rules met.
14. In approval mode: owner confirmation present and not expired.

Any failure rejects the order and writes a `risk_events` row with the exact reason. Checks never partially pass.

## 4. Stop policy

- Every entry defines a stop before the order is sent.
- **Live:** place exchange-side stop orders (SL / SL-M, or GTT for delivery where suitable) immediately after the entry fills; reconcile them continuously. If the stop cannot be placed, the position is closed or the order is not allowed to proceed.
- **Paper:** simulate stop triggers realistically, including gaps (a gap through the stop fills at the gap price, not the stop price).
- Trailing stops move only in the favorable direction.

## 5. Circuit breakers

| Breaker | Trips when | Effect | Reset |
|---------|-----------|--------|-------|
| Daily loss | Realized + unrealized loss exceeds limit | Block new entries; optionally flatten | Next trading day, automatic or manual (config) |
| Weekly loss | Weekly limit exceeded | Block all entries | Manual |
| Drawdown | Peak-to-trough limit exceeded | Halt all trading | Manual with review note |
| Stale data | Feed older than threshold | Block new entries | Auto when feed recovers, after N healthy ticks |
| Broker error | Repeated API failures | Block new orders | Manual after reconciliation |
| Reconciliation | Position or order mismatch | Halt | Manual |
| Reject storm | Too many broker rejects in window | Halt | Manual |
| Unhandled exception | Any, in trading path | Halt | Manual |

## 6. Kill switch

- Runs in the **watchdog process**, separate from the main app.
- Triggers: manual (dashboard, Telegram command, CLI), watchdog health loss, breaker escalation.
- Actions, configurable: (a) block new orders; (b) cancel open orders; (c) flatten positions.
- Tested weekly in paper and before enabling any live mode.
- Must work if the main app is hung: it talks directly to the broker adapter and database with its own credentials scope.

## 7. Cost and small-capital realism

At around ₹5,000 capital, fixed charges (for example per-sell DP charges on delivery, verify current) can be a large share of a trade. The Risk Engine includes an **expected-edge check**: reject trades whose expected gain does not clear estimated round-trip costs by a margin. The cost model values live in config and must be verified against the broker's current charge calculator.

## 8. F&O risk (Phase 5+)

- Defined-risk only: long options or spreads; no naked selling.
- Max premium at risk per trade and per day; max lots; max number of open option positions.
- Exit rules before expiry day cutoff; theta-based time stops.
- Margin check with buffer before every order.
- Liquidity filter on strikes (bid-ask spread and volume).
- Separate, tighter daily loss limit for the F&O book.

## 9. Risk reporting

- Daily report: P&L, drawdown, exposure by stock and sector, limits used, rejected orders with reasons, breaker events.
- Weekly review checklist in `runbook.md`.
- Any breaker event requires a short written note (what happened, why, what changes) before restart.

## 10. What risk management cannot do

It limits losses; it does not prevent them. Gaps, halts, bad fills, broker outages and model error can still cause losses beyond a stop. The defense is small size, diversification, exchange-side stops, and only risking money you can afford to lose.
