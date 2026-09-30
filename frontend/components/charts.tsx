"use client";

import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineStyle,
  type IChartApi,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { useEffect, useRef, useState } from "react";

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/** Re-render charts when the OS theme flips. */
function useThemeKey(): number {
  const [k, setK] = useState(0);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setK((x) => x + 1);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return k;
}

function baseChart(el: HTMLElement, height: number): IChartApi {
  return createChart(el, {
    height,
    autoSize: true,
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: token("--muted"),
      fontFamily: getComputedStyle(document.body).fontFamily,
      fontSize: 11,
      attributionLogo: true,
    },
    grid: { vertLines: { visible: false }, horzLines: { color: token("--chart-grid") } },
    rightPriceScale: { borderVisible: false },
    timeScale: { borderVisible: false, timeVisible: false },
    crosshair: { vertLine: { labelBackgroundColor: token("--text") }, horzLine: { labelBackgroundColor: token("--text") } },
    localization: { locale: "en-IN" },
  });
}

export function EquityChart({ points, height = 220 }: { points: { t: number; v: number }[]; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  useEffect(() => {
    if (!ref.current || points.length === 0) return;
    const chart = baseChart(ref.current, height);
    const up = points[points.length - 1].v >= points[0].v;
    const line = up ? token("--pos") : token("--neg");
    const s = chart.addSeries(AreaSeries, {
      lineColor: line,
      lineWidth: 2,
      topColor: `${line}33`,
      bottomColor: `${line}00`,
      priceLineVisible: false,
      priceFormat: { type: "price", precision: 0, minMove: 1 },
    });
    const seen = new Set<number>();
    s.setData(
      points
        .filter((p) => (seen.has(p.t) ? false : (seen.add(p.t), true)))
        .map((p) => ({ time: p.t as UTCTimestamp, value: p.v })),
    );
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [points, height, theme]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}

export type Candle = { t: number; o: number; h: number; l: number; c: number; v: number };
export type FillMark = { t: number; side: "BUY" | "SELL"; qty: number; price: number };

export function CandleChart({
  bars,
  fills,
  stop,
  target,
  entry,
  height = 440,
}: {
  bars: Candle[];
  fills: FillMark[];
  stop?: number | null;
  target?: number | null;
  entry?: number | null;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = baseChart(ref.current, height);
    const pos = token("--pos");
    const neg = token("--neg");
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: pos,
      downColor: neg,
      wickUpColor: pos,
      wickDownColor: neg,
      borderVisible: false,
      priceLineVisible: false,
    });
    candles.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c })));
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      color: token("--border-strong"),
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.v })));

    // Snap fills to the bar they happened in so markers render on daily charts.
    const times = bars.map((b) => b.t);
    const snap = (t: number) => {
      let best = times[0];
      for (const x of times) if (x <= t) best = x;
      return best;
    };
    const markers: SeriesMarker<Time>[] = fills
      .filter((f) => f.t >= times[0])
      .map((f) => ({
        time: snap(f.t) as UTCTimestamp,
        position: f.side === "BUY" ? "belowBar" : "aboveBar",
        color: f.side === "BUY" ? pos : neg,
        shape: f.side === "BUY" ? "arrowUp" : "arrowDown",
        text: `${f.side === "BUY" ? "B" : "S"} ${f.qty}`,
      }));
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(candles, markers);

    const line = (price: number | null | undefined, title: string, color: string, style: LineStyle) => {
      if (price) candles.createPriceLine({ price, title, color, lineStyle: style, lineWidth: 1, axisLabelVisible: true });
    };
    line(entry, "entry", token("--muted"), LineStyle.Dotted);
    line(stop, "stop", neg, LineStyle.Dashed);
    line(target, "target", pos, LineStyle.Dashed);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, fills, stop, target, entry, height, theme]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
