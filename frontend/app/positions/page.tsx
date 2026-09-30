"use client";

import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Badge, Card, Empty, PageHeader, Stat } from "@/components/ui";
import { api } from "@/lib/api";
import { day, money, pct, signedMoney, tone, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { OrderRow, Trade } from "@/lib/types";

type Event = { ts: string; from: string; to: string; detail: Record<string, unknown> };

const STATE_TONE: Record<string, "pos" | "neg" | "warn" | "neutral" | "accent"> = {
  FILLED: "pos",
  ACKED: "accent",
  PARTIALLY_FILLED: "accent",
  REJECTED: "neg",
  FAILED: "neg",
  CANCELLED: "neutral",
  EXPIRED: "neutral",
  SUBMITTED: "warn",
  CANCEL_REQ: "warn",
};

function OrderEvents({ id }: { id: string }) {
  const q = useQuery({ queryKey: ["events", id], queryFn: () => api<Event[]>(`/api/orders/${id}/events`) });
  if (!q.data) return <div className="text-xs text-muted">Loading…</div>;
  return (
    <ol className="space-y-1 text-[12px]">
      {q.data.map((e, i) => (
        <li key={i} className="flex gap-3">
          <span className="num w-28 shrink-0 text-muted">{when(e.ts)}</span>
          <span className="font-medium">{e.to}</span>
          <span className="truncate text-ink-2">{Object.entries(e.detail).map(([k, v]) => `${k}: ${String(v)}`).join(" · ")}</span>
        </li>
      ))}
    </ol>
  );
}

export default function Positions() {
  const { portfolio } = useLive();
  const [filter, setFilter] = useState<"all" | "open">("all");
  const [openRow, setOpenRow] = useState<string | null>(null);
  const orders = useQuery({
    queryKey: ["orders", filter],
    queryFn: () => api<OrderRow[]>(`/api/orders?limit=200${filter === "open" ? "&open_only=true" : ""}`),
    refetchInterval: 5000,
  });
  const trades = useQuery({ queryKey: ["trades"], queryFn: () => api<Trade[]>("/api/trades"), refetchInterval: 15_000 });
  const closed = trades.data ?? [];
  const wins = closed.filter((t) => t.pnl > 0);
  const net = closed.reduce((s, t) => s + t.pnl, 0);

  return (
    <div className="space-y-5">
      <PageHeader title="Positions & orders" sub="Every order carries its full state history. Rejected orders show the exact risk check that failed." />

      <Card>
        <div className="grid grid-cols-2 gap-5 md:grid-cols-5">
          <Stat label="Invested" value={money(portfolio?.invested)} />
          <Stat label="Unrealized" value={signedMoney(portfolio?.unrealized)} tone={tone(portfolio?.unrealized)} />
          <Stat label="Closed trades" value={closed.length} sub={closed.length ? `${pct((wins.length / closed.length) * 100, 0)} winners` : undefined} />
          <Stat label="Net closed P&L" value={signedMoney(net)} tone={tone(net)} sub="after all charges" />
          <Stat label="Charges paid" value={money(portfolio?.fees)} />
        </div>
      </Card>

      <Card title="Orders" pad={false} action={
        <div className="flex rounded-lg border border-line-strong p-0.5">
          {(["all", "open"] as const).map((f) => (
            <button key={f} onClick={() => setFilter(f)} className={`rounded-md px-2.5 py-1 text-[12px] ${filter === f ? "bg-surface-2 font-semibold" : "text-muted"}`}>
              {f === "all" ? "All" : "Working"}
            </button>
          ))}
        </div>
      }>
        {!orders.data?.length ? (
          <Empty>No orders yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Purpose</th>
                  <th className="r">Qty</th>
                  <th className="r">Price</th>
                  <th>State</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {orders.data.map((o) => (
                  <Fragment key={o.id}>
                    <tr className="cursor-pointer hover:bg-surface-2" onClick={() => setOpenRow(openRow === o.id ? null : o.id)}>
                      <td className="num whitespace-nowrap text-ink-2">{when(o.created_at)}</td>
                      <td className="font-medium">{o.symbol}</td>
                      <td><Badge tone={o.side === "BUY" ? "pos" : "neg"}>{o.side}</Badge></td>
                      <td className="text-ink-2">{o.purpose.replace("_", " ")}</td>
                      <td className="r num">{o.filled_qty ? `${o.filled_qty}/${o.qty}` : o.qty}</td>
                      <td className="r num">{money(o.avg_fill_price ?? o.trigger_price)}</td>
                      <td><Badge tone={STATE_TONE[o.state] ?? "neutral"}>{o.state.replace("_", " ")}</Badge></td>
                      <td className="max-w-[380px] truncate text-ink-2" title={o.reason}>{o.reason}</td>
                    </tr>
                    {openRow === o.id && (
                      <tr>
                        <td colSpan={8} className="bg-surface-2">
                          <OrderEvents id={o.id} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Closed trades" pad={false}>
        {closed.length === 0 ? (
          <Empty>No closed trades yet.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="data">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Strategy</th>
                  <th>Opened</th>
                  <th>Closed</th>
                  <th className="r">Qty</th>
                  <th className="r">Entry</th>
                  <th className="r">Exit</th>
                  <th className="r">Charges</th>
                  <th className="r">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {closed.map((t, i) => (
                  <tr key={i}>
                    <td className="font-medium">{t.symbol}</td>
                    <td className="text-ink-2">{t.strategy}</td>
                    <td className="num text-ink-2">{day(t.opened_at)}</td>
                    <td className="num text-ink-2">{day(t.closed_at)}</td>
                    <td className="r num">{t.qty}</td>
                    <td className="r num">{money(t.entry)}</td>
                    <td className="r num">{money(t.exit)}</td>
                    <td className="r num text-muted">{money(t.fees)}</td>
                    <td className={`r num font-medium ${tone(t.pnl)}`}>{signedMoney(t.pnl)}</td>
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
