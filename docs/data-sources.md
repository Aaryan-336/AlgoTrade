# Data Sources

## 1. The TradingView question

You proposed TradingView's API as a free real-time feed. Here is the reality, because it affects the whole design:

- TradingView does **not** offer a free, official real-time market data API for retail developers.
- What TradingView provides officially is **display tooling**: embeddable widgets and the charting libraries. Data in those widgets is for viewing, not for feeding your trading logic.
- Unofficial libraries that tap TradingView's websocket exist, but they use the service outside its terms, break without notice, and can get your account blocked. A trading bot must not depend on that.
- TradingView alerts with webhooks can send signals to your server, but webhooks need a paid plan and only carry alert messages, not a data feed.

**Decision:** TradingView is **not** a data source. We use its free open-source **Lightweight Charts** library for dashboard charts only.

## 2. Options for the feed

| Option | Cost | Quality | Use for |
|--------|------|---------|---------|
| **Kite Connect WebSocket** | Paid data plan (verify current price) | Real-time, reliable; same ecosystem as execution | Live and validation phases |
| **Free broker APIs** (e.g. Upstox, Angel One SmartAPI, Dhan; terms vary, verify) | Often free with a broker account | Real-time, but tied to that broker's rules and uptime | Paper-phase feed if you open an account there |
| **Free delayed/public sources** (e.g. yfinance, exchange end-of-day files) | Free | Delayed, sometimes wrong or rate-limited | Development, daily-bar research, backtests; never for live decisions |
| **Recorded tick/bar replay** | Free | Deterministic | Testing, chaos tests, regression |
| Commercial data vendors | Paid | High | Only if a strategy truly needs it |

## 3. Why bar-based decisions make this easier

Phase 1 strategies run on **daily and 15-minute bars**. That means:

- A free or slightly delayed feed is often adequate for paper trading.
- No tick-level latency engineering is needed.
- The bot can recompute state from stored bars after a restart.

Tick-level or sub-minute strategies are out of scope (see `scope.md`).

## 4. Provider abstraction

All providers implement `MarketDataProvider` (see `design.md`). Consequences:

- Switching from a free feed to Kite later is a config change plus an adapter, not a rewrite.
- The same interface serves replay for tests and backtests.
- Provider health (last tick time, latency) feeds the staleness circuit breaker.

## 5. Data validation rules

Every incoming tick or bar is checked before use:

1. Timestamp is within market hours and not in the future; clock skew is monitored.
2. Price is positive and within the exchange price band.
3. Change vs the previous price does not exceed a sanity threshold (bad-tick filter).
4. Volume is non-negative; cumulative volume does not decrease.
5. No duplicate or out-of-order bars; gaps are detected and flagged.
6. Symbol mapping is exact (exchange, ISIN); corporate actions (splits, bonus, dividends) are adjusted in history.

Invalid data is quarantined and logged. If quarantined data exceeds a threshold, trading halts.

## 6. News and announcement sources

| Source | Notes |
|--------|-------|
| BSE/NSE corporate announcements | Primary, official; free; highest credibility |
| Company filings and results | Structured where possible |
| RSS feeds from major financial outlets | Check each site's terms; store headlines and links, not full copies |
| Paid news APIs | Optional later; evaluate cost vs benefit |
| Social media | Low credibility; excluded initially |

Each item stores source, timestamp, URL hash and a credibility tier; sentiment is weighted by tier.

## 7. Fundamental and ownership data

Optional, slow-moving inputs (valuation, growth, promoter holding, pledging). Sources: exchange filings, broker fundamentals endpoints, or periodically refreshed public datasets. Treated as filters or score components, refreshed daily or weekly, never intraday.

## 8. Storage and retention

- Bars: keep all timeframes used by strategies; daily bars indefinitely.
- Ticks: not stored long term unless needed for replay tests; if stored, compress and rotate.
- News: store metadata and the model's structured output; keep raw text only as long as needed and permitted.

## 9. Open decisions

1. Which paper-phase feed: a free broker API or delayed public data? Decide in P1.
2. Whether to pay for Kite's data plan before `LIVE_APPROVAL`. Needed for real-time live data; verify current pricing and terms.
3. Whether any strategy needs option-chain history (P5), and the cost of that data.
