"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorNote, PageHeader } from "@/components/ui";
import { API_URL, api, getToken, post, put, setToken } from "@/lib/api";
import { when } from "@/lib/format";
import { useLive } from "@/lib/live";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- config is a validated server-side schema
type Json = Record<string, any>;
type ConfigResponse = {
  active_version: number;
  latest_version: number;
  pending_activation: boolean;
  risk: Json;
  strategy: Json;
  history: { version: number; ts: string; changed_by: string; reason: string }[];
  market_open: boolean;
  universe_all: { symbol: string; name: string; sector: string }[];
};

function get(obj: Json, path: string): unknown {
  return path.split(".").reduce<unknown>((o, k) => (o as Json)?.[k], obj);
}
function set(obj: Json, path: string, value: unknown): Json {
  const copy = structuredClone(obj);
  const keys = path.split(".");
  let cur: Json = copy;
  for (const k of keys.slice(0, -1)) cur = cur[k];
  cur[keys[keys.length - 1]] = value;
  return copy;
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="text-[13px] font-medium text-ink">{label}</span>
      {hint && <span className="block text-[11px] text-muted">{hint}</span>}
      <div className="mt-1.5">{children}</div>
    </label>
  );
}

export default function Settings() {
  const { status } = useLive();
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api<ConfigResponse>("/api/config") });
  const [draft, setDraft] = useState<{ risk: Json; strategy: Json } | null>(null);
  const [reason, setReason] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [saved, setSaved] = useState("");
  const [token, setTokenInput] = useState("");

  useEffect(() => {
    if (cfg.data && !draft) setDraft({ risk: cfg.data.risk, strategy: cfg.data.strategy });
  }, [cfg.data, draft]);
  useEffect(() => setTokenInput(getToken()), []);

  if (!cfg.data || !draft) return <div className="text-sm text-muted">Loading settings…</div>;
  const locked = cfg.data.market_open;

  const num = (section: "risk" | "strategy", path: string, step = "any") => (
    <input
      type="number"
      step={step}
      disabled={locked}
      className="w-full"
      value={String(get(draft[section], path) ?? "")}
      onChange={(e) => setDraft({ ...draft, [section]: set(draft[section], path, e.target.value === "" ? "" : Number(e.target.value)) })}
    />
  );
  const toggle = (section: "risk" | "strategy", path: string, label: string) => (
    <label className="flex items-center gap-2 text-[13px]">
      <input
        type="checkbox"
        disabled={locked}
        checked={Boolean(get(draft[section], path))}
        onChange={(e) => setDraft({ ...draft, [section]: set(draft[section], path, e.target.checked) })}
      />
      {label}
    </label>
  );
  const watch: string[] = draft.strategy.universe.symbols ?? [];

  async function save() {
    setErr(null);
    setSaved("");
    try {
      const r = await put<{ version: number; note: string }>("/api/config", { ...draft, reason });
      setSaved(`Saved as version ${r.version}. ${r.note}`);
      setReason("");
      qc.invalidateQueries();
    } catch (e) {
      setErr(e);
    }
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Settings"
        sub="Every change is saved as a new config version with your reason. Changes are refused during market hours (09:15–15:30 IST)."
        action={<Badge tone={locked ? "warn" : "pos"} dot>{locked ? "Locked: market open" : "Editable"}</Badge>}
      />

      <Card title="Connections">
        <div className="grid gap-5 md:grid-cols-3">
          <div>
            <div className="text-[13px] font-medium">Upstox market data</div>
            <p className="mt-1 text-[12px] text-muted">
              {status?.upstox.logged_in
                ? `Connected since ${when(status.upstox.since)}.`
                : status?.upstox.configured
                  ? "Configured. Log in each trading day; the token expires overnight."
                  : "Set UPSTOX_API_KEY, UPSTOX_API_SECRET and UPSTOX_REDIRECT_URI in backend/.env."}
            </p>
            <div className="mt-2 flex gap-2">
              {status?.upstox.configured && !status.upstox.logged_in && (
                <a href={`${API_URL}/api/auth/upstox/login`}><Button variant="primary">Log in to Upstox</Button></a>
              )}
              {status?.upstox.logged_in && <Button onClick={() => post("/api/auth/upstox/logout").then(() => qc.invalidateQueries())}>Disconnect</Button>}
            </div>
          </div>
          <div>
            <div className="text-[13px] font-medium">Groq sentiment</div>
            <p className="mt-1 text-[12px] text-muted">
              {status?.groq.configured ? `Using ${status.groq.model}.` : "Set GROQ_API_KEY in backend/.env."}
              {status?.groq.last_error && <span className="block text-neg">Last error: {status.groq.last_error}</span>}
            </p>
          </div>
          <div>
            <div className="text-[13px] font-medium">Dashboard access token</div>
            <p className="mt-1 text-[12px] text-muted">Only needed when API_TOKEN is set on the backend. Stored in this browser.</p>
            <div className="mt-2 flex gap-2">
              <input type="password" className="min-w-0 flex-1" value={token} onChange={(e) => setTokenInput(e.target.value)} />
              <Button onClick={() => { setToken(token.trim()); qc.invalidateQueries(); }}>Save</Button>
            </div>
          </div>
        </div>
      </Card>

      <fieldset disabled={locked} className="space-y-5">
        <Card title="Capital and trading style">
          <div className="grid gap-5 md:grid-cols-3">
            <Field label="Paper capital (₹)" hint="Starting cash for the paper account">{num("strategy", "capital", "1000")}</Field>
            <Field label="Decision bars" hint="Daily = swing, low turnover (recommended at small capital)">
              <select className="w-full" value={draft.strategy.timeframe} onChange={(e) => setDraft({ ...draft, strategy: set(draft.strategy, "timeframe", e.target.value) })}>
                <option value="1d">Daily (swing)</option>
                <option value="15m">15-minute</option>
              </select>
            </Field>
            <Field label="Max open positions">{num("risk", "max_open_positions", "1")}</Field>
          </div>
        </Card>

        <Card title="Watchlist" action={<span className="text-[11px] text-muted">{watch.length ? `${watch.length} selected` : "empty = all Nifty 50"}</span>}>
          <div className="flex flex-wrap gap-1.5">
            {cfg.data.universe_all.map((u) => {
              const on = watch.includes(u.symbol);
              return (
                <button
                  type="button"
                  key={u.symbol}
                  title={`${u.name} · ${u.sector}`}
                  onClick={() => setDraft({ ...draft, strategy: set(draft.strategy, "universe.symbols", on ? watch.filter((s) => s !== u.symbol) : [...watch, u.symbol]) })}
                  className={`rounded-md border px-2 py-0.5 text-[12px] ${on ? "border-ink bg-ink text-bg" : "border-line text-ink-2 hover:bg-surface-2"}`}
                >
                  {u.symbol}
                </button>
              );
            })}
          </div>
        </Card>

        <Card title="Risk limits" action={<span className="text-[11px] text-muted">bounded server-side; unsafe values are rejected</span>}>
          <div className="grid gap-5 md:grid-cols-3 xl:grid-cols-4">
            <Field label="Risk per trade %" hint="Loss to the stop, as % of equity">{num("risk", "risk_per_trade_pct", "0.1")}</Field>
            <Field label="Max position %">{num("risk", "max_position_pct", "1")}</Field>
            <Field label="Max sector %">{num("risk", "max_sector_pct", "1")}</Field>
            <Field label="Cash buffer %">{num("risk", "min_cash_buffer_pct", "1")}</Field>
            <Field label="Daily loss limit %" hint="Blocks entries for the day">{num("risk", "max_daily_loss_pct", "0.1")}</Field>
            <Field label="Weekly loss limit %" hint="Blocks entries until reviewed">{num("risk", "max_weekly_loss_pct", "0.1")}</Field>
            <Field label="Max drawdown %" hint="Halts trading until reviewed">{num("risk", "max_drawdown_pct", "0.5")}</Field>
            <Field label="Max order value ₹" hint="Fat-finger cap">{num("risk", "max_order_value", "500")}</Field>
            <Field label="Min reward ÷ costs" hint="Skip trades whose target barely covers charges">{num("risk", "min_edge_to_cost_ratio", "0.5")}</Field>
            <Field label="Max orders per day">{num("risk", "max_orders_per_day", "1")}</Field>
            <Field label="Max correlation" hint="With any existing holding">{num("risk", "max_correlation", "0.05")}</Field>
            <Field label="Price deviation %" hint="Reject if price moved this much since the signal">{num("risk", "max_price_deviation_pct", "0.1")}</Field>
          </div>
        </Card>

        <div className="grid gap-5 xl:grid-cols-2">
          <Card title="Trend strategy">
            <div className="mb-4">{toggle("strategy", "strategies.trend.enabled", "Enabled")}</div>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <Field label="Fast EMA">{num("strategy", "strategies.trend.fast_ema", "1")}</Field>
              <Field label="Slow EMA">{num("strategy", "strategies.trend.slow_ema", "1")}</Field>
              <Field label="Stop (× ATR)">{num("strategy", "strategies.trend.atr_stop_mult", "0.1")}</Field>
              <Field label="RSI min">{num("strategy", "strategies.trend.rsi_min", "1")}</Field>
              <Field label="RSI max">{num("strategy", "strategies.trend.rsi_max", "1")}</Field>
              <Field label="Reward : risk">{num("strategy", "strategies.trend.reward_risk", "0.1")}</Field>
              <Field label="Max holding days">{num("strategy", "strategies.trend.max_holding_days", "1")}</Field>
            </div>
          </Card>
          <Card title="Breakout strategy">
            <div className="mb-4">{toggle("strategy", "strategies.breakout.enabled", "Enabled")}</div>
            <div className="grid grid-cols-2 gap-4 md:grid-cols-3">
              <Field label="Lookback bars">{num("strategy", "strategies.breakout.lookback", "1")}</Field>
              <Field label="Volume × average">{num("strategy", "strategies.breakout.volume_mult", "0.1")}</Field>
              <Field label="Stop (× ATR)">{num("strategy", "strategies.breakout.atr_stop_mult", "0.1")}</Field>
              <Field label="Reward : risk">{num("strategy", "strategies.breakout.reward_risk", "0.1")}</Field>
              <Field label="Max holding days">{num("strategy", "strategies.breakout.max_holding_days", "1")}</Field>
            </div>
          </Card>
        </div>

        <div className="grid gap-5 xl:grid-cols-3">
          <Card title="Scoring">
            <div className="grid grid-cols-2 gap-4">
              <Field label="Trend weight">{num("strategy", "decision.weights.trend", "0.05")}</Field>
              <Field label="Momentum weight">{num("strategy", "decision.weights.momentum", "0.05")}</Field>
              <Field label="Volume weight">{num("strategy", "decision.weights.volume", "0.05")}</Field>
              <Field label="Sentiment weight">{num("strategy", "decision.weights.sentiment", "0.05")}</Field>
              <Field label="Entry threshold">{num("strategy", "decision.entry_threshold", "0.01")}</Field>
              <Field label="Exit threshold">{num("strategy", "decision.exit_threshold", "0.01")}</Field>
            </div>
          </Card>
          <Card title="Rotation">
            <div className="mb-4">{toggle("strategy", "rotation.enabled", "Swap the weakest holding for a clearly better stock")}</div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Min score gap" hint="Candidate must beat holding by">{num("strategy", "rotation.min_score_gap", "0.01")}</Field>
              <Field label="Min hold days">{num("strategy", "rotation.min_hold_days", "1")}</Field>
            </div>
          </Card>
          <Card title="News sentiment">
            <div className="mb-4 space-y-2">
              {toggle("strategy", "sentiment.enabled", "Use news sentiment")}
              {toggle("strategy", "sentiment.exit_on_negative_material", "Exit on negative material news")}
            </div>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Lookback days">{num("strategy", "sentiment.lookback_days", "1")}</Field>
              <Field label="Half-life days">{num("strategy", "sentiment.half_life_days", "0.5")}</Field>
            </div>
          </Card>
        </div>

        <Card title="Regime and events">
          <div className="grid gap-5 md:grid-cols-3">
            <div className="space-y-2">{toggle("strategy", "regime.enabled", "Block entries when Nifty 50 is below its long EMA")}</div>
            <Field label="Index EMA">{num("strategy", "regime.index_ema", "1")}</Field>
            <Field label="Halve size when India VIX above">{num("strategy", "regime.vix_reduce_above", "0.5")}</Field>
            <Field label="Event blackout dates" hint="Comma-separated YYYY-MM-DD (budget, RBI policy…)">
              <input
                className="w-full"
                value={(draft.strategy.regime.event_blackout_dates ?? []).join(", ")}
                onChange={(e) => setDraft({ ...draft, strategy: set(draft.strategy, "regime.event_blackout_dates", e.target.value.split(",").map((s) => s.trim()).filter(Boolean)) })}
              />
            </Field>
          </div>
        </Card>
      </fieldset>

      <Card>
        <div className="flex flex-col gap-3 md:flex-row md:items-end">
          <Field label="Reason for this change" hint="Stored with the new config version">
            <input className="w-full md:w-[420px]" value={reason} onChange={(e) => setReason(e.target.value)} disabled={locked} />
          </Field>
          <div className="flex gap-2">
            <Button variant="primary" disabled={locked || reason.trim().length < 3} onClick={save}>Save new version</Button>
            <Button variant="ghost" onClick={() => setDraft({ risk: cfg.data!.risk, strategy: cfg.data!.strategy })}>Discard</Button>
          </div>
        </div>
        <div className="mt-3 space-y-2">
          <ErrorNote error={err} />
          {saved && <div className="text-[13px] text-pos">{saved}</div>}
          {cfg.data.pending_activation && (
            <div className="text-[13px] text-warn">Version {cfg.data.latest_version} is saved and activates when the engine next reloads (outside market hours).</div>
          )}
        </div>
      </Card>

      <Card title="Version history" pad={false}>
        <table className="data">
          <thead><tr><th>Version</th><th>When</th><th>By</th><th>Reason</th></tr></thead>
          <tbody>
            {cfg.data.history.map((h) => (
              <tr key={h.version}>
                <td className="num">v{h.version}{h.version === cfg.data!.active_version && <span className="ml-2"><Badge tone="accent">active</Badge></span>}</td>
                <td className="num text-ink-2">{when(h.ts)}</td>
                <td className="text-ink-2">{h.changed_by}</td>
                <td className="text-ink-2">{h.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}
