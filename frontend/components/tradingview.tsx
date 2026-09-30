"use client";

import { useEffect, useRef } from "react";

/**
 * Official TradingView embeddable widgets. Display only: nothing here feeds
 * the bot's decisions (docs/data-sources.md §1). Some NSE symbols are not
 * available in free widgets, so BSE listings are used by default.
 */
function useWidget(script: string, config: Record<string, unknown>) {
  const ref = useRef<HTMLDivElement>(null);
  const key = JSON.stringify(config);
  useEffect(() => {
    const host = ref.current;
    if (!host) return;
    host.innerHTML = "";
    const inner = document.createElement("div");
    inner.className = "tradingview-widget-container__widget";
    host.appendChild(inner);
    const s = document.createElement("script");
    s.src = `https://s3.tradingview.com/external-embedding/${script}`;
    s.async = true;
    s.type = "text/javascript";
    s.innerHTML = key;
    host.appendChild(s);
    return () => {
      host.innerHTML = "";
    };
  }, [script, key]);
  return ref;
}

const dark = () => typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;

export function TickerTape({ symbols }: { symbols: string[] }) {
  const ref = useWidget("embed-widget-ticker-tape.js", {
    symbols: [
      { proName: "BSE:SENSEX", title: "Sensex" },
      ...symbols.slice(0, 12).map((s) => ({ proName: `BSE:${s.replace("&", "_")}`, title: s })),
    ],
    showSymbolLogo: false,
    isTransparent: true,
    displayMode: "compact",
    colorTheme: dark() ? "dark" : "light",
    locale: "en",
  });
  return <div ref={ref} className="tradingview-widget-container min-h-[46px]" />;
}

export function AdvancedChart({ symbol }: { symbol: string }) {
  const ref = useWidget("embed-widget-advanced-chart.js", {
    autosize: true,
    symbol: `BSE:${symbol.replace("&", "_")}`,
    interval: "D",
    timezone: "Asia/Kolkata",
    theme: dark() ? "dark" : "light",
    style: "1",
    locale: "en",
    allow_symbol_change: true,
    hide_side_toolbar: false,
    backgroundColor: "rgba(0,0,0,0)",
  });
  return <div ref={ref} className="tradingview-widget-container h-[520px] w-full" />;
}
