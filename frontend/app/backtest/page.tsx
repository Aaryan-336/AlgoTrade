"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { EquityChart } from "@/components/charts";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader, Stat } from "@/components/ui";
import { api, post } from "@/lib/api";
import { loadDraft, type Draft } from "@/lib/draft";
import { day, humanize, money, pct, signedMoney, tone } from "@/lib/format";
import type { BacktestResult, UniverseRow } from "@/lib/types";

const today = new Date();
const iso = (d: Date) => d.toISOString().slice(0, 10);
const yearsAgo = (n: number) => iso(new Date(today.getFullYear() - n, today.getMonth(), today.getDate()));

function Metrics({ r }: { r: BacktestResult }) {
  const m = r.metrics as Record<string, number | null>;
  return (
    <div className="grid grid-cols-2 gap-5 md:grid-cols-4 xl:grid-cols-6">
      <Stat label="Return" value={pct(m.total_return_pct, 2, true)} tone={tone(m.total_return_pct)} sub={`CAGR ${pct(m.cagr_pct)}`} />
      <Stat label="Max drawdown" value={pct(m.max_drawdown_pct)} tone="text-neg" />
      <Stat label="Sharpe" value={(m.sharpe ?? 0).toFixed(2)} sub={`Sortino ${(m.sortino ?? 0).toFixed(2)}`} />
      <Stat label="Trades" value={m.trades ?? 0} sub={`${pct(m.win_rate_pct, 0)} winners`} />
      <Stat label="Expectancy" value={signedMoney(m.expectancy)} tone={tone(m.expectancy)} sub="per trade, after costs" />
      <Stat label="Charges" value={money(m.fees_total)} sub={m.fees_share_of_gross_pct != null ? `${pct(m.fees_share_of_gross_pct, 0)} of gross profit` : "gross profit ≤ 0"} />
    </div>
  );
}

export default function Backtest() {
  const qc = useQueryClient();
  const universe = useQuery({ queryKey: ["universe"], queryFn: () => api<UniverseRow[]>("/api/universe") });
  const history = useQuery({ queryKey: ["backtests"], queryFn: () => api<BacktestResult[]>("/api/backtests") });
  const [start, setStart] = useState(yearsAgo(2));
  const [end, setEnd] = useState(iso(today));
  const [capital, setCapital] = useState("");
  const [symbols, setSymbols] = useState<string[]>([]);
  const [selected, setSelected] = useState<BacktestResult | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [useDraft, setUseDraft] = useState(false);
  useEffect(() => {
    const d = loadDraft();
    setDraft(d);
    setUseDraft(!!d);
  }, []);

  const run = useMutation({
    mutationFn: () =>
      post<BacktestResult>("/api/backtest", {
        start,
        end,
        symbols,
        capital: capital ? Number(capital) : undefined,
        ...(useDraft && draft ? { risk: draft.risk, strategy: draft.strategy } : {}),
      }),
    onSuccess: (r) => {
      setSelected(r);
      qc.invalidateQueries({ queryKey: ["backtests"] });
    },
  });
  const shown = selected ?? history.data?.[0] ?? null;
  const points = useMemo(
    () => (shown?.equity_curve ?? []).map((p) => ({ t: Math.floor(new Date(p.date).getTime() / 1000), v: p.equity })),
    [shown],
  );

  return (
    <div className="space-y-5">
      <PageHeader
        title="Backtest"
        sub="Replays daily history through the same strategy, risk and paper-broker code, with costs and slippage. Sentiment is neutral here because past news can't be reliably replayed."
      />
      <Card
        title="Run"
        action={
          draft ? (
            <div className="flex rounded-lg border border-line-strong p-0.5 text-[12px]">
              {([false, true] as const).map((v) => (
                <button
                  key={String(v)}
                  onClick={() => setUseDraft(v)}
                  className={`rounded-md px-2.5 py-1 ${useDraft === v ? "bg-surface-2 font-semibold" : "text-muted"}`}
                >
                  {v ? "Edited settings (unsaved)" : "Live config"}
                </button>
              ))}
            </div>
          ) : (
            <Link href="/settings" className="text-xs text-accent">Edit strategy settings →</Link>
          )
        }
      >
        <div className="grid gap-4 md:grid-cols-4">
          <label className="block">
            <span className="label">From</span>
            <input type="date" className="mt-1 w-full" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="block">
            <span className="label">To</span>
            <input type="date" className="mt-1 w-full" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label className="block">
            <span className="label">Capital (₹, optional)</span>
            <input inputMode="numeric" className="mt-1 w-full" placeholder="config value" value={capital} onChange={(e) => setCapital(e.target.value.replace(/[^0-9]/g, ""))} />
          </label>
          <div className="flex items-end">
            <Button variant="primary" className="w-full" disabled={run.isPending} onClick={() => run.mutate()}>
              {run.isPending ? "Running… (can take a minute)" : "Run backtest"}
            </Button>
          </div>
        </div>
        <div className="mt-4">
          <span className="label">Symbols ({symbols.length ? symbols.length : "all configured"})</span>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {(universe.data ?? []).map((u) => {
              const on = symbols.includes(u.symbol);
              return (
                <button
                  key={u.symbol}
                  onClick={() => setSymbols(on ? symbols.filter((s) => s !== u.symbol) : [...symbols, u.symbol])}
                  className={`rounded-md border px-2 py-0.5 text-[12px] ${on ? "border-ink bg-ink text-bg" : "border-line text-ink-2 hover:bg-surface-2"}`}
                >
                  {u.symbol}
                </button>
              );
            })}
          </div>
        </div>
        <div className="mt-3"><ErrorNote error={run.error} /></div>
      </Card>

      {shown ? (
        <>
          <Card
            title={`${day(shown.params.start)} → ${day(shown.params.end)} · ${shown.params.symbols.length} symbols · ${money(shown.params.capital, 0)} · ${shown.params.config_version === "draft" ? "edited settings" : `config v${shown.params.config_version ?? "?"}`}`}
            action={shown.params.failed?.length ? <Badge tone="warn">{shown.params.failed.length} symbols had no data</Badge> : undefined}
          >
            <Metrics r={shown} />
            <div className="mt-5">{points.length > 1 && <EquityChart points={points} height={260} />}</div>
            {shown.rejections && Object.keys(shown.rejections).length > 0 && (
              <div className="mt-4 flex flex-wrap gap-1.5 text-[12px]">
                <span className="text-muted">Risk rejections:</span>
                {Object.entries(shown.rejections).map(([k, v]) => (
                  <Badge key={k}>{humanize(k)} × {v}</Badge>
                ))}
              </div>
            )}
          </Card>
          <Card title="Trades" pad={false}>
            {shown.trades.length === 0 ? (
              <Empty>No trades in this window.</Empty>
            ) : (
              <div className="max-h-[480px] overflow-auto">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Symbol</th><th>Strategy</th><th>Opened</th><th>Closed</th>
                      <th className="r">Qty</th><th className="r">Entry</th><th className="r">Exit</th><th className="r">Net P&L</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.trades.map((t, i) => (
                      <tr key={i}>
                        <td className="font-medium">{t.symbol}</td>
                        <td className="text-ink-2">{t.strategy}</td>
                        <td className="num text-ink-2">{day(t.opened)}</td>
                        <td className="num text-ink-2">{day(t.closed)}</td>
                        <td className="r num">{t.qty}</td>
                        <td className="r num">{money(t.entry)}</td>
                        <td className="r num">{money(t.exit)}</td>
                        <td className={`r num ${tone(t.pnl)}`}>{signedMoney(t.pnl)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      ) : (
        <Card><Empty>No backtests yet. Connect Upstox, then run one above.</Empty></Card>
      )}

      {(history.data?.length ?? 0) > 1 && (
        <Card title="Previous runs" pad={false}>
          <table className="data">
            <thead><tr><th>Run</th><th>Window</th><th className="r">Return</th><th className="r">Max DD</th><th className="r">Trades</th><th /></tr></thead>
            <tbody>
              {history.data!.map((h) => (
                <tr key={h.id}>
                  <td className="num">#{h.id}</td>
                  <td className="text-ink-2">{day(h.params.start)} → {day(h.params.end)}</td>
                  <td className={`r num ${tone(h.metrics.total_return_pct as number)}`}>{pct(h.metrics.total_return_pct as number, 2, true)}</td>
                  <td className="r num">{pct(h.metrics.max_drawdown_pct as number)}</td>
                  <td className="r num">{String(h.metrics.trades)}</td>
                  <td className="r"><Button variant="ghost" onClick={() => setSelected(h)}>View</Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
