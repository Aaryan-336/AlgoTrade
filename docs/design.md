# Detailed Design

## 1. Repository layout

```
algo-bot/
  apps/
    api/            FastAPI app (REST + WebSocket for dashboard)
    worker/         Scheduled and event-driven jobs
    watchdog/       Independent health monitor + kill switch
    dashboard/      Next.js UI
  core/
    data/           providers/, bar_builder.py, validators.py
    features/       indicators.py, feature_store.py
    strategies/     base.py, trend.py, breakout.py, meanrev.py, options/
    decision/       scoring.py, portfolio_context.py
    risk/           engine.py, checks/, limits.py, circuit_breakers.py
    oms/            order.py, state_machine.py, manager.py
    brokers/        base.py, paper.py, kite.py
    news/           ingest.py, sentiment.py, schemas.py
    backtest/       engine.py, cost_model.py, walk_forward.py
    audit/          logger.py
    config/         settings.py, risk_config.yaml, strategy_config.yaml
  tests/            unit/, property/, integration/, chaos/, replay/
  docs/             (this doc set) + adr/
  infra/            docker-compose.yml, Dockerfiles, nginx/tunnel config
```

## 2. Core interfaces

### 2.1 Market data provider

```python
class MarketDataProvider(Protocol):
    async def subscribe(self, symbols: list[str]) -> None: ...
    async def stream(self) -> AsyncIterator[Tick]: ...
    async def history(self, symbol: str, timeframe: str, start: datetime, end: datetime) -> list[Bar]: ...
    def health(self) -> ProviderHealth: ...   # last tick time, latency, status
```

Implementations: `ReplayProvider`, `DelayedFreeProvider`, `KiteProvider`. Adding a provider must not change any other module.

### 2.2 Strategy

```python
class Strategy(Protocol):
    name: str
    params: StrategyParams
    def evaluate(self, symbol: str, features: FeatureSet, ctx: MarketContext) -> Signal: ...

@dataclass
class Signal:
    symbol: str
    action: Literal["BUY", "SELL", "HOLD"]
    strength: float            # 0..1
    reasons: list[str]         # human-readable, stored in audit log
    suggested_stop: float | None
    suggested_target: float | None
```

Strategies are pure: no I/O, no broker calls, no clock reads (time comes from `ctx`). This makes backtests identical to live.

### 2.3 Broker adapter

```python
class Broker(Protocol):
    async def place(self, order: Order) -> BrokerAck: ...
    async def cancel(self, order_id: str) -> None: ...
    async def positions(self) -> list[Position]: ...
    async def orders(self) -> list[BrokerOrder]: ...
    async def funds(self) -> Funds: ...
```

`PaperBroker` and `KiteBroker` implement it; the OMS only sees this interface.

## 3. Data model (PostgreSQL)

| Table | Key columns |
|-------|-------------|
| `instruments` | symbol, exchange, isin, lot_size, tick_size, segment, is_active |
| `bars` | symbol, timeframe, ts, o, h, l, c, v (unique on symbol, timeframe, ts) |
| `news_items` | id, source, url_hash, published_at, headline, body_ref, symbols[] |
| `sentiment` | news_id, symbol, score (-1..1), confidence, event_type, horizon, model, prompt_version, created_at |
| `signals` | id, ts, symbol, strategy, action, strength, reasons (jsonb), config_version |
| `decisions` | id, ts, symbol, score, components (jsonb), outcome, rejection_reason |
| `orders` | id, idempotency_key, mode, symbol, side, qty, type, price, stop, state, broker_order_id, created_at, updated_at |
| `order_events` | order_id, ts, from_state, to_state, detail (jsonb) |
| `fills` | id, order_id, ts, qty, price, charges (jsonb) |
| `positions` | symbol, qty, avg_price, stop_price, opened_at, strategy |
| `risk_events` | id, ts, check_name, result, details (jsonb) |
| `config_versions` | version, ts, risk_yaml, strategy_yaml, changed_by, reason |
| `audit_log` | id, ts, actor, event, payload (jsonb), hash_prev, hash_self |
| `daily_pnl` | date, mode, realized, unrealized, fees, drawdown |

`audit_log` uses hash chaining so tampering is detectable. No update or delete permissions for the application role on `audit_log`, `order_events`, `fills`.

## 4. Order state machine

```
 CREATED --> RISK_APPROVED --> SUBMITTED --> ACKED --> PARTIALLY_FILLED --> FILLED
    |             |               |           |               |
    v             v               v           v               v
 REJECTED     REJECTED         FAILED     CANCEL_REQ --> CANCELLED
                                              |
                                              v
                                          EXPIRED
```

Rules:
- Transitions are only those drawn; anything else raises and halts trading.
- `SUBMITTED` without `ACKED` within timeout triggers a broker query, never a blind resend.
- Idempotency key = hash of (strategy, symbol, side, decision_id, bar_ts); duplicates are dropped.
- In `LIVE_APPROVAL`, `RISK_APPROVED` waits for owner approval (Telegram button or dashboard) with an expiry.

## 5. Paper broker simulation

To make paper results trustworthy it must model:

| Effect | Approach |
|--------|----------|
| Slippage | Function of spread, order size vs average volume, volatility; pessimistic default |
| Fill price | Market orders fill at next available price plus slippage, never at the signal bar's close |
| Latency | Configurable delay between decision and fill |
| Partial fills | For orders above a fraction of bar volume |
| Rejections | Random and rule-based (price band, insufficient funds, circuit limit) |
| Costs | Brokerage, STT, exchange charges, GST, stamp duty, DP charges; values in config (verify against the broker's charge calculator) |
| Market hours | No fills outside session; AMO behavior explicit |
| Limit orders | Fill only if price trades through the limit |

## 6. Decision Engine

```
score = w_trend * trend_score
      + w_mom   * momentum_score
      + w_vol   * volume_score
      + w_sent  * sentiment_score        # decayed by age
      + w_fund  * fundamental_score      # optional, slow-moving
entry if score >= entry_threshold AND all vetoes pass
exit  if score <= exit_threshold OR stop/target hit OR risk rule triggers
```

Weights and thresholds live in `strategy_config.yaml` and are versioned. Vetoes (in `strategy-spec.md`) override scores. The Decision Engine outputs an **order intent** only; sizing suggestions are passed to the Risk Engine, which has final say on quantity.

## 7. Position sizing

1. Risk per trade = `risk_per_trade_pct * equity`.
2. Quantity = floor(risk per trade / (entry - stop)).
3. Cap by max position %, cash available, liquidity cap (percentage of average daily volume), and lot size for F&O.
4. If quantity rounds to zero after caps, no trade (never round up).

## 8. Sentiment service

Flow: news item in, deduplicate, map to symbols, LLM call with a fixed prompt version, JSON schema validation, store.

Output schema:

```json
{
  "symbol": "string",
  "sentiment": -1.0,
  "confidence": 0.0,
  "event_type": "results|order_win|regulatory|management|rating|deal|other",
  "horizon": "intraday|days|weeks",
  "is_material": true,
  "summary": "max 200 chars"
}
```

Rules: invalid JSON or schema is discarded; low confidence is ignored; scores decay over time; the news text is treated as untrusted (prompt-injection defenses in `security.md`).

## 9. Configuration

- `risk_config.yaml`: limits (see `risk-management.md`).
- `strategy_config.yaml`: active strategies, weights, thresholds, universe.
- `settings.py`: environment, mode, provider selection, secrets by reference.
- Every change writes a `config_versions` row. Risk config changes are allowed only outside market hours and take effect at next startup.
- Each decision stores the `config_version` used.

## 10. Dashboard views

1. Overview: mode, P&L, risk status, feed health, kill switch.
2. Charts: candles with indicators and trade markers (Lightweight Charts).
3. Positions and orders with state history.
4. Signals and decisions with reasons, including rejected ones.
5. Risk events log.
6. News and sentiment feed.
7. Backtest results and comparison to paper.
8. Config and version history (read-only for risk config).

## 11. Backtester

- Event-driven; replays bars through the same Feature Engine, Strategies, Decision Engine, Risk Engine and a simulated broker.
- Applies the same cost and slippage model as `PaperBroker`.
- Supports walk-forward analysis and parameter sweeps with separate out-of-sample windows.
- Stores run metadata (code commit, config, data range) for reproducibility.

## 12. Observability

- Structured JSON logs with correlation IDs (decision_id, order_id).
- Metrics: feed latency, decision latency, order rejection rate, risk events, P&L, drawdown.
- Alerts: feed stale, reconciliation mismatch, risk breach, daily loss threshold, unhandled exception, watchdog heartbeat loss.
