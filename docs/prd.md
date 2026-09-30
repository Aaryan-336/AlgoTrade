# PRD: Personal Algo Trading Bot

## 1. Summary

A personal trading assistant that watches Indian markets, evaluates the user's existing holdings and candidate stocks, combines technical signals with news sentiment, and decides to buy, sell or hold. It first runs on simulated orders (paper trading). After a strict validation period, the same system sends real orders through the broker API (Zerodha Kite).

## 2. Problem

- Manual trading is slow, emotional and inconsistent.
- Watching many stocks and reading all news by hand is not possible.
- Existing no-code platforms do not combine portfolio-aware decisions, custom news sentiment and strict personal risk rules in one place.

## 3. User

Single user (the owner). No multi-tenant features. No handling of other people's money, which keeps the project outside broker / adviser licensing territory (confirm with a professional before changing this).

## 4. Goals

| ID | Goal |
|----|------|
| G1 | Paper trade with realistic fills and costs, using the exact code path that live trading will use |
| G2 | Produce explainable decisions: every trade records the signals, scores and risk checks behind it |
| G3 | Enforce risk limits that no strategy can override |
| G4 | Make the switch from paper to live a configuration change plus passed gates, not a rewrite |
| G5 | Be safe by default: fail closed, reconcile with the broker, keep an audit trail |
| G6 | Support swapping strategies without touching risk or execution code |

## 5. Non-goals

- Guaranteed or "flawless" profits.
- High-frequency or latency-sensitive trading.
- Managing money for others, selling signals, or giving advice.
- Crypto, forex or commodities (for now).
- Fully autonomous live trading from day one.

## 6. User stories

1. As the owner, I can see live prices, charts and my portfolio on a dashboard.
2. As the owner, I can choose a strategy and change its parameters without editing code.
3. As the owner, I can run the bot in paper mode and compare its results with a backtest.
4. As the owner, I can see why any trade was taken or rejected.
5. As the owner, I get alerts (Telegram) for trades, rejections, risk events and errors.
6. As the owner, I can stop all trading instantly with a kill switch.
7. As the owner, I can require manual approval for each live order.
8. As the owner, I can review daily and weekly performance reports.

## 7. Functional requirements

### Data
- **FR-001** Ingest market data via a pluggable provider (see `data-sources.md`).
- **FR-002** Build and store OHLCV bars at the configured timeframes.
- **FR-003** Ingest news and exchange announcements for the watchlist and holdings.
- **FR-004** Score news with an LLM into a structured sentiment record (never free text into decisions).

### Decisions
- **FR-010** Compute indicators and features from bars.
- **FR-011** Run one or more strategies that emit signals (`BUY`, `SELL`, `HOLD`, strength, reason).
- **FR-012** Combine signals with sentiment and portfolio context into a scored decision.
- **FR-013** Evaluate existing holdings for exit or trim, and scan the universe for new entries.

### Risk and execution
- **FR-020** Every order intent passes the Risk Engine, which can veto but never be bypassed.
- **FR-021** Orders follow a tracked state machine with idempotency keys.
- **FR-022** Paper broker simulates slippage, spreads, costs, partial fills and rejections.
- **FR-023** Live broker adapter sends orders via Kite Connect and reconciles positions.
- **FR-024** Kill switch halts new orders and optionally flattens positions.
- **FR-025** Live stop-losses are placed exchange-side where the broker supports it.

### Research and operations
- **FR-030** Backtester replays history through the same strategy code.
- **FR-031** Dashboard for charts, positions, orders, signals, risk status and logs.
- **FR-032** Telegram alerts and daily reports.
- **FR-033** Full audit log of signals, decisions, orders, fills and config changes.
- **FR-034** F&O support (Phase 5+): futures and options, defined-risk strategies first.

## 8. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-01 | Fail closed: stale data, broker errors or unknown state means no new orders |
| NFR-02 | Decisions reproducible from stored data and config version |
| NFR-03 | Secrets never in code or logs |
| NFR-04 | Runs unattended on a VPS during market hours (9:15 to 15:30 IST) |
| NFR-05 | Recoverable: on restart, state is rebuilt from the database and reconciled with the broker |
| NFR-06 | Decision latency under 2 seconds for bar-based strategies |
| NFR-07 | Test coverage of the Risk Engine and Order Manager above 95% with property tests |

## 9. Success metrics

Metrics judge **process quality first, profit second**.

| Metric | Target |
|--------|--------|
| Risk-limit breaches | 0 |
| Unreconciled position mismatches | 0 |
| Paper vs backtest divergence | Explained and within tolerance |
| Unhandled exceptions during market hours | 0 over the validation period |
| Expectancy after costs (paper) | Positive over the validation period |
| Max drawdown (paper) | Within configured limit |

Go-live gates are defined in `testing-and-go-live.md`.

## 10. Assumptions

- Broker is Zerodha (Kite Connect); daily login and static IP are required (verify).
- Starting capital is small (around ₹5,000); strategy and fee assumptions must reflect this.
- The owner codes in Python and TypeScript, and will build using Claude Code.

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Overfitted strategy looks good in backtest, fails live | Walk-forward testing, out-of-sample data, long paper period |
| Bad or stale data triggers wrong trades | Data validation, staleness halt, price sanity checks |
| Bug sends repeated or wrong orders | Idempotency, rate limits, max order size, kill switch |
| LLM misreads or is manipulated by news text | Structured output only, schema validation, no direct trade authority |
| Fees dominate at small capital | Cost model in paper mode; favor low-turnover strategies |
| Regulatory change | Track SEBI and broker circulars; keep compliance checklist in `security.md` |

## 12. Open questions

1. Delivery (CNC) vs intraday (MIS) for Phase 1? Recommendation: delivery/swing on daily and 15-minute bars.
2. Which live data source: Kite data plan, or a free broker feed for paper only? See `data-sources.md`.
3. Universe size: full BSE scan vs liquid subset? Recommendation: start with a liquid subset (see `scope.md`).
4. When does F&O begin, given capital constraints? Gated on capital and Phase 4 results.
