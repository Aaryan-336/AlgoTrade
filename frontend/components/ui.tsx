"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({ title, action, children, className = "", pad = true }: {
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
  pad?: boolean;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="text-[13px] font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className={pad ? "p-4" : ""}>{children}</div>
    </section>
  );
}

export function Stat({ label, value, sub, tone = "", big = false }: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: string;
  big?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="label">{label}</div>
      <div className={`num mt-1 truncate font-semibold ${big ? "text-[28px] leading-8" : "text-lg"} ${tone}`}>
        {value}
      </div>
      {sub && <div className="num mt-0.5 text-xs text-muted">{sub}</div>}
    </div>
  );
}

type BadgeTone = "neutral" | "pos" | "neg" | "warn" | "accent";
const BADGE: Record<BadgeTone, string> = {
  neutral: "bg-surface-2 text-ink-2 border-line",
  pos: "bg-pos-soft text-pos border-transparent",
  neg: "bg-neg-soft text-neg border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  accent: "bg-accent-soft text-accent border-transparent",
};

export function Badge({ children, tone = "neutral", dot = false }: {
  children: ReactNode;
  tone?: BadgeTone;
  dot?: boolean;
}) {
  return (
    <span className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium ${BADGE[tone]}`}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  );
}

type Variant = "primary" | "secondary" | "danger" | "ghost";
const BTN: Record<Variant, string> = {
  primary: "bg-ink text-bg hover:opacity-90",
  secondary: "border border-line-strong bg-surface text-ink hover:bg-surface-2",
  danger: "bg-neg text-white hover:opacity-90",
  ghost: "text-ink-2 hover:bg-surface-2",
};

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
}) {
  return (
    <button
      {...props}
      className={`inline-flex h-8 items-center justify-center gap-1.5 rounded-lg px-3 text-[13px] font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${BTN[variant]} ${className}`}
    />
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-10 text-center text-sm text-muted">{children}</div>;
}

export function Banner({ tone, title, children, action }: {
  tone: "warn" | "neg" | "accent" | "neutral";
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  const cls = {
    warn: "border-warn/30 bg-warn-soft",
    neg: "border-neg/30 bg-neg-soft",
    accent: "border-accent/25 bg-accent-soft",
    neutral: "border-line bg-surface-2",
  }[tone];
  const titleCls = { warn: "text-warn", neg: "text-neg", accent: "text-accent", neutral: "text-ink" }[tone];
  return (
    <div className={`flex flex-col gap-2 rounded-[10px] border px-4 py-3 sm:flex-row sm:items-center sm:justify-between ${cls}`}>
      <div className="min-w-0">
        <div className={`text-[13px] font-semibold ${titleCls}`}>{title}</div>
        {children && <div className="mt-0.5 text-[13px] text-ink-2">{children}</div>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

export function Meter({ value, max, danger = 0.8 }: { value: number; max: number; danger?: number }) {
  const ratio = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const color = ratio >= 1 ? "bg-neg" : ratio >= danger ? "bg-warn" : "bg-accent";
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

export function ScoreBar({ value }: { value: number }) {
  const v = Math.max(0, Math.min(1, value));
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-2">
        <div className="h-full rounded-full bg-accent" style={{ width: `${v * 100}%` }} />
      </div>
      <span className="num text-xs text-ink-2">{v.toFixed(2)}</span>
    </div>
  );
}

export function PageHeader({ title, sub, action }: { title: string; sub?: ReactNode; action?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink">{title}</h1>
        {sub && <p className="mt-1 text-[13px] text-muted">{sub}</p>}
      </div>
      {action}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="rounded-lg border border-neg/30 bg-neg-soft px-3 py-2 text-[13px] text-neg">{msg}</div>;
}
