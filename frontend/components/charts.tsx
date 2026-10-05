"use client";

import {
  AreaSeries,
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
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
  livePrice,
  height = 440,
}: {
  bars: Candle[];
  fills: FillMark[];
  stop?: number | null;
  target?: number | null;
  entry?: number | null;
  /** Latest streamed price: moves today's candle between data refreshes. */
  livePrice?: number | null;
  height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  const chartRef = useRef<IChartApi | null>(null);
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const markersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const shownRef = useRef<{ first: number; count: number } | null>(null);
  const lastRef = useRef<Candle | null>(null);

  // Build the chart once per theme/size; data updates below never rebuild it,
  // so zoom and scroll position survive live updates.
  useEffect(() => {
    if (!ref.current) return;
    const chart = baseChart(ref.current, height);
    const pos = token("--pos");
    const neg = token("--neg");
    candlesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: pos,
      downColor: neg,
      wickUpColor: pos,
      wickDownColor: neg,
      borderVisible: false,
      priceLineVisible: true,
      priceLineStyle: LineStyle.Dotted,
    });
    volRef.current = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      color: token("--border-strong"),
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    markersRef.current = createSeriesMarkers(candlesRef.current, []);
    chartRef.current = chart;
    shownRef.current = null;
    linesRef.current = [];
    return () => {
      chart.remove();
      chartRef.current = candlesRef.current = volRef.current = markersRef.current = null;
    };
  }, [height, theme]);

  // Data: a full reload when the series changes (new symbol, new day), otherwise
  // only the last candle is updated in place.
  useEffect(() => {
    const candles = candlesRef.current;
    const vol = volRef.current;
    if (!candles || !vol || bars.length === 0) return;
    const toC = (b: Candle) => ({ time: b.t as UTCTimestamp, open: b.o, high: b.h, low: b.l, close: b.c });
    const shown = shownRef.current;
    const last = bars[bars.length - 1];
    if (!shown || shown.first !== bars[0].t || Math.abs(shown.count - bars.length) > 1) {
      candles.setData(bars.map(toC));
      vol.setData(bars.map((b) => ({ time: b.t as UTCTimestamp, value: b.v })));
      chartRef.current?.timeScale().fitContent();
    } else {
      if (bars.length > shown.count) {
        const prev = bars[bars.length - 2];
        candles.update(toC(prev));
        vol.update({ time: prev.t as UTCTimestamp, value: prev.v });
      }
      candles.update(toC(last));
      vol.update({ time: last.t as UTCTimestamp, value: last.v });
    }
    shownRef.current = { first: bars[0].t, count: bars.length };
    lastRef.current = last;
  }, [bars, theme]);

  // Every streamed price moves today's candle without waiting for a refetch.
  useEffect(() => {
    const candles = candlesRef.current;
    const last = lastRef.current;
    if (!candles || !last || livePrice == null) return;
    const moved = { ...last, c: livePrice, h: Math.max(last.h, livePrice), l: Math.min(last.l, livePrice) };
    lastRef.current = moved;
    candles.update({ time: moved.t as UTCTimestamp, open: moved.o, high: moved.h, low: moved.l, close: moved.c });
  }, [livePrice]);

  // Fills and the entry/stop/target lines.
  useEffect(() => {
    const candles = candlesRef.current;
    if (!candles || bars.length === 0) return;
    const pos = token("--pos");
    const neg = token("--neg");
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
    markersRef.current?.setMarkers(markers);

    for (const l of linesRef.current) candles.removePriceLine(l);
    linesRef.current = [];
    const line = (price: number | null | undefined, title: string, color: string, style: LineStyle) => {
      if (price) linesRef.current.push(candles.createPriceLine({ price, title, color, lineStyle: style, lineWidth: 1, axisLabelVisible: true }));
    };
    line(entry, "entry", token("--muted"), LineStyle.Dotted);
    line(stop, "stop", neg, LineStyle.Dashed);
    line(target, "target", pos, LineStyle.Dashed);
  }, [bars, fills, stop, target, entry, theme]);

  return <div ref={ref} style={{ height }} className="w-full" />;
}

/** Categorical series colors, validated for colour-blind separation in both
 * themes (dataviz palette slots 1-4). Assigned by position in the comparison,
 * never cycled: at most 4 runs are compared at once. */
export const SERIES_COLORS = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500"],
};

export function seriesColor(i: number): string {
  const dark = typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
  return (dark ? SERIES_COLORS.dark : SERIES_COLORS.light)[i] ?? "#888888";
}

export type CompareSeries = { label: string; points: { t: number; v: number }[] };

/** Several runs on one axis, each indexed to % return from its own start. */
export function CompareChart({
  series,
  height = 340,
  onHover,
}: {
  series: CompareSeries[];
  height?: number;
  onHover?: (values: (number | null)[] | null, t: number | null) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const theme = useThemeKey();
  useEffect(() => {
    if (!ref.current || series.length === 0) return;
    const chart = baseChart(ref.current, height);
    chart.applyOptions({ rightPriceScale: { borderVisible: false } });
    const handles = series.map((s, i) => {
      const line = chart.addSeries(LineSeries, {
        color: seriesColor(i),
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: true,
        title: s.label,
        priceFormat: { type: "custom", formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` },
      });
      const seen = new Set<number>();
      line.setData(
        s.points
          .filter((p) => (seen.has(p.t) ? false : (seen.add(p.t), true)))
          .map((p) => ({ time: p.t as UTCTimestamp, value: p.v })),
      );
      return line;
    });
    handles[0]?.createPriceLine({ price: 0, color: token("--border-strong"), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: false, title: "" });
    chart.subscribeCrosshairMove((param) => {
      if (!onHover) return;
      if (!param.time) {
        onHover(null, null);
        return;
      }
      onHover(
        handles.map((h) => {
          const d = param.seriesData.get(h) as { value?: number } | undefined;
          return d?.value ?? null;
        }),
        param.time as number,
      );
    });
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [series, height, theme, onHover]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
