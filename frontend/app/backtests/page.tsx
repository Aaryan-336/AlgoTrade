"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo, useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";
import { api, del } from "@/lib/api";
import { day, money, pct, signedMoney, tone, when } from "@/lib/format";
import type { BacktestSummary } from "@/lib/types";

type SortKey = "id" | "profit" | "return" | "drawdown" | "sharpe" | "trades";

const num = (v: unknown): number => (typeof v === "number" ? v : Number(v ?? 0));
const profit = (r: BacktestSummary) => num(r.metrics.final_equity) - num(r.metrics.start_equity);

export default function BacktestHistory() {
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["backtests", "all"], queryFn: () => api<BacktestSummary[]>("/api/backtests") });
  const [version, setVersion] = useState("all");
  const [sort, setSort] = useState<SortKey>("id");
  const [err, setErr] = useState<unknown>(null);
  const [picked, setPicked] = useState<number[]>([]);
  const togglePick = (id: number) =>
    setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : p.length < 4 ? [...p, id] : p));

  const versions = useMemo(
    () => ["all", ...new Set((runs.data ?? []).map((r) => r.params.version_name ?? "unnamed"))],
    [runs.data],
  );
  const rows = useMemo(() => {
    const list = (runs.data ?? []).filter((r) => version === "all" || (r.params.version_name ?? "unnamed") === version);
    const key: Record<SortKey, (r: BacktestSummary) => number> = {
      id: (r) => r.id,
      profit,
      return: (r) => num(r.metrics.total_return_pct),
      drawdown: (r) => -num(r.metrics.max_drawdown_pct), // smaller drawdown first
      sharpe: (r) => num(r.metrics.sharpe),
      trades: (r) => num(r.metrics.trades),
    };
    return [...list].sort((a, b) => key[sort](b) - key[sort](a));
  }, [runs.data, version, sort]);
  const best = rows.length ? Math.max(...rows.map(profit)) : null;

  async function remove(id: number) {
    if (!window.confirm(`Delete backtest #${id}?`)) return;
    setErr(null);
    try {
      await del(`/api/backtests/${id}`);
      await qc.invalidateQueries({ queryKey: ["backtests"] });
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Backtest history"
        sub="Every backtest you have run, with the strategy version and settings window it used."
        action={
          <div className="flex gap-2">
            <Link href={picked.length >= 2 ? `/compare?runs=${picked.join(",")}` : "/compare"}>
              <Button disabled={picked.length === 1}>
                {picked.length >= 2 ? `Compare ${picked.length} selected` : "Compare strategies"}
              </Button>
            </Link>
            <Link href="/backtest">
              <Button variant="primary">New backtest</Button>
            </Link>
          </div>
        }
      />
      <ErrorNote error={err} />
      <Card
        pad={false}
        title={`${rows.length} run${rows.length === 1 ? "" : "s"}`}
        action={
          <div className="flex flex-wrap gap-2">
            <select value={version} onChange={(e) => setVersion(e.target.value)}>
              {versions.map((v) => (
                <option key={v} value={v}>{v === "all" ? "All versions" : v}</option>
              ))}
            </select>
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="id">Newest first</option>
              <option value="profit">Highest profit</option>
              <option value="return">Highest return</option>
              <option value="drawdown">Smallest drawdown</option>
              <option value="sharpe">Highest Sharpe</option>
              <option value="trades">Most trades</option>
            </select>
          </div>
        }
      >
        {runs.isLoading ? (
          <Empty>Loading…</Empty>
        ) : rows.length === 0 ? (
          <Empty>No backtests yet. Run one from the Backtest page.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th className="w-8" title="Select up to 4 to compare" />
                  <th>Run</th>
                  <th>Run at</th>
                  <th>Version</th>
                  <th>Window</th>
                  <th className="r">Stocks</th>
                  <th className="r">Capital</th>
                  <th className="r">Profit / loss</th>
                  <th className="r">Return</th>
                  <th className="r">CAGR</th>
                  <th className="r">Max DD</th>
                  <th className="r">Sharpe</th>
                  <th className="r">Trades</th>
                  <th className="r">Win %</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const m = r.metrics as Record<string, number | null>;
                  const p = profit(r);
                  return (
                    <tr key={r.id}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Select run ${r.id} to compare`}
                          checked={picked.includes(r.id)}
                          disabled={!picked.includes(r.id) && picked.length >= 4}
                          onChange={() => togglePick(r.id)}
                        />
                      </td>
                      <td className="num">#{r.id}</td>
                      <td className="num whitespace-nowrap text-ink-2">{when(r.ts)}</td>
                      <td className="whitespace-nowrap">
                        {r.params.version_name ?? "unnamed"}
                        {best !== null && best > 0 && p === best && rows.length > 1 && (
                          <span className="ml-2"><Badge tone="pos">best</Badge></span>
                        )}
                      </td>
                      <td className="whitespace-nowrap text-ink-2">{day(r.params.start)} → {day(r.params.end)}</td>
                      <td className="r num">{r.params.symbols?.length ?? "—"}</td>
                      <td className="r num">{money(r.params.capital, 0)}</td>
                      <td className={`r num font-medium ${tone(p)}`}>{signedMoney(p)}</td>
                      <td className={`r num ${tone(m.total_return_pct)}`}>{pct(m.total_return_pct, 2, true)}</td>
                      <td className="r num">{pct(m.cagr_pct)}</td>
                      <td className="r num">{pct(m.max_drawdown_pct)}</td>
                      <td className="r num">{(m.sharpe ?? 0).toFixed(2)}</td>
                      <td className="r num">{m.trades ?? 0}</td>
                      <td className="r num">{pct(m.win_rate_pct, 0)}</td>
                      <td className="r whitespace-nowrap">
                        <Link href={`/backtest?run=${r.id}`}>
                          <Button variant="ghost">Open</Button>
                        </Link>
                        <Button variant="ghost" onClick={() => remove(r.id)}>Delete</Button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
