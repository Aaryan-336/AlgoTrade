# AlgoTrade

A personal algorithmic trading bot for NSE equities, running in **paper mode** so you can check whether the idea works before connecting real money through Kite.

It watches a watchlist (default: Nifty 50), scores each stock on trend, momentum, volume and 30 days of news sentiment (Groq), and decides to buy, hold, sell, or rotate out of a weak holding into a clearly better one. Every order passes a Risk Engine that the strategy cannot bypass.

> No trading system is flawless and profit is not guaranteed. The goal of the paper phase is an honest answer, not a nice-looking backtest. See `docs/testing-and-go-live.md` for the gates that must pass before any live trading.

## What's inside

| Part | Tech | Where |
|------|------|-------|
| Trading engine, risk engine, order manager, paper broker, backtester | Python 3.12, FastAPI, SQLAlchemy, Alembic | `backend/` |
| Market data | Upstox API (real-time, free with an account) | `backend/algotrade/data/providers/upstox.py` |
| News sentiment | Google News / Moneycontrol / ET RSS + Groq LLM | `backend/algotrade/news/` |
| Dashboard | Next.js, Tailwind, TradingView Lightweight Charts + TradingView widgets (display only) | `frontend/` |
| Watchdog + CLI kill switch | Separate process | `backend/algotrade/watchdog/` |
| Database | SQLite (laptop) or PostgreSQL (compose) | `infra/docker-compose.yml` |

Design docs are in `docs/` (start with `docs/README.md`). The full list of risk controls and their status is in `docs/risk-features.md`.

## Quick start (laptop)

**1. Backend**

```bash
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,research]"
cp .env.example .env          # add your Upstox and Groq keys
algotrade-api                 # http://localhost:8000 (runs migrations on start)
```

**2. Dashboard** (in a second terminal)

```bash
cd frontend
npm install
npm run dev                   # http://localhost:3000
```

**3. Watchdog** (third terminal, optional on a laptop)

```bash
cd backend && source .venv/bin/activate
algotrade-watchdog            # or: algotrade-watchdog kill --reason "..." [--flatten]
```

**4. Each trading day:** open the dashboard and click **Log in to Upstox** before 09:15 IST. Upstox tokens expire overnight.

### Try it without any accounts

This seeds a separate database with *synthetic* prices (a random walk), so you can explore the dashboard offline. A banner marks it as demo data.

```bash
cd backend
DATABASE_URL=sqlite:///./demo.db python -m algotrade.demo --days 120 --capital 100000
DATABASE_URL=sqlite:///./demo.db DATA_PROVIDER=replay algotrade-api
```

## Getting the keys

- **Upstox:** open a free Upstox account, then create an app at https://account.upstox.com/developer/apps. Set the redirect URL to `http://localhost:8000/api/auth/upstox/callback` (it must match `UPSTOX_REDIRECT_URI` exactly).
- **Groq:** create a key at https://console.groq.com/keys. Without it, headlines are still collected but sentiment stays neutral.
- **Telegram (optional):** create a bot with @BotFather, then set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## How a trading day runs (default: daily bars)

1. After the 15:30 close (at 15:40 IST), the engine fetches today's candles, runs the strategies and queues **intents** for tomorrow.
2. At 09:20 the next morning, each intent is re-checked by the Risk Engine against live prices. If the stock gapped more than 2%, or any limit fails, it is rejected and the reason is logged.
3. Market orders fill in the paper broker at the live price plus spread, slippage and full Zerodha delivery charges.
4. A stop order is placed at the (simulated) broker right after every fill. It keeps protecting the position even if the engine halts.
5. Throughout the day: prices are polled every 5 seconds; stops trigger; breakers, reconciliation and the watchdog run.
6. News is fetched and scored every 30 minutes.

You can switch to 15-minute bars in Settings (outside market hours).

## Tests and checks

```bash
cd backend
pytest                        # 125 tests: unit, property (Hypothesis), integration, chaos
pytest --cov=algotrade.risk --cov=algotrade.oms   # 100% on Risk Engine and OMS
ruff check algotrade && mypy
cd ../frontend && npm run typecheck && npm run build
```

## Running on a VPS

```bash
POSTGRES_PASSWORD=<long-random> docker compose -f infra/docker-compose.yml up -d --build
```

Ports bind to `127.0.0.1` only. Reach the dashboard over SSH tunnel or a VPN such as Tailscale, and set `API_TOKEN`. See `docs/security.md`.

## Before switching to Kite

Paper trade for at least 8 to 12 weeks and 100 trades, then check every gate in `docs/testing-and-go-live.md` §6. Kite Connect *Personal* (free) handles orders; keep Upstox for data. Live trading needs a static IP, a VPS and the SEBI retail-algo requirements; verify current rules first.
