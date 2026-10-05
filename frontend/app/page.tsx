"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo } from "react";
import { ActivityFeed, BotStatus } from "@/components/bot-status";
import { EquityChart } from "@/components/charts";
import { LivePrice } from "@/components/live-price";
import { MarketInsights } from "@/components/market";
import { TickerTape } from "@/components/tradingview";
import { Badge, Banner, Button, Card, Empty, Meter, Stat } from "@/components/ui";
import { API_URL, api } from "@/lib/api";
import { ago, humanize, money, pct, signedMoney, tone, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { ActivityItem, Market } from "@/lib/types";

type EquityPoint = { ts: string; equity: number; drawdown_pct: number };

export default function Overview() {
  const { status, portfolio } = useLive();
  const equity = useQuery({
    queryKey: ["equity", 180],
    queryFn: () => api<EquityPoint[]>("/api/equity?days=180"),
    refetchInterval: 60_000,
  });
  const market = useQuery({
    queryKey: ["market"],
    queryFn: () => api<Market>("/api/market"),
    refetchInterval: status?.market_open ? 5_000 : 60_000,
  });
  const activity = useQuery({
    queryKey: ["activity"],
    queryFn: () => api<ActivityItem[]>("/api/activity?limit=40"),
    refetchInterval: 5_000,
  });
  const points = useMemo(() => {
    // One point per day keeps the curve readable.
    const byDay = new Map<string, EquityPoint>();
    for (const p of equity.data ?? []) byDay.set(p.ts.slice(0, 10), p);
    return [...byDay.values()].map((p) => ({ t: Math.floor(new Date(p.ts).getTime() / 1000), v: p.equity }));
  }, [equity.data]);

  if (!status || !portfolio) {
    return <Empty>Connecting to the trading engine…</Empty>;
  }
  const breakers = Object.entries(status.breakers);
  const limits = portfolio.limits;
  const dayLossPct = status.day_pnl < 0 ? (-status.day_pnl / (status.equity - status.day_pnl)) * 100 : 0;
  const maxSector = Math.max(0, ...Object.values(portfolio.sectors).map((s) => s.pct));

  return (
    <div className="space-y-5">
      {/* 1. Anything that needs attention comes first. */}
      <div className="space-y-2">
        {status.demo && (
          <Banner tone="neutral" title="Synthetic demo data">
            Prices and trades on this screen come from a random walk. They carry no meaning about any real stock.
          </Banner>
        )}
        {status.kill_switch.active && (
          <Banner tone="neg" title={`Kill switch active (${status.kill_switch.action})`}>
            {status.kill_switch.reason} · since {when(status.kill_switch.ts)}
          </Banner>
        )}
        {breakers.map(([name, b]) => (
          <Banner
            key={name}
            tone={b.severity === "halt" ? "neg" : "warn"}
            title={`${humanize(name)} breaker: ${b.severity === "halt" ? "trading halted" : "new entries blocked"}`}
            action={
              <Link href="/risk">
                <Button>Review</Button>
              </Link>
            }
          >
            {b.reason}
          </Banner>
        ))}
        {status.provider.name === "upstox" && !status.upstox.logged_in && (
          <Banner
            tone="accent"
            title={status.upstox.analytics_rejected ? "Upstox rejected the analytics token" : "Connect Upstox for live prices"}
            action={
              status.upstox.configured ? (
                <a href={`${API_URL}/api/auth/upstox/login`}>
                  <Button variant="primary">Log in to Upstox</Button>
                </a>
              ) : (
                <Link href="/settings">
                  <Button>Setup guide</Button>
                </Link>
              )
            }
          >
            {status.upstox.analytics_rejected
              ? "It has expired or was revoked. Generate a new one (Upstox Developer Apps > Analytics), put it in UPSTOX_ANALYTICS_TOKEN and restart the API, or log in for today."
              : status.upstox.configured
              ? `Upstox tokens expire daily. Log in each morning before 09:15 IST. Until then, charts and backtests use delayed Yahoo history${status.runner.history_source ? "" : " (loading…)"} and no trades are placed.`
              : "Add UPSTOX_API_KEY and UPSTOX_API_SECRET to backend/.env, then restart the API."}
          </Banner>
        )}
        {status.regime.entries_blocked && (
          <Banner tone="warn" title="Market regime filter is blocking new entries">
            {status.regime.reason}
          </Banner>
        )}
      </div>

      {/* 2. Is the bot doing its job right now, and what has it done? */}
      <div className="grid gap-5 xl:grid-cols-3">
        <div className="xl:col-span-2">
          <BotStatus health={status.health} now={status.now} />
        </div>
        <ActivityFeed items={activity.data} loading={activity.isLoading} />
      </div>

      {/* 3. The money. */}
      <Card>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-6">
          <div className="col-span-2">
            <Stat big label="Equity" value={money(status.equity)} sub={`from ${money(status.starting_capital, 0)}`} />
          </div>
          <Stat label="Today" value={signedMoney(status.day_pnl)} tone={tone(status.day_pnl)} />
          <Stat
            label="Total P&L"
            value={signedMoney(status.total_pnl)}
            tone={tone(status.total_pnl)}
            sub={pct((status.total_pnl / status.starting_capital) * 100, 2, true)}
          />
          <Stat label="Cash" value={money(status.cash)} sub={`fees paid ${money(status.fees)}`} />
          <Stat
            label="Drawdown"
            value={pct(status.drawdown_pct)}
            tone={status.drawdown_pct > 5 ? "text-neg" : ""}
            sub="from peak equity"
          />
        </div>
      </Card>

      {/* 4. What the market is doing. */}
      <MarketInsights data={market.data} loading={market.isLoading} />

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Equity" className="xl:col-span-2" action={<span className="text-xs text-muted">paper account</span>}>
          {points.length > 1 ? <EquityChart points={points} /> : <Empty>The equity curve starts after the first trading day.</Empty>}
        </Card>

        {/* Risk usage: how close each hard limit is. */}
        <Card title="Risk limits in use">
          <ul className="space-y-4 text-[13px]">
            <li>
              <div className="mb-1.5 flex justify-between">
                <span className="text-ink-2">Open positions</span>
                <span className="num">{portfolio.positions.length} / {limits.max_open_positions}</span>
              </div>
              <Meter value={portfolio.positions.length} max={limits.max_open_positions} danger={1} />
            </li>
            <li>
              <div className="mb-1.5 flex justify-between">
                <span className="text-ink-2">Daily loss</span>
                <span className="num">{pct(dayLossPct)} / {pct(limits.max_daily_loss_pct)}</span>
              </div>
              <Meter value={dayLossPct} max={limits.max_daily_loss_pct} />
            </li>
            <li>
              <div className="mb-1.5 flex justify-between">
                <span className="text-ink-2">Drawdown</span>
                <span className="num">{pct(status.drawdown_pct)} / {pct(limits.max_drawdown_pct)}</span>
              </div>
              <Meter value={status.drawdown_pct} max={limits.max_drawdown_pct} />
            </li>
            <li>
              <div className="mb-1.5 flex justify-between">
                <span className="text-ink-2">Largest sector</span>
                <span className="num">{pct(maxSector)} / {pct(limits.max_sector_pct, 0)}</span>
              </div>
              <Meter value={maxSector} max={limits.max_sector_pct} />
            </li>
          </ul>
          <div className="mt-5 flex flex-wrap gap-1.5 border-t border-line pt-4">
            <Badge tone={status.groq.configured ? "pos" : "neutral"} dot>
              Groq {status.groq.configured ? "on" : "off"}
            </Badge>
            <Badge tone={status.telegram ? "pos" : "neutral"} dot>
              Telegram {status.telegram ? "on" : "off"}
            </Badge>
            <Badge tone={status.runner.running ? "pos" : "warn"} dot>
              Engine {status.runner.running ? "running" : "stopped"}
            </Badge>
          </div>
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Open positions" className="xl:col-span-2" pad={false} action={<Link className="text-xs text-accent" href="/positions">All orders →</Link>}>
          {portfolio.positions.length === 0 ? (
            <Empty>No open positions. The bot waits for a signal that passes every risk check.</Empty>
          ) : (
            <div className="overflow-x-auto">
              <table className="data">
                <thead>
                  <tr>
                    <th>Symbol</th>
                    <th className="r">Qty</th>
                    <th className="r">Avg</th>
                    <th className="r">LTP</th>
                    <th className="r">P&L</th>
                    <th className="r">Stop</th>
                    <th className="r">Target</th>
                  </tr>
                </thead>
                <tbody>
                  {portfolio.positions.map((p) => (
                    <tr key={p.symbol}>
                      <td>
                        <Link href={`/chart?symbol=${encodeURIComponent(p.symbol)}`} className="font-medium hover:text-accent">
                          {p.symbol}
                        </Link>
                        <div className="text-[11px] text-muted">{p.strategy} · {p.sector}</div>
                      </td>
                      <td className="r num">{p.qty}</td>
                      <td className="r num">{money(p.avg_price)}</td>
                      <td className="r"><LivePrice value={p.ltp} /></td>
                      <td className={`r num ${tone(p.pnl)}`}>
                        {signedMoney(p.pnl)}
                        <div className="text-[11px]">{pct(p.pnl_pct, 2, true)}</div>
                      </td>
                      <td className="r num">
                        {money(p.stop)}
                        {!p.stop_live && <div className="text-[11px] text-neg">not placed</div>}
                      </td>
                      <td className="r num">{money(p.target)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card title="Next actions" pad={false} action={<Link className="text-xs text-accent" href="/watchlist">Watchlist →</Link>}>
          {status.pending_intents.length === 0 ? (
            <Empty>No pending intents. Next decision after the {status.timeframe === "1d" ? "15:40 IST daily close" : "next 15-minute bar"}.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {status.pending_intents.map((i) => (
                <li key={i.decision_id} className="px-4 py-3 text-[13px]">
                  <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <span className="font-medium">
                      <Badge tone={i.side === "BUY" ? "pos" : "neg"}>{i.side}</Badge> {i.symbol}
                    </span>
                    <span className="text-[11px] text-muted">expires {when(i.expires_at)}</span>
                  </div>
                  <div className="mt-1 text-[12px] text-ink-2">{i.reason}</div>
                  <div className="mt-0.5 text-[11px] text-muted">Waits for the entry window, fresh prices and a final risk check.</div>
                </li>
              ))}
            </ul>
          )}
          <div className="border-t border-line px-4 py-3 text-[11px] text-muted">
            Last decision cycle {ago(status.last_cycle)}
          </div>
        </Card>
      </div>


      <div className="card overflow-hidden px-2">
        <TickerTape symbols={portfolio.positions.map((p) => p.symbol).concat(["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"])} />
      </div>
    </div>
  );
}
