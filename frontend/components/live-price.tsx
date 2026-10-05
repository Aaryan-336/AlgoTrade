"use client";

import { useEffect, useRef, useState } from "react";
import { money, pct, tone } from "@/lib/format";
import { useLive } from "@/lib/live";

/** A price that briefly tints when it changes, so live updates are visible. */
export function LivePrice({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  const prev = useRef(value);
  const [dir, setDir] = useState<"up" | "down" | null>(null);
  useEffect(() => {
    if (value == null || prev.current == null || value === prev.current) {
      prev.current = value;
      return;
    }
    setDir(value > prev.current ? "up" : "down");
    prev.current = value;
    const t = setTimeout(() => setDir(null), 900);
    return () => clearTimeout(t);
  }, [value]);
  const flash = dir === "up" ? "bg-pos-soft" : dir === "down" ? "bg-neg-soft" : "bg-transparent";
  return (
    <span className={`num rounded px-0.5 motion-safe:transition-colors motion-safe:duration-700 ${flash} ${className}`}>
      {money(value)}
    </span>
  );
}

/** Live price and day change for one symbol, from the streamed price map. */
export function useLivePrice(symbol: string | undefined, fallback?: { ltp?: number | null; chg?: number | null }) {
  const { prices } = useLive();
  const p = symbol ? prices[symbol] : undefined;
  return { ltp: p?.ltp ?? fallback?.ltp ?? null, chg: p?.chg ?? fallback?.chg ?? null, ts: p?.ts ?? null };
}

export function LiveChange({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  return <span className={`num ${tone(value)} ${className}`}>{pct(value, 2, true)}</span>;
}
