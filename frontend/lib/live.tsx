"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { api, wsUrl } from "./api";
import type { Portfolio, Status } from "./types";

type Live = { status?: Status; portfolio?: Portfolio; connected: boolean; error?: string };
const LiveContext = createContext<Live>({ connected: false });

/** Streams status + portfolio over the WebSocket; falls back to polling. */
export function LiveProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [connected, setConnected] = useState(false);
  const retry = useRef(0);

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
        setConnected(true);
      };
      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          qc.setQueryData(["status"], msg.status);
          qc.setQueryData(["portfolio"], msg.portfolio);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setConnected(false);
        if (closed) return;
        retry.current = Math.min(retry.current + 1, 6);
        timer = setTimeout(open, 1000 * 2 ** retry.current);
      };
    };
    open();
    return () => {
      closed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [qc]);

  const error = (status.error ?? portfolio.error)?.message;
  return (
    <LiveContext.Provider value={{ status: status.data, portfolio: portfolio.data, connected, error }}>
      {children}
    </LiveContext.Provider>
  );
}

export const useLive = () => useContext(LiveContext);
