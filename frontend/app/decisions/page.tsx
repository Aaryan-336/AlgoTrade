"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Badge, Card, Empty, PageHeader, ScoreBar } from "@/components/ui";
import { api } from "@/lib/api";
import { humanize, when } from "@/lib/format";
import type { Decision } from "@/lib/types";

const OUTCOME_TONE: Record<string, "pos" | "neg" | "warn" | "neutral" | "accent"> = {
  submitted: "pos",
  intent: "accent",
  risk_rejected: "neg",
  vetoed: "warn",
  below_threshold: "neutral",
  no_slot: "neutral",
  expired: "neutral",
  failed: "neg",
};

export default function Decisions() {
  const [outcome, setOutcome] = useState("all");
  const q = useQuery({ queryKey: ["decisions"], queryFn: () => api<Decision[]>("/api/decisions?limit=400"), refetchInterval: 15_000 });
  const outcomes = useMemo(() => ["all", ...new Set((q.data ?? []).map((d) => d.outcome))], [q.data]);
  const rows = (q.data ?? []).filter((d) => outcome === "all" || d.outcome === outcome);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Decisions"
        sub="Why each trade was taken or skipped: the score parts, the strategy's reasons and the final risk verdict."
        action={
          <select value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            {outcomes.map((o) => (
              <option key={o} value={o}>{o === "all" ? "All outcomes" : humanize(o)}</option>
            ))}
          </select>
        }
      />
      <Card pad={false}>
        {rows.length === 0 ? (
          <Empty>No decisions recorded yet. They appear after the first decision cycle.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Kind</th>
                  <th>Score</th>
                  <th>Trend · Mom · Vol · News</th>
                  <th>Outcome</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((d) => {
                  const c = d.components;
                  return (
                    <tr key={d.id + d.outcome}>
                      <td className="num whitespace-nowrap text-ink-2">{when(d.ts)}</td>
                      <td className="font-medium">{d.symbol}</td>
                      <td><Badge tone={d.kind === "exit" ? "neg" : "pos"}>{d.kind}</Badge></td>
                      <td><ScoreBar value={d.score} /></td>
                      <td className="num whitespace-nowrap text-[12px] text-ink-2">
                        {c.trend !== undefined
                          ? [c.trend, c.momentum, c.volume, c.sentiment].map((x) => (x ?? 0).toFixed(2)).join(" · ")
                          : "—"}
                      </td>
                      <td><Badge tone={OUTCOME_TONE[d.outcome] ?? "neutral"}>{humanize(d.outcome)}</Badge></td>
                      <td className="max-w-[460px] text-[12px] text-ink-2">
                        {d.reason && <div>{d.reason}</div>}
                        {c.signal && (
                          <div className="mt-0.5 text-muted">
                            {c.signal.strategy}: {c.signal.reasons.join("; ")}
                          </div>
                        )}
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
