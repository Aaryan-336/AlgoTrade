# Strategy Specification

Strategies are **plugins** that emit signals. They never place orders, never check risk, and never read the clock or call the network. Risk, sizing caps and execution belong to other modules.

## 1. Philosophy on "a really well trained bot"

- Start **rule-based and explainable**. Every trade must be traceable to named signals.
- Machine learning is added later, only with enough clean data, walk-forward validation and a strong out-of-sample record. A model that looks brilliant in a backtest is usually overfitted.
- "Trained" here means **tested, tuned with discipline and validated in paper trading**, not a black box trusted blindly.
- Sentiment is a **filter and modifier**, not a standalone trigger.

## 2. Universe and candidate selection

- **Holdings:** always evaluated each cycle for hold, trim or exit.
- **Candidates:** drawn from the configured universe after filters:
  - Minimum average daily traded value (liquidity).
  - Not in ban list, not in surveillance/ASM/GSM stages that restrict trading (verify lists).
  - Price above a minimum; not at circuit limits.
  - Enough history for indicators.
- Default universe is a liquid subset; full-market scanning is optional and off by default.

## 3. Phase 1 strategies (equities)

### 3.1 Trend following (primary)

| Component | Default |
|-----------|---------|
| Trend | Close above 50 EMA, and 20 EMA above 50 EMA |
| Momentum | RSI(14) between 50 and 70 |
| Volume | Volume above 20-day average |
| Stop | Entry minus `k * ATR(14)` (default k = 2) |
| Target | Risk-reward of at least 2 (configurable); trailing stop after 1R |
| Exit | Close below 20 EMA, stop hit, target hit, or exit score reached |

### 3.2 Breakout

- Enter on a close above the N-day high with volume confirmation and low recent volatility compression.
- Stop below breakout level or ATR-based; reject if gap is too large.

### 3.3 Mean reversion (optional)

- Short-horizon RSI or Bollinger extremes in a confirmed larger uptrend.
- Short holding periods; strict time stop.
- Disabled in strongly trending or high-VIX regimes unless tested otherwise.

All default values are starting points for research, not recommendations. Final parameters must come from walk-forward testing.

## 4. Market regime filter

The bot reduces or stops new entries when the market regime is unfavorable:

- Index below its long moving average and breadth weak: lower size or no new long entries.
- India VIX above a threshold: reduce size.
- Major scheduled events (budget, central bank decisions, results days for the stock): block or reduce.

## 5. Sentiment integration

- Sentiment per symbol is the time-decayed, confidence-weighted average of recent items.
- **Veto:** strongly negative, material sentiment (fraud, regulatory action, rating downgrade) blocks new entries and can trigger review of existing holdings.
- **Modifier:** moderately positive sentiment can raise the entry score or allow slightly larger size (within risk caps).
- **Novelty check:** if price has already moved sharply on the news, the edge may be priced in; reduce its weight.
- If sentiment data is missing or stale, it is treated as neutral and logged, never as positive.

## 6. Portfolio-aware logic

For each holding the bot considers:

- Current P&L against stop and target; trailing stop status.
- Position weight vs `max_position_pct`; sector exposure vs `max_sector_pct`.
- Holding period vs tax thresholds (short-term vs long-term), as an input to exit timing, not the sole driver.
- Correlation with other holdings; avoid adding highly correlated positions.
- Cash available after reserving a minimum cash buffer.

## 7. Scoring and vetoes

See `design.md` for the score formula. Hard vetoes (any one blocks an entry):

1. Risk Engine rejection (always final).
2. Data stale or invalid.
3. Illiquid, banned or circuit-locked stock.
4. Negative material sentiment.
5. Earnings or major event within a configured window.
6. Regime filter says no new entries.
7. Position or sector limit reached.

## 8. Exits

Exits are as important as entries. Every position has, at entry time:

- A stop price (placed exchange-side in live mode where possible).
- A target or trailing rule.
- A maximum holding time.

Exit triggers: stop, target, trailing stop, time stop, exit score, negative material news, risk rule (daily loss, drawdown), kill switch.

## 9. F&O (Phase 5+, paper first)

Scope is deliberately narrow:

- **Allowed first:** index options **buying** (defined risk) and defined-risk spreads; index futures in paper only.
- **Not allowed:** naked option selling, undefined-risk positions, holding into expiry without explicit rule.
- **Inputs:** underlying trend, IV and IV percentile, days to expiry, open interest and put-call ratio, Greeks (delta, theta, vega), liquidity of the strike (spread and volume).
- **Selection:** strike and expiry chosen by rules (for example a delta band and minimum days to expiry); reject wide-spread or illiquid contracts.
- **Risk:** maximum premium at risk per trade and per day; max lots; theta-aware time stops; forced exit before expiry day cutoff.
- F&O requires capital far above ₹5,000 for meaningful operation (see `scope.md`); it stays paper-only until that is resolved.

## 10. Strategy lifecycle

1. Hypothesis written down (what edge and why).
2. Implemented as a plugin with unit tests.
3. Backtested with costs on in-sample data, then walk-forward and out-of-sample.
4. Paper traded in live markets.
5. Promoted only when it passes the gates in `testing-and-go-live.md`.
6. Monitored; disabled automatically if live behavior diverges from expectations (drawdown or hit-rate drift).

## 11. Parameter discipline

- Few parameters per strategy; avoid optimizing many at once.
- Prefer stable parameter regions over a single "best" value.
- Record every tested variant to avoid hidden multiple-testing bias.
- Never tune on the out-of-sample window.
