"use client";

import { useQuery } from "@tanstack/react-query";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { CandleChart, type Candle, type FillMark } from "@/components/charts";
import { AdvancedChart } from "@/components/tradingview";
import { Badge, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { money, pct, tone } from "@/lib/format";
import type { UniverseRow } from "@/lib/types";

type BarsResponse = {
  symbol: string;
  bars: Candle[];
  fills: FillMark[];
  position: { avg_price: number; stop: number | null; target: number | null; qty: number } | null;
};

function ChartView() {
  const params = useSearchParams();
  const router = useRouter();
  const universe = useQuery({ queryKey: ["universe"], queryFn: () => api<UniverseRow[]>("/api/universe"), refetchInterval: 15_000 });
  const symbol = params.get("symbol") ?? universe.data?.find((u) => u.held)?.symbol ?? universe.data?.[0]?.symbol ?? "";
  const [view, setView] = useState<"bot" | "tradingview">("bot");
  const bars = useQuery({
    queryKey: ["bars", symbol],
    queryFn: () => api<BarsResponse>(`/api/bars/${encodeURIComponent(symbol)}?timeframe=1d&limit=260`),
    enabled: !!symbol,
    refetchInterval: 30_000,
  });
  const row = universe.data?.find((u) => u.symbol === symbol);
  const sorted = useMemo(
    () => [...(universe.data ?? [])].sort((a, b) => Number(b.held) - Number(a.held) || a.symbol.localeCompare(b.symbol)),
    [universe.data],
  );

  return (
    <div>
      <PageHeader
        title="Chart"
        sub="Candles from the bot's own data feed, with its fills, stop and target."
        action={
          <div className="flex gap-2">
            <select value={symbol} onChange={(e) => router.replace(`/chart?symbol=${encodeURIComponent(e.target.value)}`)}>
              {sorted.map((u) => (
                <option key={u.symbol} value={u.symbol}>
                  {u.held ? "● " : ""}
                  {u.symbol} · {u.name}
                </option>
              ))}
            </select>
            <div className="flex rounded-lg border border-line-strong p-0.5">
              {(["bot", "tradingview"] as const).map((v) => (
                <button
                  key={v}
                  onClick={() => setView(v)}
                  className={`rounded-md px-2.5 py-1 text-[12px] ${view === v ? "bg-surface-2 font-semibold" : "text-muted"}`}
                >
                  {v === "bot" ? "Bot data" : "TradingView"}
                </button>
              ))}
            </div>
          </div>
        }
      />
      <div className="grid gap-5 xl:grid-cols-4">
        <Card className="xl:col-span-3" pad={false}>
          <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-line px-4 py-3">
            <span className="text-[15px] font-semibold">{symbol}</span>
            <span className="num text-[15px]">{money(row?.ltp)}</span>
            <span className={`num text-[13px] ${tone(row?.change_pct)}`}>{pct(row?.change_pct, 2, true)}</span>
            {row?.held && <Badge tone="accent">Held</Badge>}
            {view === "tradingview" && <span className="ml-auto text-[11px] text-muted">TradingView widget · display only, not used for decisions</span>}
          </div>
          <div className="p-2">
            {view === "tradingview" ? (
              <AdvancedChart symbol={symbol} />
            ) : bars.error ? (
              <div className="p-4"><ErrorNote error={bars.error} /></div>
            ) : bars.data && bars.data.bars.length > 0 ? (
              <CandleChart
                bars={bars.data.bars}
                fills={bars.data.fills}
                entry={bars.data.position?.avg_price}
                stop={bars.data.position?.stop}
                target={bars.data.position?.target}
              />
            ) : (
              <Empty>No bars yet for {symbol}. History loads after you connect the data feed.</Empty>
            )}
          </div>
        </Card>
        <Card title="Symbol" className="self-start">
          <dl className="space-y-3 text-[13px]">
            <div className="flex justify-between"><dt className="text-muted">Name</dt><dd className="text-right">{row?.name}</dd></div>
            <div className="flex justify-between"><dt className="text-muted">Sector</dt><dd>{row?.sector}</dd></div>
            <div className="flex justify-between">
              <dt className="text-muted">News sentiment</dt>
              <dd className={`num ${tone(row?.sentiment)}`}>{row?.sentiment == null ? "neutral" : row.sentiment.toFixed(2)}</dd>
            </div>
            <div className="flex justify-between"><dt className="text-muted">Headlines (30d)</dt><dd className="num">{row?.news_count ?? 0}</dd></div>
            {row?.negative_material && <Badge tone="neg">Negative material news: entries vetoed</Badge>}
            {bars.data?.position && (
              <>
                <div className="border-t border-line pt-3 flex justify-between"><dt className="text-muted">Qty</dt><dd className="num">{bars.data.position.qty}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Entry</dt><dd className="num">{money(bars.data.position.avg_price)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Stop</dt><dd className="num text-neg">{money(bars.data.position.stop)}</dd></div>
                <div className="flex justify-between"><dt className="text-muted">Target</dt><dd className="num text-pos">{money(bars.data.position.target)}</dd></div>
              </>
            )}
          </dl>
        </Card>
      </div>
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <ChartView />
    </Suspense>
  );
}
