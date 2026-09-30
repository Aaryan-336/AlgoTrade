# Architecture

## 1. Principles

1. **One code path.** Paper and live share strategy, risk and order code; only the broker adapter differs.
2. **Risk is independent.** The Risk Engine sits between every decision and every order. Strategies cannot call brokers directly.
3. **Fail closed.** Unknown state, stale data or errors mean no new orders.
4. **Broker is the source of truth** for positions and orders in live mode; the bot reconciles constantly.
5. **Everything is logged** to an append-only audit trail.
6. **LLMs advise, code decides.** Sentiment is a structured input with no authority to place orders.
7. **Simple first.** A modular monolith, not microservices.

## 2. High-level view

```
                 +-------------------+        +----------------------+
  Data sources   |  Market Data      |        |  News Ingestor       |
  (feed, RSS,    |  Gateway          |        |  + Sentiment Service |
  exchange)  --> |  (provider        |        |  (LLM, structured)   |
                 |   adapters)       |        +----------+-----------+
                 +---------+---------+                   |
                           | ticks/bars                  | sentiment records
                           v                             v
                 +-------------------+        +----------------------+
                 |  Bar Builder +    | -----> |  Strategy Engine     |
                 |  Feature Engine   |        |  (plugins)           |
                 +-------------------+        +----------+-----------+
                                                         | signals
                          +------------------+           v
                          | Portfolio Service| --> +----------------------+
                          | (positions, PnL) |     |  Decision Engine     |
                          +--------^---------+     |  (score + context)   |
                                   |               +----------+-----------+
                                   |                          | order intents
                                   |                          v
                                   |               +----------------------+
                                   +-------------- |  RISK ENGINE (veto)  |
                                                   +----------+-----------+
                                                              | approved orders
                                                              v
                                                   +----------------------+
                                                   |  Order Manager (OMS) |
                                                   |  state machine       |
                                                   +----+------------+----+
                                                        |            |
                                           +------------v--+   +-----v-----------+
                                           | PaperBroker   |   | KiteBroker      |
                                           | (simulated)   |   | (live adapter)  |
                                           +---------------+   +-----------------+

  Side systems:  Audit Log | Reconciler | Watchdog + Kill Switch (separate process)
                 Dashboard (Next.js) | Alerts (Telegram) | Backtester (same strategy code)
```

## 3. Components

| Component | Responsibility | Notes |
|-----------|----------------|-------|
| Market Data Gateway | Normalize data from any provider into ticks/bars | Adapters: Replay, free feed, Kite WebSocket; see `data-sources.md` |
| Bar Builder | Aggregate ticks into OHLCV; validate and store | Detects gaps, duplicates, bad ticks |
| Feature Engine | Indicators and derived features | Pure functions, unit tested |
| News Ingestor | Pull announcements and RSS; dedupe; map to symbols | Runs as scheduled jobs (n8n optional) |
| Sentiment Service | LLM returns validated JSON; stores records with decay | News text is untrusted input |
| Strategy Engine | Runs strategy plugins; emits signals | Stateless per call where possible |
| Decision Engine | Combines signals, sentiment, portfolio context into order intents | Scoring and vetoes in `strategy-spec.md` |
| Risk Engine | Pre-trade checks, limits, circuit breakers | Details in `risk-management.md` |
| Order Manager | Order lifecycle, idempotency, retries, timeouts | State machine in `design.md` |
| Broker Adapters | `PaperBroker`, `KiteBroker` behind one interface | Only place that knows broker specifics |
| Portfolio Service | Positions, cash, PnL, exposure | Rebuilt from fills; verified vs broker |
| Reconciler | Compares internal state to broker every N seconds | Mismatch triggers halt |
| Watchdog + Kill Switch | Independent process monitoring health and limits | Can halt trading even if main app hangs |
| Backtester | Event-driven replay with cost model | Reuses strategy and risk code |
| Dashboard | Charts, positions, orders, risk status, controls | Protected behind auth and private network |
| Alerts | Telegram messages and daily report | Also used for approval prompts in `LIVE_APPROVAL` |

## 4. Technology choices

| Layer | Choice | Reason |
|-------|--------|--------|
| Core services | Python 3.12, FastAPI | Fits the owner's stack; rich finance libraries |
| Data store | PostgreSQL (TimescaleDB optional) | Reliable, relational, good for time series at this scale |
| Cache / pub-sub / locks | Redis | Fast messaging, distributed locks, rate limiting |
| Dashboard | Next.js + TradingView Lightweight Charts | Free, open-source charting library |
| Workflow glue | n8n (optional) | News ingestion, schedules, alerts; never order placement or risk |
| Broker | Zerodha Kite Connect | Chosen broker |
| LLM | Claude or Groq API | Structured sentiment extraction |
| Packaging | Docker Compose | Reproducible local and VPS setup |
| Observability | Structured logs, Prometheus metrics, simple Grafana (optional) | Enough for a single-user system |

## 5. Runtime flow (one decision cycle)

1. Bar closes; Bar Builder validates and stores it.
2. Feature Engine computes indicators.
3. Strategy Engine emits signals for candidates and holdings.
4. Decision Engine adds sentiment and portfolio context, produces scored order intents.
5. Risk Engine runs all checks; rejects or approves (with adjusted size).
6. Order Manager creates the order with an idempotency key.
7. Broker Adapter executes (simulated or real); fills come back.
8. Portfolio Service updates; Audit Log records the whole chain; alerts fire.
9. Reconciler verifies state against the broker.

## 6. Deployment

| Stage | Where | Notes |
|-------|-------|-------|
| Development and paper | Laptop is acceptable | No money at risk |
| Live-approval and beyond | VPS with static IP | Required for broker API orders (verify); runs 24/7 |

VPS layout: Docker Compose with `app`, `worker`, `watchdog`, `postgres`, `redis`, `dashboard`. Database and Redis are not exposed publicly. Dashboard is reachable only through a VPN or private tunnel.

## 7. Failure handling (summary)

| Failure | Behavior |
|---------|----------|
| Feed stale beyond threshold | Halt new entries; alert |
| Broker API errors | Halt new orders; do not retry blindly; reconcile |
| Main app crash | Watchdog alerts; exchange-side stops continue protecting open positions |
| DB unavailable | Halt trading; no in-memory-only trading |
| Position mismatch | Halt, alert, require manual resolution |
| LLM failure | Sentiment treated as missing; strategies continue with neutral sentiment, or pause entries per config |

Full procedures are in `runbook.md`.

## 8. Architecture decisions to record (ADR log)

Keep a short `docs/adr/` folder. Initial decisions:

- ADR-001: Modular monolith over microservices.
- ADR-002: Provider abstraction for market data; TradingView is not used as a data source.
- ADR-003: Risk Engine as a mandatory gate; no direct broker access for strategies.
- ADR-004: LLM output limited to validated structured sentiment.
- ADR-005: Delivery/swing equities first; F&O gated.
