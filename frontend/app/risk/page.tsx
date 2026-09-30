"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorNote, PageHeader } from "@/components/ui";
import { api, post } from "@/lib/api";
import { humanize, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { RiskEvent } from "@/lib/types";

const LAYERS = [
  ["Strategy filters", "Liquidity, price, history and regime filters remove weak candidates."],
  ["Decision vetoes", "Negative material news, event days and index regime veto entries."],
  ["Pre-trade Risk Engine", "22 hard checks on every order; any failure rejects it."],
  ["Order manager", "Idempotent orders, state machine, no blind retries."],
  ["Broker-side stops", "Every entry gets a stop order that lives at the broker."],
  ["Circuit breakers", "Daily, weekly, drawdown, stale data, broker errors, mismatches."],
  ["Watchdog + kill switch", "Separate process; can block trading if the engine goes silent."],
  ["Reconciliation", "Positions and cash compared with the broker every minute."],
];

function ResetBreaker({ name }: { name: string }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<unknown>(null);
  return (
    <form
      className="mt-3 flex flex-col gap-2 sm:flex-row"
      onSubmit={async (e) => {
        e.preventDefault();
        setErr(null);
        try {
          await post(`/api/breakers/${name}/reset`, { note });
          setNote("");
          qc.invalidateQueries();
        } catch (x) {
          setErr(x);
        }
      }}
    >
      <input className="flex-1" placeholder="What happened, why, and what changes (required)" value={note} onChange={(e) => setNote(e.target.value)} />
      <Button type="submit" disabled={note.trim().length < 3}>Reset after review</Button>
      <ErrorNote error={err} />
    </form>
  );
}

export default function Risk() {
  const { status } = useLive();
  const events = useQuery({ queryKey: ["risk-events"], queryFn: () => api<RiskEvent[]>("/api/risk-events?limit=300"), refetchInterval: 10_000 });
  const audit = useQuery({ queryKey: ["audit-verify"], queryFn: () => api<{ ok: boolean; first_bad_row: number | null }>("/api/audit/verify"), refetchInterval: 60_000 });
  const breakers = Object.entries(status?.breakers ?? {});

  return (
    <div className="space-y-5">
      <PageHeader title="Risk" sub="Limits are enforced in code and cannot be changed during market hours." />

      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="Circuit breakers" className="self-start xl:col-span-2">
          {breakers.length === 0 ? (
            <div className="flex items-center gap-2 text-[13px] text-ink-2">
              <Badge tone="pos" dot>Clear</Badge> No breaker has tripped.
            </div>
          ) : (
            <ul className="space-y-4">
              {breakers.map(([name, b]) => (
                <li key={name} className="rounded-lg border border-line p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-[13px] font-semibold">{humanize(name)}</span>
                    <Badge tone={b.severity === "halt" ? "neg" : "warn"}>{b.severity === "halt" ? "halts trading" : "blocks entries"}</Badge>
                    <span className="ml-auto text-[11px] text-muted">{when(b.tripped_at)}</span>
                  </div>
                  <p className="mt-1 text-[13px] text-ink-2">{b.reason}</p>
                  {name !== "stale_data" && <ResetBreaker name={name} />}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4 text-[12px]">
            <Badge tone={status?.kill_switch.active ? "neg" : "pos"} dot>
              Kill switch {status?.kill_switch.active ? `on (${status.kill_switch.action})` : "off"}
            </Badge>
            <Badge tone={audit.data?.ok ? "pos" : audit.data ? "neg" : "neutral"} dot>
              Audit chain {audit.data ? (audit.data.ok ? "intact" : `broken at row ${audit.data.first_bad_row}`) : "…"}
            </Badge>
            <Badge tone={status?.heartbeat?.running ? "pos" : "neutral"} dot>
              Heartbeat {status?.heartbeat ? when(status.heartbeat.ts) : "none"}
            </Badge>
          </div>
        </Card>

        <Card title="Defense in depth">
          <ol className="space-y-2.5">
            {LAYERS.map(([t, d], i) => (
              <li key={t} className="flex gap-3 text-[13px]">
                <span className="num w-4 shrink-0 text-muted">{i + 1}</span>
                <span>
                  <span className="font-medium">{t}</span>
                  <span className="block text-[12px] text-muted">{d}</span>
                </span>
              </li>
            ))}
          </ol>
        </Card>
      </div>

      <Card title="Risk events" pad={false}>
        {!events.data?.length ? (
          <Empty>No risk events recorded.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Result</th>
                  <th>Check</th>
                  <th>Symbol</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {events.data.map((e) => (
                  <tr key={e.id}>
                    <td className="num whitespace-nowrap text-ink-2">{when(e.ts)}</td>
                    <td><Badge tone={e.result === "BREAKER" ? "neg" : e.result === "REJECT" ? "warn" : "neutral"}>{e.result}</Badge></td>
                    <td className="font-medium">{humanize(e.check)}</td>
                    <td>{e.symbol ?? "—"}</td>
                    <td className="max-w-[520px] text-[12px] text-ink-2">
                      {Object.entries(e.details).map(([k, v]) => `${k}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
