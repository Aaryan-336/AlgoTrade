# Personal Algo Trading Bot: Documentation Set

A personal, single-user algorithmic trading bot for Indian markets (NSE/BSE).
It runs in **paper mode first**; real order execution is enabled only after the go-live gates in `testing-and-go-live.md` are met.

> **Reality check (read once, then never forget):**
> No trading system is "flawless". Markets are uncertain, data can be wrong, and brokers can fail.
> The goal of this design is a bot that is **safe, auditable, and fails closed**: when anything is unclear, it does nothing.
> Profit is not guaranteed. Most retail algo and F&O traders lose money. Only risk money you can afford to lose.

## Reading order

| # | Doc | Purpose |
|---|-----|---------|
| 1 | `prd.md` | What we are building and why; requirements and success metrics |
| 2 | `scope.md` | What is in and out, per phase; constraints and assumptions |
| 3 | `architecture.md` | System components, data flow, deployment |
| 4 | `design.md` | Detailed design: modules, interfaces, data model, state machines |
| 5 | `rules.md` | Non-negotiable invariants and trading/change-control rules |
| 6 | `risk-management.md` | Risk layers, limits, pre-trade checks, circuit breakers, kill switch |
| 7 | `security.md` | Threat model, secrets, access control, compliance |
| 8 | `data-sources.md` | Market data options, TradingView reality, provider abstraction |
| 9 | `strategy-spec.md` | Signals, scoring, sentiment, portfolio-aware logic, F&O |
| 10 | `testing-and-go-live.md` | Test strategy, backtesting, paper validation, go-live gates |
| 11 | `roadmap.md` | Phased plan with exit criteria |
| 12 | `runbook.md` | Daily operations and incident procedures |
| 13 | `CLAUDE.md` | Instructions for Claude Code when building this repo |
| 14 | `risk-features.md` | Every risk control, where it lives, and whether it is built |

## Operating modes (used across all docs)

| Mode | Orders go to | Real money? | Human approval? |
|------|--------------|-------------|-----------------|
| `PAPER` | Simulated broker | No | No |
| `LIVE_APPROVAL` | Real broker | Yes (tiny caps) | Yes, every order |
| `LIVE_LIMITED` | Real broker | Yes (capped) | No, but hard caps |
| `LIVE` | Real broker | Yes | No |

The same strategy, risk and order code runs in every mode. **Only the broker adapter differs.**

## Document status

Version 0.1, drafted 2026-09-30. Values marked **(verify)** depend on broker, exchange or SEBI rules that change; confirm them against official sources before relying on them.
