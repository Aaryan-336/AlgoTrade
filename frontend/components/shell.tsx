"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, type ReactNode } from "react";
import { money, signedMoney, tone } from "@/lib/format";
import { useLive } from "@/lib/live";
import { KillSwitchButton } from "./kill-switch";
import { TokenGate } from "./token-gate";

const NAV = [
  { href: "/", label: "Overview" },
  { href: "/watchlist", label: "Watchlist" },
  { href: "/chart", label: "Chart" },
  { href: "/positions", label: "Positions & orders" },
  { href: "/decisions", label: "Decisions" },
  { href: "/news", label: "News & sentiment" },
  { href: "/risk", label: "Risk" },
  { href: "/backtest", label: "Backtest" },
  { href: "/backtests", label: "Backtest history" },
  { href: "/compare", label: "Compare strategies" },
  { href: "/settings", label: "Settings" },
];

function Dot({ ok, warn = false }: { ok: boolean; warn?: boolean }) {
  const c = ok ? "bg-pos" : warn ? "bg-warn" : "bg-neg";
  return <span className={`inline-block h-2 w-2 rounded-full ${c}`} />;
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const { status, error } = useLive();
  const [open, setOpen] = useState(false);

  const feedOk = !!status?.provider.connected;
  const needsLogin = status && status.provider.name === "upstox" && !status.upstox.logged_in;

  return (
    <div className="min-h-screen">
      <TokenGate error={error} />
      {/* Top bar: mode first, then market/feed state, then money, then the kill switch. */}
      <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-3 px-4">
          <button
            className="rounded-md p-1.5 text-ink-2 hover:bg-surface-2 lg:hidden"
            onClick={() => setOpen(!open)}
            aria-label="Toggle navigation"
          >
            <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" strokeWidth="1.6">
              <path d="M3 5h12M3 9h12M3 13h12" />
            </svg>
          </button>
          <Link href="/" className="flex items-center gap-2 text-[15px] font-semibold tracking-tight">
            <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden>
              <rect x="1" y="1" width="18" height="18" rx="5" fill="currentColor" />
              <path d="M5 13l3-4 3 2 4-5" stroke="var(--bg)" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            AlgoTrade
          </Link>
          <span className="rounded-md border border-warn/40 bg-warn-soft px-1.5 py-0.5 text-[11px] font-semibold tracking-wide text-warn">
            {status?.mode ?? "PAPER"}
          </span>
          <div className="hidden items-center gap-4 pl-2 text-[12px] text-ink-2 md:flex">
            <span className="flex items-center gap-1.5">
              <Dot ok={!!status?.market_open} warn />
              {status?.market_open ? "Market open" : "Market closed"}
            </span>
            <span className="flex items-center gap-1.5">
              <Dot ok={feedOk} warn={!!needsLogin} />
              {status
                ? [status.provider.name, status.provider.message].filter(Boolean).join(" · ")
                : "connecting…"}
            </span>
            {status?.health && (
              <Link href="/" className="flex items-center gap-1.5 hover:text-ink" title={status.health.headline}>
                <Dot ok={status.health.verdict === "active" || status.health.verdict === "waiting"} warn={status.health.verdict === "degraded"} />
                Bot {status.health.verdict === "stopped" ? "not trading" : status.health.verdict === "degraded" ? "needs attention" : "running"}
              </Link>
            )}
          </div>
          <div className="ml-auto flex items-center gap-4">
            {status && (
              <div className="hidden text-right sm:block">
                <div className="num text-[13px] font-semibold">{money(status.equity)}</div>
                <div className={`num text-[11px] ${tone(status.day_pnl)}`}>{signedMoney(status.day_pnl)} today</div>
              </div>
            )}
            <KillSwitchButton />
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-[1400px]">
        <nav
          className={`${open ? "block" : "hidden"} fixed inset-x-0 top-14 z-10 border-b border-line bg-surface p-3 lg:sticky lg:top-14 lg:block lg:h-[calc(100vh-3.5rem)] lg:w-56 lg:shrink-0 lg:border-b-0 lg:border-r lg:bg-transparent`}
        >
          <ul className="space-y-0.5">
            {NAV.map((n) => {
              const active = path === n.href || (n.href !== "/" && path.startsWith(`${n.href}/`));
              return (
                <li key={n.href}>
                  <Link
                    href={n.href}
                    onClick={() => setOpen(false)}
                    className={`block rounded-lg px-3 py-2 text-[13px] ${active ? "bg-surface-2 font-semibold text-ink" : "text-ink-2 hover:bg-surface-2"}`}
                  >
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
          {status && (
            <div className="mt-6 space-y-1.5 border-t border-line px-3 pt-4 text-[11px] text-muted">
              <div>{status.now_ist}</div>
              <div>Config v{status.config_version} · {status.timeframe} bars</div>
              <div>Engine {status.runner.running ? "running" : "stopped"}</div>
            </div>
          )}
        </nav>
        <main className="min-w-0 flex-1 px-4 py-6 lg:px-8">{children}</main>
      </div>
    </div>
  );
}
