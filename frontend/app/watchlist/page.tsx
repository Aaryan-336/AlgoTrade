"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { LiveChange, LivePrice } from "@/components/live-price";
import { Badge, Banner, Button, Card, Empty, ErrorNote, PageHeader, ScoreBar } from "@/components/ui";
import { api, post } from "@/lib/api";
import { ago, day, money, signedMoney, tone } from "@/lib/format";
import { useLive, useLiveInterval } from "@/lib/live";
import type { Watchlist, WatchRow } from "@/lib/types";

const STATUS: Record<WatchRow["status"], { label: string; tone: "pos" | "neg" | "warn" | "neutral" | "accent" }> = {
  holding: { label: "Holding", tone: "accent" },
  buy_candidate: { label: "Buy candidate", tone: "pos" },
  vetoed: { label: "Vetoed", tone: "warn" },
  below_threshold: { label: "Below threshold", tone: "neutral" },
  no_signal: { label: "No signal", tone: "neutral" },
  not_enough_history: { label: "Not enough data", tone: "neutral" },
};

const FILTERS: { key: "all" | WatchRow["status"]; label: string }[] = [
  { key: "all", label: "All" },
  { key: "buy_candidate", label: "Candidates" },
  { key: "holding", label: "Holding" },
  { key: "vetoed", label: "Vetoed" },
  { key: "below_threshold", label: "Below threshold" },
  { key: "no_signal", label: "No signal" },
];

export default function WatchlistPage() {
  const qc = useQueryClient();
  const { portfolio, status, prices } = useLive();
  const wl = useQuery({ queryKey: ["watchlist"], queryFn: () => api<Watchlist>("/api/watchlist"), refetchInterval: useLiveInterval(15_000) });
  const liveOn = !!status?.market_open && !!wl.data?.live_ranked_at;
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const scan = useMutation({
    mutationFn: () => post<{ intents: number; ranked: number }>("/api/engine/scan"),
    onSuccess: () => qc.invalidateQueries(),
  });

  const rows = wl.data?.rows ?? [];
  const counts = useMemo(() => {
    const c: Record<string, number> = { all: rows.length };
    for (const r of rows) c[r.status] = (c[r.status] ?? 0) + 1;
    return c;
  }, [rows]);
  const shown = rows.filter((r) => filter === "all" || r.status === filter);

  // The bot's curated portfolio: what it holds, what it will buy next session,
  // then the best remaining candidates up to the open slots.
  const held = portfolio?.positions ?? [];
  const pending = wl.data?.pending ?? {};
  const buying = rows.filter((r) => pending[r.symbol] === "BUY");
  const free = Math.max(0, (wl.data?.max_positions ?? 0) - held.length - buying.length);
  const nextUp = rows.filter((r) => r.status === "buy_candidate" && !pending[r.symbol] && !r.held).slice(0, free);
  const byPrice = Object.fromEntries(rows.map((r) => [r.symbol, r]));

  return (
    <div className="space-y-5">
      <PageHeader
        title="Watchlist"
        sub={
          wl.data?.bar_ts
            ? `Every stock in your universe, ranked by the bot's score on the ${day(wl.data.bar_ts)} close.${liveOn ? ` Prices stream live; the live score was refreshed ${ago(wl.data?.live_ranked_at)}.` : ` Updated ${ago(wl.data.ranked_at)}.`}`
            : "Every stock in your universe, ranked by the bot's score."
        }
        action={
          <Button variant="primary" disabled={scan.isPending} onClick={() => scan.mutate()}>
            {scan.isPending ? "Scanning…" : "Scan now"}
          </Button>
        }
      />
      <ErrorNote error={scan.error ?? wl.error} />
      {scan.data && (
        <div className="text-[13px] text-ink-2">
          Scanned {scan.data.ranked} stocks · {scan.data.intents} new trade plan{scan.data.intents === 1 ? "" : "s"} queued for the next session.
        </div>
      )}
      {wl.data?.regime?.entries_blocked && (
        <Banner tone="warn" title="Market filter: no new buys right now">
          {wl.data.regime.reason}. Scores still update so you can see who would qualify.
        </Banner>
      )}
      {status && !status.market_open && (
        <Banner tone="neutral" title="Market closed">
          The bot plans on the latest close; buys and sells happen at 09:20 IST on the next trading day, after a final risk check
          against live prices.
        </Banner>
      )}

      <Card title="Bot's portfolio" pad={false} action={<span className="text-xs text-muted">{held.length} held · {buying.length} buying next session · {wl.data?.max_positions ?? "—"} slots</span>}>
        {held.length + buying.length + nextUp.length === 0 ? (
          <Empty>
            {rows.length === 0
              ? "No data yet. Log in to Upstox (or wait a minute for delayed history), then press Scan now."
              : "Nothing qualifies right now. Every stock is either below the score threshold, vetoed, or the market filter is blocking buys."}
          </Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Stock</th>
                  <th>Status</th>
                  <th className="r">Price</th>
                  <th>Score</th>
                  <th className="r">Stop</th>
                  <th className="r">Target</th>
                  <th className="r">P&L</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {held.map((p) => (
                  <tr key={`h-${p.symbol}`}>
                    <td><Link href={`/chart?symbol=${encodeURIComponent(p.symbol)}`} className="font-medium hover:text-accent">{p.symbol}</Link><div className="text-[11px] text-muted">{p.sector}</div></td>
                    <td><Badge tone="accent">Holding</Badge>{pending[p.symbol] === "SELL" && <span className="ml-1"><Badge tone="neg">Selling next session</Badge></span>}</td>
                    <td className="r"><LivePrice value={prices[p.symbol]?.ltp ?? p.ltp} /></td>
                    <td>{byPrice[p.symbol]?.score != null ? <ScoreBar value={byPrice[p.symbol].score!} /> : "—"}</td>
                    <td className="r num">{money(p.stop)}</td>
                    <td className="r num">{money(p.target)}</td>
                    <td className={`r num ${tone(p.pnl)}`}>{signedMoney(p.pnl)}</td>
                    <td className="max-w-[340px] text-[12px] text-ink-2">{byPrice[p.symbol]?.reason ?? p.strategy}</td>
                  </tr>
                ))}
                {[...buying, ...nextUp].map((r) => (
                  <tr key={`b-${r.symbol}`}>
                    <td><Link href={`/chart?symbol=${encodeURIComponent(r.symbol)}`} className="font-medium hover:text-accent">{r.symbol}</Link><div className="text-[11px] text-muted">{r.sector}</div></td>
                    <td>{pending[r.symbol] === "BUY" ? <Badge tone="pos">Buying next session</Badge> : <Badge tone="neutral">Next in line</Badge>}</td>
                    <td className="r"><LivePrice value={prices[r.symbol]?.ltp ?? r.ltp} /></td>
                    <td>{r.score != null ? <ScoreBar value={r.score} /> : "—"}</td>
                    <td className="r num">{money(r.stop)}</td>
                    <td className="r num">{money(r.target)}</td>
                    <td className="r num text-muted">—</td>
                    <td className="max-w-[340px] text-[12px] text-ink-2">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line px-4 py-3 text-[12px] text-muted">
          “Buying next session” means a plan is queued. It is only placed if it still passes every risk check at 09:20 IST (price
          gap, position and sector caps, cash, costs).
        </p>
      </Card>

      <Card
        title="All stocks, ranked"
        pad={false}
        action={
          <div className="flex flex-wrap gap-1">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                onClick={() => setFilter(f.key)}
                className={`rounded-md px-2 py-1 text-[12px] ${filter === f.key ? "bg-surface-2 font-semibold text-ink" : "text-muted hover:text-ink"}`}
              >
                {f.label} <span className="num text-muted">{counts[f.key] ?? 0}</span>
              </button>
            ))}
          </div>
        }
      >
        {wl.isLoading ? (
          <Empty>Loading…</Empty>
        ) : shown.length === 0 ? (
          <Empty>No stocks in this group.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th className="r">#</th>
                  <th>Stock</th>
                  <th className="r">Price</th>
                  <th className="r">Day</th>
                  <th title="Score on the last completed close: this is what the bot trades on">Score</th>
                  {liveOn && <th title="Re-scored every minute on today's forming candle: what the bot would decide if the day closed now. It does not trade on this.">Live score</th>}
                  <th className="r" title="Trend · Momentum · Volume · News (each 0-1)">Trend · Mom · Vol · News</th>
                  <th>Status</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.symbol}>
                    <td className="r num text-muted">{rows.indexOf(r) + 1}</td>
                    <td>
                      <Link href={`/chart?symbol=${encodeURIComponent(r.symbol)}`} className="font-medium hover:text-accent">{r.symbol}</Link>
                      <div className="text-[11px] text-muted">{r.name} · {r.sector}</div>
                    </td>
                    <td className="r"><LivePrice value={prices[r.symbol]?.ltp ?? r.ltp} /></td>
                    <td className="r"><LiveChange value={prices[r.symbol]?.chg ?? r.change_pct} /></td>
                    <td>{r.score != null ? <ScoreBar value={r.score} /> : <span className="text-muted">—</span>}</td>
                    {liveOn && (
                      <td title={r.live_reason ?? undefined}>
                        {r.live_score != null ? (
                          <span className="flex items-center gap-1.5">
                            <ScoreBar value={r.live_score} />
                            {r.live_status === "buy_candidate" && r.status !== "buy_candidate" && <Badge tone="pos">new</Badge>}
                          </span>
                        ) : (
                          <span className="text-muted">—</span>
                        )}
                      </td>
                    )}
                    <td className="r num text-[12px] text-ink-2">
                      {r.trend !== undefined ? [r.trend, r.momentum, r.volume, r.sentiment].map((x) => (x ?? 0).toFixed(2)).join(" · ") : "—"}
                    </td>
                    <td><Badge tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Badge></td>
                    <td className="max-w-[380px] text-[12px] text-ink-2">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="border-t border-line px-4 py-3 text-[12px] text-muted">
          A stock becomes a buy candidate when a strategy fires <em>and</em> its score is at least {wl.data?.entry_threshold ?? "—"}
          {" "}<em>and</em> no veto applies (market filter, bad news, event day, illiquid).
          {liveOn && (
            <>
              {" "}The live score re-runs the same rules every minute on today&apos;s forming candle. It is a preview: buys are
              still decided on the completed close at 15:40, the way the strategy was backtested.
            </>
          )}
        </p>
      </Card>
    </div>
  );
}
