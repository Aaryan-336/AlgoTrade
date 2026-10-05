"use client";

import { useQueries, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useMemo, useState } from "react";
import { CompareChart, seriesColor, type CompareSeries } from "@/components/charts";
import { Badge, Banner, Button, Card, Empty, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { day, humanize, money, pct, signedMoney, when } from "@/lib/format";
import type { BacktestResult, BacktestSummary } from "@/lib/types";

const MAX_RUNS = 4;
const n = (v: unknown): number | null => (v === null || v === undefined || v === "" ? null : Number(v));

type Row = {
  label: string;
  get: (r: BacktestResult) => number | null;
  fmt: (v: number | null) => string;
  better: "high" | "low" | null; // which direction wins; null = informational
  hint?: string;
};

const ROWS: Row[] = [
  { label: "Profit / loss", get: (r) => (n(r.metrics.final_equity) ?? 0) - (n(r.metrics.start_equity) ?? 0), fmt: signedMoney, better: "high" },
  { label: "Return", get: (r) => n(r.metrics.total_return_pct), fmt: (v) => pct(v, 2, true), better: "high" },
  { label: "CAGR (per year)", get: (r) => n(r.metrics.cagr_pct), fmt: (v) => pct(v), better: "high" },
  { label: "Max drawdown", get: (r) => n(r.metrics.max_drawdown_pct), fmt: (v) => pct(v), better: "low", hint: "worst fall from a peak" },
  { label: "Sharpe", get: (r) => n(r.metrics.sharpe), fmt: (v) => (v ?? 0).toFixed(2), better: "high", hint: "return per unit of risk" },
  { label: "Sortino", get: (r) => n(r.metrics.sortino), fmt: (v) => (v ?? 0).toFixed(2), better: "high" },
  { label: "Volatility", get: (r) => n(r.metrics.volatility_pct), fmt: (v) => pct(v), better: "low" },
  { label: "Trades", get: (r) => n(r.metrics.trades), fmt: (v) => String(v ?? 0), better: null },
  { label: "Win rate", get: (r) => n(r.metrics.win_rate_pct), fmt: (v) => pct(v, 0), better: "high" },
  { label: "Avg win", get: (r) => n(r.metrics.avg_win), fmt: money, better: "high" },
  { label: "Avg loss", get: (r) => n(r.metrics.avg_loss), fmt: money, better: "high" },
  { label: "Expectancy / trade", get: (r) => n(r.metrics.expectancy), fmt: signedMoney, better: "high", hint: "after costs" },
  { label: "Charges paid", get: (r) => n(r.metrics.fees_total), fmt: money, better: "low" },
  { label: "Time invested", get: (r) => n(r.metrics.exposure_pct), fmt: (v) => pct(v, 0), better: null },
];

/** Flatten nested settings to "a.b.c" -> value, for a differences table. */
function flatten(obj: unknown, prefix = "", out: Record<string, string> = {}): Record<string, string> {
  if (obj && typeof obj === "object" && !Array.isArray(obj)) {
    for (const [k, v] of Object.entries(obj as Record<string, unknown>)) flatten(v, prefix ? `${prefix}.${k}` : k, out);
  } else {
    out[prefix] = Array.isArray(obj) ? (obj.length ? obj.join(", ") : "—") : String(obj);
  }
  return out;
}

export default function ComparePage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <Compare />
    </Suspense>
  );
}

function Compare() {
  const params = useSearchParams();
  const router = useRouter();
  const ids = useMemo(
    () => (params.get("runs") ?? "").split(",").map(Number).filter((x) => x > 0).slice(0, MAX_RUNS),
    [params],
  );
  const all = useQuery({ queryKey: ["backtests", "all"], queryFn: () => api<BacktestSummary[]>("/api/backtests") });
  const details = useQueries({
    queries: ids.map((id) => ({ queryKey: ["backtest", id], queryFn: () => api<BacktestResult>(`/api/backtests/${id}`) })),
  });
  const runs = details.map((d) => d.data).filter((r): r is BacktestResult => !!r);
  const ready = runs.length === ids.length && ids.length > 0;
  const [hover, setHover] = useState<{ values: (number | null)[]; t: number } | null>(null);

  const setIds = (next: number[]) => router.replace(next.length ? `/compare?runs=${next.join(",")}` : "/compare");
  const toggle = (id: number) =>
    setIds(ids.includes(id) ? ids.filter((x) => x !== id) : ids.length < MAX_RUNS ? [...ids, id] : ids);

  const label = (r: BacktestResult) => `#${r.id} ${r.params.version_name ?? ""}`.trim();
  const series: CompareSeries[] = useMemo(
    () =>
      ready
        ? runs.map((r) => {
            const base = r.equity_curve[0]?.equity || 1;
            return {
              label: label(r),
              points: r.equity_curve.map((p) => ({ t: Math.floor(new Date(p.date).getTime() / 1000), v: (p.equity / base - 1) * 100 })),
            };
          })
        : [],
    // eslint-disable-next-line react-hooks/exhaustive-deps -- runs is derived from details
    [ready, details.map((d) => d.dataUpdatedAt).join()],
  );
  const onHover = useCallback((values: (number | null)[] | null, t: number | null) => {
    setHover(values && t ? { values, t } : null);
  }, []);

  const windows = new Set(runs.map((r) => `${r.params.start}|${r.params.end}`));
  const capitals = new Set(runs.map((r) => r.params.capital));
  const diffs = useMemo(() => {
    const flat = runs.map((r) => (r.params as { settings?: unknown }).settings ? flatten((r.params as { settings?: unknown }).settings) : null);
    if (flat.some((f) => !f) || flat.length < 2) return null;
    const keys = [...new Set(flat.flatMap((f) => Object.keys(f!)))].sort();
    return keys.filter((k) => new Set(flat.map((f) => f![k])).size > 1).map((k) => ({ key: k, values: flat.map((f) => f![k] ?? "—") }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, ids.join()]);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Compare strategies"
        sub={`Pick up to ${MAX_RUNS} backtests to see them side by side. Equity is shown as % return from each run's own start, so different capitals line up.`}
        action={
          <Link href="/backtests">
            <Button>Backtest history</Button>
          </Link>
        }
      />

      <Card title={`Runs to compare (${ids.length}/${MAX_RUNS})`} pad={false}>
        {!all.data?.length ? (
          <Empty>No backtests yet. Run a few from the Backtest page first.</Empty>
        ) : (
          <div className="max-h-[260px] overflow-auto">
            <table className="data">
              <tbody>
                {all.data.map((r) => {
                  const idx = ids.indexOf(r.id);
                  const on = idx >= 0;
                  const full = !on && ids.length >= MAX_RUNS;
                  return (
                    <tr key={r.id} className={`cursor-pointer hover:bg-surface-2 ${full ? "opacity-50" : ""}`} onClick={() => !full && toggle(r.id)}>
                      <td className="w-8">
                        <input type="checkbox" checked={on} disabled={full} readOnly aria-label={`Compare run ${r.id}`} />
                      </td>
                      <td className="w-6">{on && <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: seriesColor(idx) }} />}</td>
                      <td className="num">#{r.id}</td>
                      <td className="font-medium">{r.params.version_name ?? "unnamed"}</td>
                      <td className="text-ink-2">{day(r.params.start)} → {day(r.params.end)}</td>
                      <td className="r num">{pct(n(r.metrics.total_return_pct), 2, true)}</td>
                      <td className="r num text-muted">{when(r.ts)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {ids.length < 2 ? (
        <Card><Empty>Tick at least two runs above to compare them.</Empty></Card>
      ) : !ready ? (
        <Card><Empty>Loading runs…</Empty></Card>
      ) : (
        <>
          {(windows.size > 1 || capitals.size > 1) && (
            <Banner tone="warn" title="These runs aren't like-for-like">
              {windows.size > 1 && "They cover different date ranges. "}
              {capitals.size > 1 && "They start with different capital, so compare % figures, not rupees. "}
              For a fair comparison, run every version over the same window and capital.
            </Banner>
          )}

          <Card title="Equity, % return from start" pad={false}>
            {/* Legend doubles as the hover readout; identity never relies on colour alone. */}
            <div className="flex flex-wrap gap-x-5 gap-y-1.5 border-b border-line px-4 py-3 text-[12px]">
              {runs.map((r, i) => {
                const v = hover?.values[i];
                return (
                  <span key={r.id} className="flex items-center gap-2">
                    <span className="inline-block h-0.5 w-4 rounded" style={{ background: seriesColor(i) }} />
                    <span className="font-medium text-ink">{label(r)}</span>
                    <span className="num text-ink-2">
                      {hover ? (v === null || v === undefined ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`) : pct(n(r.metrics.total_return_pct), 1, true)}
                    </span>
                  </span>
                );
              })}
              {hover && <span className="num ml-auto text-muted">{day(new Date(hover.t * 1000).toISOString())}</span>}
            </div>
            <div className="p-2">
              <CompareChart series={series} onHover={onHover} />
            </div>
          </Card>

          <Card title="Results" pad={false}>
            <div className="overflow-x-auto">
              <table className="data">
                <thead>
                  <tr>
                    <th>Metric</th>
                    {runs.map((r, i) => (
                      <th key={r.id} className="r">
                        <span className="inline-flex items-center gap-1.5">
                          <span className="inline-block h-2 w-2 rounded-full" style={{ background: seriesColor(i) }} />
                          {label(r)}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="text-ink-2">Window</td>
                    {runs.map((r) => (
                      <td key={r.id} className="r text-ink-2">{day(r.params.start)} → {day(r.params.end)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td className="text-ink-2">Capital</td>
                    {runs.map((r) => (
                      <td key={r.id} className="r num">{money(r.params.capital, 0)}</td>
                    ))}
                  </tr>
                  <tr>
                    <td>
                      Stopped by a breaker
                      <div className="text-[11px] text-muted">trading halted for the rest of the run</div>
                    </td>
                    {runs.map((r) => {
                      const b = (r.metrics.breakers_tripped as string[] | undefined) ?? [];
                      return (
                        <td key={r.id} className="r">
                          {b.length ? <Badge tone="warn">{b.map(humanize).join(", ")}</Badge> : <span className="text-muted">no</span>}
                        </td>
                      );
                    })}
                  </tr>
                  {ROWS.map((row) => {
                    const vals = runs.map((r) => row.get(r));
                    const valid = vals.filter((v): v is number => v !== null);
                    const winner =
                      row.better && valid.length > 1 && new Set(valid).size > 1
                        ? row.better === "high"
                          ? Math.max(...valid)
                          : Math.min(...valid)
                        : null;
                    return (
                      <tr key={row.label}>
                        <td>
                          {row.label}
                          {row.hint && <div className="text-[11px] text-muted">{row.hint}</div>}
                        </td>
                        {vals.map((v, i) => (
                          <td key={runs[i].id} className={`r num ${v === winner ? "font-semibold text-ink" : "text-ink-2"}`}>
                            {row.fmt(v)}
                            {v === winner && <span className="ml-1.5"><Badge tone="accent">best</Badge></span>}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="border-t border-line px-4 py-3 text-[12px] text-muted">
              “Best” marks the winner on each row only. A higher return with a much deeper drawdown is often the worse strategy:
              look at return, drawdown and Sharpe together.
            </p>
          </Card>

          <Card title="What's different between them" pad={false}>
            {diffs === null ? (
              <Empty>Settings were not recorded for one of these runs (runs made before this feature). Re-run them to see a settings comparison.</Empty>
            ) : diffs.length === 0 ? (
              <Empty>Identical settings. Any difference in results comes from the date range, capital or stock list.</Empty>
            ) : (
              <div className="overflow-x-auto">
                <table className="data">
                  <thead>
                    <tr>
                      <th>Setting</th>
                      {runs.map((r, i) => (
                        <th key={r.id} className="r">
                          <span className="inline-flex items-center gap-1.5">
                            <span className="inline-block h-2 w-2 rounded-full" style={{ background: seriesColor(i) }} />
                            {label(r)}
                          </span>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {diffs.map((d) => (
                      <tr key={d.key}>
                        <td className="text-ink-2">{d.key.split(".").map(humanize).join(" › ")}</td>
                        {d.values.map((v, i) => (
                          <td key={i} className="r num max-w-[260px] truncate" title={v}>{v}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}
    </div>
  );
}
