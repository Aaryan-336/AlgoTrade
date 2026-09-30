# CLAUDE.md: Instructions for building the Algo Trading Bot

You are helping build a personal algorithmic trading bot for Indian markets. It handles real money once live, so correctness and safety outrank speed and cleverness.

Read in this order before coding: `README.md`, `rules.md`, `architecture.md`, `design.md`, `risk-management.md`, `security.md`. Follow `roadmap.md` phases; do not build ahead of the current phase.

## Non-negotiables

1. Never give strategies or the Decision Engine access to a broker. All orders flow: intent -> Risk Engine -> Order Manager -> Broker adapter.
2. Never bypass, weaken or "temporarily disable" a risk check. If a check blocks progress, raise it with the owner.
3. Default mode is `PAPER`. Never write code that can enable live mode without the explicit startup confirmation in rule I-04.
4. Fail closed: on uncertainty, block new orders and alert.
5. Never put secrets in code, tests, logs, prompts or commits. Use environment variables and fixtures with dummy values.
6. The LLM has no authority. Its output must be schema-validated structured data used only as a score input or veto.
7. Do not use TradingView as a data source, and do not use unofficial or terms-violating scrapers.
8. No naked short options or undefined-risk positions.
9. Do not place orders against a live account from scripts, notebooks or tests.

## Coding standards

- Python 3.12, full type hints, `mypy --strict` on `core/`; `ruff` for lint and format.
- Strategies are pure functions or classes: no I/O, no network, no wall clock (time comes from context).
- Money and prices use `Decimal`; quantities are integers; timestamps are timezone-aware (Asia/Kolkata for display, UTC for storage).
- Every state-changing action writes to the audit log with a correlation ID and config version.
- Keep modules small; prefer explicit over clever. Avoid premature abstraction beyond the interfaces in `design.md`.
- Database access through a single repository layer; migrations versioned (Alembic).
- No new dependency without a stated reason; pin versions.

## Testing requirements

- Write tests with the code. Risk Engine and Order Manager need unit and property-based tests (Hypothesis) with coverage above 95%.
- Add a replay regression test for any behavior change in signals or decisions.
- Every bug fix includes a regression test.
- Chaos scenarios in `testing-and-go-live.md` must have automated tests before Phase 4.
- Run the full suite before declaring work done; report failures honestly.

## Definition of done (per task)

- [ ] Behavior matches the relevant doc; docs updated if behavior changed.
- [ ] Tests written and passing; coverage targets met for risk/OMS code.
- [ ] Lint, type check and security scans pass.
- [ ] Audit logging present for new state changes.
- [ ] No secrets or personal data in the diff.
- [ ] Failure modes considered: what happens on stale data, broker error, duplicate input, restart?

## How to work with the owner

- State assumptions and open questions early; ask before making decisions that increase risk, scope or autonomy.
- Mark uncertain external facts (fees, SEBI rules, broker limits) as **verify** rather than guessing.
- Prefer small, reviewable changes. One phase exit criterion at a time.
- If asked to do something that conflicts with `rules.md`, explain the conflict and propose a safe alternative instead of complying silently.
- Do not claim the bot is "flawless" or guarantee profits. Describe results with their uncertainty.

## Phase pointer

Current phase: **P1-P3 built for paper; P4 paper validation running.** The owner asked for sentiment and scanning together with the paper core, so P1-P3 were built as one paper-only milestone (2026-09-30). Code lives in `backend/` (Python) and `frontend/` (Next.js). Nothing that can place a live order exists yet; the Kite adapter comes only after the go-live gates. `docs/risk-features.md` tracks which risk controls are built.
