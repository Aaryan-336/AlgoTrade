"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, wsUrl } from "./api";
import type { LivePrices, Portfolio, Status } from "./types";

type Live = {
  status?: Status;
  portfolio?: Portfolio;
  prices: LivePrices;
  connected: boolean;
  /** When the last update arrived (ms since epoch), from either channel. */
  updatedAt?: number;
  error?: string;
};
const LiveContext = createContext<Live>({ connected: false, prices: {} });

/** A frame is expected every 2s; after this long the socket is treated as dead. */
const STALE_MS = 8000;

/** Streams status, portfolio and prices over the WebSocket. If the socket drops
 * or silently stalls, it reconnects and polls over HTTP in the meantime, so the
 * page never needs a manual reload. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<number>();
  const retry = useRef(0);
  const lastFrame = useRef(0);

  const status = useQuery({
    queryKey: ["status"],
    queryFn: () => api<Status>("/api/status"),
    refetchInterval: connected ? false : 5000,
  });
  const portfolio = useQuery({
    queryKey: ["portfolio"],
    queryFn: () => api<Portfolio>("/api/portfolio"),
    refetchInterval: connected ? false : 5000,
  });
  const prices = useQuery({
    queryKey: ["prices"],
    queryFn: () => api<LivePrices>("/api/prices"),
    refetchInterval: connected ? false : 5000,
  });

  useEffect(() => {
    if (!connected && (status.dataUpdatedAt || prices.dataUpdatedAt)) {
      setUpdatedAt(Math.max(status.dataUpdatedAt, prices.dataUpdatedAt));
    }
  }, [connected, status.dataUpdatedAt, prices.dataUpdatedAt]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let closed = false;
    const open = () => {
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        return;
      }
      ws.onopen = () => {
        retry.current = 0;
        lastFrame.current = Date.now();
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          lastFrame.current = Date.now();
          setUpdatedAt(lastFrame.current);
          qc.setQueryData(["status"], msg.status);
          qc.setQueryData(["portfolio"], msg.portfolio);
          if (msg.prices) qc.setQueryData(["prices"], msg.prices);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retry.current = Math.min(retry.current + 1, 5);
        timer = setTimeout(open, 1000 * 2 ** retry.current);
      };
    };
    open();
    // A socket can stall without closing (sleep, network change, proxy). Detect
    // it and reconnect; HTTP polling covers the gap.
    const watchdog = setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastFrame.current > STALE_MS) {
        setConnected(false);
        ws.close();
      }
    }, 2000);
    // Coming back to the tab: refresh at once rather than waiting.
    const onVisible = () => {
      if (document.visibilityState === "visible") qc.invalidateQueries();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      closed = true;
      clearInterval(watchdog);
      document.removeEventListener("visibilitychange", onVisible);
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  const error = (status.error ?? portfolio.error)?.message;
  return (
    <LiveContext.Provider
      value={{ status: status.data, portfolio: portfolio.data, prices: prices.data ?? {}, connected, updatedAt, error }}
    >
      {children}
    </LiveContext.Provider>
  );
}

export const useLive = () => useContext(LiveContext);

/** Refresh interval for page data: fast while the market is open, slow otherwise. */
export function useLiveInterval(openMs: number, closedMs = 60_000): number {
  const { status } = useLive();
  return status?.market_open ? openMs : closedMs;
}
