"use client";

import { Card, Empty } from "@/components/ui";
import { ago, when } from "@/lib/format";
import type { ActivityItem, Health, HealthState } from "@/lib/types";

/** Each state has its own glyph and word, so it never relies on colour alone. */
const STATE: Record<HealthState, { glyph: string; word: string; cls: string }> = {
  ok: { glyph: "✓", word: "OK", cls: "bg-pos-soft text-pos" },
  idle: { glyph: "◷", word: "Waiting", cls: "bg-surface-2 text-ink-2" },
  warn: { glyph: "!", word: "Check", cls: "bg-warn-soft text-warn" },
  error: { glyph: "✕", word: "Problem", cls: "bg-neg-soft text-neg" },
};

const VERDICT: Record<Health["verdict"], { word: string; dot: string; ring: boolean }> = {
  active: { word: "Running", dot: "bg-pos", ring: true },
  waiting: { word: "Running · waiting", dot: "bg-pos", ring: false },
  degraded: { word: "Running with a problem", dot: "bg-warn", ring: false },
  stopped: { word: "Not trading", dot: "bg-neg", ring: false },
};

export function BotStatus({ health, now }: { health?: Health; now?: string }) {
  if (!health) {
    return (
      <Card title="Is the bot working?">
        <Empty>Waiting for the engine…</Empty>
      </Card>
    );
  }
  const v = VERDICT[health.verdict];
  const ref = now ? new Date(now).getTime() : Date.now();
  return (
    <Card
      title="Is the bot working?"
      pad={false}
      action={<span className="text-[11px] text-muted">updates every 2s</span>}
    >
      <div className="flex flex-wrap items-start gap-3 border-b border-line px-4 py-3.5">
        <span className="relative mt-1.5 flex h-2.5 w-2.5 shrink-0">
          {v.ring && <span className={`absolute inline-flex h-full w-full rounded-full ${v.dot} opacity-60 motion-safe:animate-ping`} />}
          <span className={`relative inline-flex h-2.5 w-2.5 rounded-full ${v.dot}`} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[15px] font-semibold">{v.word}</div>
          <div className="text-[13px] text-ink-2">{health.headline}</div>
        </div>
        {health.next && (
          <div className="w-full pl-[22px] text-[12px] sm:w-auto sm:pl-0 sm:text-right">
            <div className="label">Next step</div>
            <div className="font-medium">
              {health.next.label} · <span className="num">{health.next.when}</span>
            </div>
          </div>
        )}
      </div>
      <ol className="divide-y divide-line">
        {health.steps.map((s, i) => {
          const st = STATE[s.state];
          return (
            <li key={s.key} className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-x-3 px-4 py-2.5 sm:grid-cols-[1.5rem_9.5rem_minmax(0,1fr)_7.5rem]">
              <span
                className={`mt-0.5 flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-bold ${st.cls}`}
                title={st.word}
                aria-label={st.word}
              >
                {st.glyph}
              </span>
              <span className="text-[13px] font-medium">
                <span className="mr-1 text-muted">{i + 1}.</span>
                {s.label}
              </span>
              <span className="col-start-2 text-[12px] text-ink-2 sm:col-start-auto">{s.detail}</span>
              <span className="col-start-2 text-[11px] text-muted sm:col-start-auto sm:text-right">
                {s.last && <div>last {ago(s.last, ref)}</div>}
                {s.next && <div>next {when(s.next)}</div>}
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}

const KIND: Record<ActivityItem["kind"], string> = {
  decision: "Decision",
  trade: "Trade",
  risk: "Risk",
  safety: "Safety",
  news: "News",
  data: "Data",
  system: "System",
};

export function ActivityFeed({ items, loading }: { items?: ActivityItem[]; loading: boolean }) {
  return (
    <Card title="Live activity" pad={false} className="flex flex-col" action={<span className="text-[11px] text-muted">what the bot did, newest first</span>}>
      {!items || items.length === 0 ? (
        <Empty>{loading ? "Loading…" : "Nothing yet. Entries appear as the bot loads data, scans news, decides and trades."}</Empty>
      ) : (
        <ul className="max-h-[460px] divide-y divide-line overflow-y-auto">
          {items.map((a) => (
            <li key={a.id} className="px-4 py-2.5 text-[13px]">
              <div className="flex items-center justify-between gap-2 text-[11px]">
                <span
                  className={`font-semibold uppercase tracking-wide ${a.level === "critical" ? "text-neg" : a.level === "warning" ? "text-warn" : "text-muted"}`}
                >
                  {a.level === "critical" ? "✕ " : a.level === "warning" ? "! " : ""}
                  {KIND[a.kind]}
                </span>
                <span className="num text-muted">{when(a.ts)}</span>
              </div>
              <div className="mt-0.5 text-ink-2">{a.text}</div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
