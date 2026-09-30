# Roadmap

Phases are sequential. Each has exit criteria; do not start the next phase until they are met.

## P0: Foundations (week 0 to 1)

- Repo, CI, linting, typing, pre-commit, secrets handling.
- Postgres + Redis (Docker Compose), migrations.
- This doc set finalized.
- **Exit:** repo builds, tests run in CI, no secrets in history.

## P1: Paper core (weeks 1 to 4)

- `MarketDataProvider` interface with a `ReplayProvider` and one free provider.
- Bar builder and indicator library.
- Strategy plugin interface; one trend strategy.
- `PaperBroker` with slippage, costs, partial fills, rejections.
- Portfolio service and audit log.
- Minimal dashboard: charts, positions, orders.
- **Exit:** a full simulated trading day runs end to end from replay data with reproducible results.

## P2: Safety and research (weeks 4 to 7)

- Risk Engine with all pre-trade checks and circuit breakers.
- Kill switch (separate process) and watchdog.
- Backtester using the same strategy code; realistic cost model.
- Two more strategies; walk-forward test harness.
- Telegram alerts and daily report.
- **Exit:** Risk Engine tests above 95% coverage with property tests; chaos tests pass; backtests reproduce.

## P3: Sentiment and scanning (weeks 7 to 10)

- News and announcement ingestion; dedupe; symbol mapping.
- LLM sentiment with structured output and validation.
- Universe scanner with liquidity filters.
- Portfolio-aware exit and trim logic.
- **Exit:** sentiment improves or at least does not harm backtest results; prompt-injection tests pass.

## P4: Validation and gated live (weeks 10 to 22)

- Live data provider integration (if needed).
- **Paper validation period:** minimum 8 to 12 weeks of live-market paper trading.
- Kite adapter, reconciliation, exchange-side stops.
- `LIVE_APPROVAL` with tiny per-order and per-day caps.
- **Exit:** all go-live gates in `testing-and-go-live.md` pass.

## P5: F&O paper (after P4, capital permitting)

- Option chain, IV, Greeks features.
- Defined-risk options strategies and index futures, paper only.
- **Exit:** at least 8 weeks of paper results with risk compliance and positive expectancy after costs.

## P6: F&O live (tiny)

- Only with sufficient capital, defined-risk strategies, `LIVE_APPROVAL` first.
- **Exit:** reviewed monthly; any risk breach returns the system to paper.

## Always-on tracks

- Weekly review of logs, rejected orders and risk events.
- Dependency and security updates.
- Regulation and broker-rule checks (monthly).

## Stop conditions (any phase)

Return to `PAPER` and investigate if: a risk limit is breached, a position mismatch appears, an unhandled exception occurs in market hours, or live results diverge materially from paper.
