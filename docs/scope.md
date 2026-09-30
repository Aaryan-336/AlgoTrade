# Scope

## 1. In scope by phase

| Phase | In scope |
|-------|----------|
| **P1: Paper core** | Data provider abstraction, bar builder, indicators, one equity strategy, paper broker, portfolio tracking, basic dashboard, audit log |
| **P2: Safety and research** | Full Risk Engine, kill switch, backtester, cost model, second and third strategies, Telegram alerts |
| **P3: Sentiment and scanning** | News and announcement ingestion, LLM sentiment service, universe scanner, portfolio-aware exit logic |
| **P4: Validation and gated live** | Extended paper period, `LIVE_APPROVAL` with tiny caps, Kite adapter, reconciliation, exchange-side stops |
| **P5: F&O (paper only)** | Option chain ingestion, Greeks/IV features, defined-risk options strategies, futures paper trading |
| **P6: F&O (live, tiny)** | Only after P5 results and sufficient capital; defined-risk only |

## 2. Out of scope (for now)

- Managing other people's money, offering signals or advice, multi-user accounts.
- High-frequency trading, co-location, tick-level scalping.
- Naked option selling and undefined-risk strategies.
- Crypto, forex, commodities, international markets.
- Fully automatic scaling of position size without human review.
- Self-modifying or self-retraining strategies in production.
- Scraping websites or data feeds against their terms (including unofficial TradingView scrapers).

## 3. Instrument and universe decisions

- **Phase 1 instruments:** cash equities on NSE/BSE, delivery (CNC) by default.
- **Universe:** configurable. Default is a liquid subset (for example, index constituents or stocks above a minimum average daily traded value). Scanning every BSE scrip is supported architecturally but disabled by default because illiquid stocks cause heavy slippage and unreliable signals.
- **Timeframes:** daily and 15-minute bars. The bot is not latency sensitive.

## 4. Constraints

- **Capital:** around ₹5,000 to start. At this size fixed per-trade charges matter, so the paper cost model must be realistic and strategies should trade infrequently. F&O is not feasible at this capital and is gated on additional funds.
- **Budget:** prefer free or low-cost tools; target under ₹3,000/month all-in during validation.
- **Regulatory:** orders must go through a SEBI-registered broker's API; SEBI's retail algo framework (Algo-ID, static IP, 2FA) applies from April 2026 (verify current details).
- **Operations:** single developer; the system must be simple enough to operate and debug alone.

## 5. Assumptions

1. Zerodha Kite Connect is available and its access token must be refreshed daily.
2. A VPS with a static IP is used once real orders begin.
3. The owner reviews reports and alerts daily.
4. Free data may be delayed or incomplete, so decisions are designed around bar data, not raw ticks.

## 6. Dependencies

- Broker API (Kite Connect).
- A market data provider (see `data-sources.md`).
- LLM API for sentiment scoring.
- News sources: exchange announcements, RSS feeds.
- PostgreSQL, Redis, a VPS, Telegram bot.

## 7. Acceptance criteria per phase

Each phase ends only when its exit criteria in `roadmap.md` are met. No phase is skipped, and live trading cannot be enabled until the gates in `testing-and-go-live.md` pass.

## 8. Change control

Scope changes require updating this file and `roadmap.md` first. Anything that increases risk (new instrument class, larger size, more autonomy) also requires updating `rules.md` and `risk-management.md` and passing the relevant tests.
