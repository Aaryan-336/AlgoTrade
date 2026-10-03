"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Badge, Card, Empty } from "@/components/ui";
import { day, money, pct, tone } from "@/lib/format";
import type { Market } from "@/lib/types";

const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(([e]) => setW(e.contentRect.width));
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

/** Single-series line with a crosshair + value readout on hover. */
function Sparkline({ points, height = 56 }: { points: { t: number; v: number }[]; height?: number }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  if (points.length < 2) return null;
  const vs = points.map((p) => p.v);
  const lo = Math.min(...vs);
  const hi = Math.max(...vs);
  const pad = 4;
  const x = (i: number) => (i / (points.length - 1)) * Math.max(1, w - 2 * pad) + pad;
  const y = (v: number) => pad + (1 - (v - lo) / (hi - lo || 1)) * (height - 2 * pad);
  const d = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join("");
  const h = hover ?? points.length - 1;
  return (
    <div ref={ref} className="relative">
      {w > 0 && (
        <svg
          width={w}
          height={height}
          className="block"
          role="img"
          aria-label={`Last ${points.length} closes, from ${num.format(points[0].v)} to ${num.format(vs[vs.length - 1])}`}
          onPointerMove={(e) => {
            const r = e.currentTarget.getBoundingClientRect();
            const i = Math.round(((e.clientX - r.left - pad) / Math.max(1, w - 2 * pad)) * (points.length - 1));
            setHover(Math.max(0, Math.min(points.length - 1, i)));
          }}
          onPointerLeave={() => setHover(null)}
        >
          <path d={d} fill="none" stroke="var(--text-2)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {hover !== null && <line x1={x(h)} x2={x(h)} y1={0} y2={height} stroke="var(--border-strong)" strokeWidth={1} />}
          <circle cx={x(h)} cy={y(points[h].v)} r={4} fill="var(--text)" stroke="var(--surface)" strokeWidth={2} />
        </svg>
      )}
      {hover !== null && (
        <div className="pointer-events-none absolute -top-1 right-0 rounded-md border border-line bg-surface px-1.5 py-0.5 text-[11px] shadow-sm">
          <span className="text-muted">{day(new Date(points[h].t * 1000).toISOString())}</span>{" "}
          <span className="num font-medium">{num.format(points[h].v)}</span>
        </div>
      )}
    </div>
  );
}

/** Up / flat / down split. Counts are always written out, so colour is never the only cue. */
function BreadthBar({ up, flat, down }: { up: number; flat: number; down: number }) {
  const total = up + flat + down || 1;
  const seg = [
    { n: up, cls: "bg-pos", label: `${up} up` },
    { n: flat, cls: "bg-line-strong", label: `${flat} flat` },
    { n: down, cls: "bg-neg", label: `${down} down` },
  ].filter((s) => s.n > 0);
  return (
    <div>
      <div className="flex h-2.5 gap-[2px] overflow-hidden rounded">
        {seg.map((s) => (
          <div key={s.label} title={s.label} className={`${s.cls} h-full`} style={{ width: `${(s.n / total) * 100}%` }} />
        ))}
      </div>
      <div className="mt-1.5 flex justify-between text-[11px] text-ink-2">
        <span>▲ {up} up</span>
        <span className="text-muted">{flat} flat</span>
        <span>▼ {down} down</span>
      </div>
    </div>
  );
}

/** Diverging bars around zero: direction and the signed label carry polarity, not only colour. */
function SectorBars({ rows }: { rows: Market["sectors"] }) {
  const [hover, setHover] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const max = Math.max(0.5, ...rows.map((r) => Math.abs(r.change_pct)));
  // Strongest and weakest by default; the middle of the list adds little.
  const shown = all || rows.length <= 8 ? rows : [...rows.slice(0, 4), ...rows.slice(-4)];
  return (
    <>
    <ul className="space-y-1">
      {shown.map((r) => {
        const w = (Math.abs(r.change_pct) / max) * 50;
        const pos = r.change_pct >= 0;
        return (
          <li
            key={r.sector}
            className={`grid grid-cols-[minmax(0,7.5rem)_1fr_3.75rem] items-center gap-2 rounded px-1 py-0.5 text-[12px] ${hover === r.sector ? "bg-surface-2" : ""}`}
            onPointerEnter={() => setHover(r.sector)}
            onPointerLeave={() => setHover(null)}
            title={`${r.sector}: ${pct(r.change_pct, 2, true)} average across ${r.count} stock${r.count === 1 ? "" : "s"}`}
          >
            <span className="truncate text-ink-2">{r.sector}</span>
            <span className="relative h-3">
              <span className="absolute inset-y-0 left-1/2 w-px bg-line-strong" />
              <span
                className={`absolute inset-y-[2px] ${pos ? "rounded-r bg-pos" : "rounded-l bg-neg"}`}
                style={pos ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
              />
            </span>
            <span className="num text-right text-ink">{pct(r.change_pct, 2, true)}</span>
          </li>
        );
      })}
    </ul>
    {rows.length > 8 && (
      <button onClick={() => setAll(!all)} className="mt-1.5 px-1 text-[12px] text-accent hover:underline">
        {all ? "Show strongest and weakest only" : `Show all ${rows.length} sectors`}
      </button>
    )}
    </>
  );
}

function MoverList({ title, rows }: { title: string; rows: Market["gainers"] }) {
  return (
    <div className="min-w-0">
      <div className="label mb-2">{title}</div>
      {rows.length === 0 ? (
        <div className="text-[12px] text-muted">None</div>
      ) : (
        <ul className="space-y-1.5 text-[13px]">
          {rows.map((r) => (
            <li key={r.symbol} className="flex items-baseline justify-between gap-2">
              <Link href={`/chart?symbol=${encodeURIComponent(r.symbol)}`} className="truncate font-medium hover:text-accent">
                {r.symbol}
                {r.held && <span className="ml-1 text-[10px] font-normal text-accent">held</span>}
              </Link>
              <span className="num shrink-0 text-[12px]">
                <span className="text-muted">{money(r.ltp)}</span>{" "}
                <span className={tone(r.change_pct)}>{pct(r.change_pct, 2, true)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function mood(avg: number): { word: string; tone: "pos" | "neg" | "neutral" } {
  if (avg > 0.15) return { word: "Positive", tone: "pos" };
  if (avg < -0.15) return { word: "Negative", tone: "neg" };
  return { word: "Mixed", tone: "neutral" };
}

export function MarketInsights({ data, loading }: { data?: Market; loading: boolean }) {
  if (!data || (!data.index && !data.breadth)) {
    return (
      <Card title="Market">
        <Empty>{loading ? "Loading market data…" : "No market data yet. It appears once price history loads (log in to Upstox, or wait for the delayed history)."}</Empty>
      </Card>
    );
  }
  const { index, vix, breadth, sentiment } = data;
  const asOf = data.as_of.live ? "live" : data.as_of.bar_date ? `as of ${day(data.as_of.bar_date)} close` : "";
  return (
    <Card
      title="Market"
      action={
        <span className="flex items-center gap-1.5 text-xs text-muted">
          {data.as_of.live && <span className="h-1.5 w-1.5 rounded-full bg-pos motion-safe:animate-pulse" />}
          Nifty 50 universe · {asOf}
        </span>
      }
    >
      <div className="grid gap-5 md:grid-cols-2 xl:grid-cols-4">
        {/* Index: the headline number, its trend, and what the regime filter thinks. */}
        <div className="min-w-0">
          <div className="label">Nifty 50</div>
          {index ? (
            <>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num text-[22px] font-semibold leading-7">{num.format(index.last)}</span>
                <span className={`num text-[13px] ${tone(index.change_pct)}`}>{pct(index.change_pct, 2, true)}</span>
              </div>
              <div className="num text-[11px] text-muted">20 days {pct(index.change_20d_pct, 1, true)}</div>
              <div className="mt-2">
                <Sparkline points={index.spark} />
              </div>
              {index.above_ema !== null && (
                <div className="mt-1.5 text-[12px] text-ink-2">
                  {index.above_ema ? "▲" : "▼"} {pct(Math.abs(index.ema_gap_pct ?? 0), 1)} {index.above_ema ? "above" : "below"} its{" "}
                  {index.ema_period}-day average:{" "}
                  <span className={index.above_ema ? "text-pos" : "text-neg"}>{index.above_ema ? "buys allowed" : "new buys paused"}</span>
                </div>
              )}
            </>
          ) : (
            <div className="mt-1 text-[12px] text-muted">Index history not loaded.</div>
          )}
        </div>

        {/* Breadth: is the move broad or carried by a few names? */}
        <div className="min-w-0">
          <div className="label">Breadth</div>
          {breadth ? (
            <>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num text-[22px] font-semibold leading-7">
                  {breadth.advancers}:{breadth.decliners}
                </span>
                <span className="text-[12px] text-muted">advancing : declining</span>
              </div>
              <div className="mt-3">
                <BreadthBar up={breadth.advancers} flat={breadth.unchanged} down={breadth.decliners} />
              </div>
              <dl className="mt-3 space-y-1 text-[12px]">
                <div className="flex justify-between">
                  <dt className="text-ink-2">Above 50-day average</dt>
                  <dd className="num">{pct(breadth.pct_above_50ema, 0)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-ink-2">At a 20-day high</dt>
                  <dd className="num">{breadth.at_20d_high} stocks</dd>
                </div>
              </dl>
            </>
          ) : (
            <div className="mt-1 text-[12px] text-muted">—</div>
          )}
        </div>

        {/* Volatility */}
        <div className="min-w-0">
          <div className="label">India VIX</div>
          {vix ? (
            <>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="num text-[22px] font-semibold leading-7">{num.format(vix.last)}</span>
                <span className="num text-[13px] text-ink-2">{pct(vix.change_pct, 1, true)}</span>
              </div>
              <div className="mt-2">
                <Badge tone={vix.elevated ? "warn" : "neutral"} dot>
                  {vix.elevated ? `Above ${vix.reduce_above}: new positions are half size` : `Calm (below ${vix.reduce_above})`}
                </Badge>
              </div>
              <p className="mt-2 text-[12px] text-muted">Expected volatility over the next 30 days. Higher means bigger swings.</p>
            </>
          ) : (
            <div className="mt-1 text-[12px] text-muted">VIX history not loaded.</div>
          )}
        </div>

        {/* News mood from Groq-scored headlines. */}
        <div className="min-w-0">
          <div className="label">News mood (30 days)</div>
          {sentiment ? (
            <>
              <div className="mt-1 flex items-baseline gap-2">
                <span className="text-[22px] font-semibold leading-7">{mood(sentiment.average).word}</span>
                <span className="num text-[13px] text-ink-2">{sentiment.average >= 0 ? "+" : "−"}{Math.abs(sentiment.average).toFixed(2)}</span>
              </div>
              <div className="mt-0.5 text-[11px] text-muted">
                {sentiment.positive} positive · {sentiment.negative} negative · {sentiment.covered} stocks with news
              </div>
              <ul className="mt-2.5 space-y-1 text-[12px]">
                {sentiment.most_positive.slice(0, 2).map((s) => (
                  <li key={s.symbol} className="flex justify-between">
                    <span>▲ {s.symbol}</span>
                    <span className="num text-ink-2">+{s.score.toFixed(2)} · {s.news} items</span>
                  </li>
                ))}
                {sentiment.most_negative.slice(0, 2).map((s) => (
                  <li key={s.symbol} className="flex justify-between">
                    <span>▼ {s.symbol}</span>
                    <span className="num text-ink-2">−{Math.abs(s.score).toFixed(2)} · {s.news} items</span>
                  </li>
                ))}
              </ul>
              {sentiment.vetoed.length > 0 && (
                <div className="mt-2 text-[11px] text-warn">Blocked by bad news: {sentiment.vetoed.join(", ")}</div>
              )}
            </>
          ) : (
            <div className="mt-1 text-[12px] text-muted">No scored news yet. Scans run every 30 minutes.</div>
          )}
        </div>
      </div>

      {(data.sectors.length > 0 || data.gainers.length > 0) && (
        <div className="mt-5 grid gap-5 border-t border-line pt-4 md:grid-cols-2 xl:grid-cols-4">
          <div className="min-w-0 md:col-span-2">
            <div className="label mb-2">Sectors (average change)</div>
            <SectorBars rows={data.sectors} />
          </div>
          <MoverList title="Top gainers" rows={data.gainers} />
          <MoverList title="Top losers" rows={data.losers} />
        </div>
      )}
    </Card>
  );
}
