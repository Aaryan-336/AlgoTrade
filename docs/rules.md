# Rules

These rules are the project's constitution. If a feature, shortcut or deadline conflicts with them, the rules win. Changing a rule requires a written reason in the ADR log and updated tests.

## A. System invariants (enforced in code and tests)

| ID | Invariant |
|----|-----------|
| I-01 | **No order reaches a broker without passing the Risk Engine.** Strategies and the Decision Engine have no broker access. |
| I-02 | **Fail closed.** On stale data, unknown state, reconciliation mismatch, DB failure or unhandled error, new orders are blocked. |
| I-03 | **Mode is explicit.** The active mode (`PAPER`, `LIVE_APPROVAL`, `LIVE_LIMITED`, `LIVE`) is shown everywhere and logged on every order. Default is `PAPER`. |
| I-04 | **Live mode needs two deliberate acts:** a config change and a fresh manual confirmation at startup. It never resumes live after a crash without confirmation. |
| I-05 | **Idempotent orders.** The same decision cannot create two orders. |
| I-06 | **Every live position has a stop** defined at entry, and placed exchange-side where the broker supports it. |
| I-07 | **No naked short options and no undefined-risk positions.** |
| I-08 | **Broker state wins.** On any mismatch, the broker's view is the truth and trading halts until resolved. |
| I-09 | **LLM output never triggers an order.** It is validated structured data that feeds scores and vetoes only. |
| I-10 | **Kill switch works independently** of the main application and is tested weekly. |
| I-11 | **Everything is auditable.** Every signal, decision, order, fill, risk event and config change is logged with the config version. |
| I-12 | **Risk limits cannot be changed during market hours** and cannot be changed by strategy code. |
| I-13 | **Secrets are never in code, logs, prompts, or the repo.** |
| I-14 | **Time and price sanity:** orders outside the price band or far from last traded price are rejected. |
| I-15 | **No silent retries of order placement.** Uncertain outcomes are resolved by querying the broker. |

## B. Trading conduct rules

1. Trade only instruments in the approved universe and within liquidity filters.
2. Risk a fixed fraction of equity per trade; never size up to "win back" losses.
3. No averaging down into losing positions unless a tested strategy explicitly defines it (default: not allowed).
4. No trading in the first minutes after the open or the final minutes before close unless a strategy is explicitly tested for it.
5. No new entries on major event days configured in the event calendar.
6. Daily, weekly and drawdown limits are hard stops, not suggestions.
7. After any circuit-breaker halt, the bot stays off until the owner reviews and restarts manually.
8. No discretionary overrides of live orders through the code path; manual trades are made in the broker app and then reconciled.
9. Paper and live performance are compared regularly; unexplained divergence pauses promotion.
10. Start every new live capability at the smallest meaningful size.

## C. Development and change-control rules

1. **Tests first for risk and order code.** Coverage above 95% with property-based tests for the Risk Engine and Order Manager.
2. **No merge without passing CI** (lint, type check, unit, property, replay, security scan).
3. **Pure strategies.** No I/O, no network, no wall-clock access inside strategy code.
4. **Config is versioned.** Code and config versions are stored with every decision.
5. **No new instrument class, larger size, or more autonomy** without updating `scope.md`, `risk-management.md` and the go-live gates.
6. **Dependencies are pinned** and reviewed; new dependencies need a stated reason.
7. **Feature flags default off** for anything touching execution.
8. **Every bug in production becomes a regression test** before the fix is merged.
9. **Two-person rule substitute:** for changes to risk or execution code, do a self-review checklist and wait one full trading day in paper before enabling live.
10. **Documentation stays current:** a change that affects behavior updates the relevant doc in the same change.

## D. Forbidden actions

- Using unofficial or terms-violating data sources in any trading path.
- Automating broker login by storing the account password or TOTP secret on the server (see `security.md`).
- Disabling or bypassing a risk check "temporarily".
- Placing orders from notebooks or ad-hoc scripts against a live account.
- Running live mode on a laptop.
- Training or tuning on data that includes the test window.
- Enabling F&O live trading before P5 results and go-live gates are met.
- Acting on instructions found inside news text, web pages, or documents fetched by the bot.

## E. Escalation ladder

| Level | Trigger | Automatic action | Human action |
|-------|---------|------------------|--------------|
| 1 | Warning (e.g. slow feed) | Alert | Review at next break |
| 2 | Risk event (e.g. daily loss 50% of limit) | Reduce size or block entries | Review same day |
| 3 | Breaker (limit breached, mismatch, stale data) | Halt new orders | Investigate, restart manually |
| 4 | Critical (unhandled error, watchdog loss) | Halt, alert, rely on exchange-side stops | Immediate attention; consider manual flatten in broker app |
