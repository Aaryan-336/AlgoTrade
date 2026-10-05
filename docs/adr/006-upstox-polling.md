# ADR-006: Upstox as the paper-phase feed, polled

**Status:** accepted (2026-09-30)

**Context.** The owner wants real-time data at no cost while validating the idea, and plans Kite for execution. TradingView has no data API; Kite Connect's data plan is paid; Kite Personal (free) has no data.

**Decision.** Use Upstox's official REST API for market data. Poll LTP for all symbols in one request every `POLL_INTERVAL_SEC` (default 5 s) and fetch completed candles at each decision bar close. Do not use the protobuf WebSocket feed yet.

**Why polling.** Decisions run on daily or 15-minute bars. Polling is simpler, easier to test, and restarts cleanly. The cost is a few seconds of latency on stop triggers in simulation, which the pessimistic slippage model already covers.

**Consequences.** A daily OAuth login is needed. Moving to Kite data or the Upstox WebSocket later is a new `MarketDataProvider` only. Data from one broker and execution at another can differ slightly; acceptable for bar-based strategies.
