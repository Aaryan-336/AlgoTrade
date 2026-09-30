"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Badge, Button, Card, Empty, PageHeader } from "@/components/ui";
import { api, post } from "@/lib/api";
import { ago, tone, when } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { NewsItem, UniverseRow } from "@/lib/types";

function SentimentChip({ score }: { score: number | null }) {
  if (score === null) return <Badge>neutral</Badge>;
  const t = score > 0.15 ? "pos" : score < -0.15 ? "neg" : "neutral";
  return <Badge tone={t}>{score > 0 ? "+" : ""}{score.toFixed(2)}</Badge>;
}

export default function News() {
  const { status } = useLive();
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const news = useQuery({
    queryKey: ["news", symbol],
    queryFn: () => api<NewsItem[]>(`/api/news?limit=150${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`),
    refetchInterval: 60_000,
  });
  const universe = useQuery({ queryKey: ["universe"], queryFn: () => api<UniverseRow[]>("/api/universe") });
  const ranked = [...(universe.data ?? [])].filter((u) => u.news_count > 0).sort((a, b) => (b.sentiment ?? 0) - (a.sentiment ?? 0));

  return (
    <div className="space-y-5">
      <PageHeader
        title="News & sentiment"
        sub={`Headlines from the last 30 days, scored by Groq (${status?.groq.model ?? "…"}). Sentiment adjusts scores and can veto entries; it never places a trade.`}
        action={
          <Button onClick={async () => { await post("/api/news/refresh"); setTimeout(() => qc.invalidateQueries({ queryKey: ["news"] }), 4000); }}>
            Refresh now
          </Button>
        }
      />
      {status && !status.groq.configured && (
        <div className="rounded-[10px] border border-line bg-surface-2 px-4 py-3 text-[13px] text-ink-2">
          Groq is not configured, so headlines are collected but not scored and sentiment stays neutral. Add <code className="num">GROQ_API_KEY</code> to <code className="num">backend/.env</code>.
        </div>
      )}
      <div className="grid gap-5 xl:grid-cols-3">
        <Card title="By symbol" pad={false} className="self-start">
          {ranked.length === 0 ? (
            <Empty>No scored news yet.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              <li>
                <button onClick={() => setSymbol("")} className={`w-full px-4 py-2 text-left text-[13px] ${symbol === "" ? "bg-surface-2 font-semibold" : ""}`}>
                  All symbols
                </button>
              </li>
              {ranked.map((u) => (
                <li key={u.symbol}>
                  <button
                    onClick={() => setSymbol(u.symbol)}
                    className={`flex w-full items-center justify-between px-4 py-2 text-left text-[13px] hover:bg-surface-2 ${symbol === u.symbol ? "bg-surface-2 font-semibold" : ""}`}
                  >
                    <span>
                      {u.symbol}
                      {u.negative_material && <span className="ml-2"><Badge tone="neg">veto</Badge></span>}
                    </span>
                    <span className="flex items-center gap-2">
                      <span className="text-[11px] text-muted">{u.news_count}</span>
                      <SentimentChip score={u.sentiment} />
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
        <Card
          title={symbol ? `Headlines · ${symbol}` : "Headlines"}
          className="xl:col-span-2"
          pad={false}
          action={<span className="text-[11px] text-muted">last run {ago(status?.news.last_run)}</span>}
        >
          {!news.data?.length ? (
            <Empty>No headlines yet. The first news cycle runs a few seconds after the API starts.</Empty>
          ) : (
            <ul className="divide-y divide-line">
              {news.data.map((n) => (
                <li key={n.id} className="px-4 py-3">
                  <div className="flex items-start justify-between gap-3">
                    <a href={n.url} target="_blank" rel="noreferrer noopener" className="text-[13px] font-medium leading-5 hover:text-accent">
                      {n.headline}
                    </a>
                    <div className="flex shrink-0 gap-1">
                      {n.sentiment.length === 0 ? (
                        <Badge>{n.scored ? "unscored" : "pending"}</Badge>
                      ) : (
                        n.sentiment.map((s) => (
                          <span key={s.symbol} title={`${s.event_type} · confidence ${s.confidence.toFixed(2)}`}>
                            <SentimentChip score={s.score} />
                          </span>
                        ))
                      )}
                    </div>
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-muted">
                    <span>{n.publisher}</span>
                    <span>·</span>
                    <span>{when(n.published_at)}</span>
                    <span>·</span>
                    <span>{n.symbols.join(", ")}</span>
                    {n.sentiment.some((s) => s.is_material) && <Badge tone="warn">material</Badge>}
                  </div>
                  {n.sentiment[0]?.summary && <p className={`mt-1 text-[12px] ${tone(n.sentiment[0].score)} opacity-90`}>{n.sentiment[0].summary}</p>}
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
