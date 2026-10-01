"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { Badge, Button, Card, ErrorNote, PageHeader } from "@/components/ui";
import { API_URL, api, del, getToken, post, put, setToken } from "@/lib/api";
import { when } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { Profile, ProfilesResponse } from "@/lib/types";

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- config is a validated server-side schema
type Json = Record<string, any>;
type ConfigResponse = {
  active_version: number;
  history: { version: number; ts: string; changed_by: string; reason: string }[];
  market_open: boolean;
  universe_all: { symbol: string; name: string; sector: string }[];
};
type Editing = { id: number | null; name: string; description: string; risk: Json; strategy: Json };

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

const fromProfile = (p: Profile): Editing => ({
  id: p.id,
  name: p.name,
  description: p.description,
  risk: p.risk,
  strategy: p.strategy,
});

export default function Settings() {
  const { status } = useLive();
  const qc = useQueryClient();
  const router = useRouter();
  const cfg = useQuery({ queryKey: ["config"], queryFn: () => api<ConfigResponse>("/api/config") });
  const versions = useQuery({ queryKey: ["profiles"], queryFn: () => api<ProfilesResponse>("/api/profiles"), refetchInterval: 30_000 });
  const [edit, setEdit] = useState<Editing | null>(null);
  const [dirty, setDirty] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [note, setNote] = useState("");
  const [token, setTokenInput] = useState("");

  useEffect(() => {
    if (versions.data && !edit) {
      const live = versions.data.profiles.find((p) => p.is_live) ?? versions.data.profiles[0];
      if (live) setEdit(fromProfile(live));
    }
  }, [versions.data, edit]);
  useEffect(() => setTokenInput(getToken()), []);

  if (!cfg.data || !versions.data || !edit) return <div className="text-sm text-muted">Loading settings…</div>;
  const marketOpen = versions.data.market_open;
  const selected = versions.data.profiles.find((p) => p.id === edit.id) ?? null;
  const isLive = !!selected?.is_live;
  const frozen = isLive && marketOpen;
  const draft = edit;
  const setDraft = (d: { risk: Json; strategy: Json }) => {
    setEdit({ ...edit, risk: d.risk, strategy: d.strategy });
    setDirty(true);
  };

  const num = (section: "risk" | "strategy", path: string, step = "any") => (
    <input
      type="number"
      step={step}
      className="w-full"
      value={String(get(draft[section], path) ?? "")}
      onChange={(e) => setDraft({ ...draft, [section]: set(draft[section], path, e.target.value === "" ? "" : Number(e.target.value)) })}
    />
  );
  const toggle = (section: "risk" | "strategy", path: string, label: string) => (
    <label className="flex items-center gap-2 text-[13px]">
      <input
        type="checkbox"
        checked={Boolean(get(draft[section], path))}
        onChange={(e) => setDraft({ ...draft, [section]: set(draft[section], path, e.target.checked) })}
      />
      {label}
    </label>
  );
  const watch: string[] = draft.strategy.universe.symbols ?? [];

  async function act(fn: () => Promise<unknown>, msg: string) {
    setErr(null);
    setNote("");
    try {
      await fn();
      setNote(msg);
      await qc.invalidateQueries();
    } catch (e) {
      setErr(e);
    }
  }
  const cur: Editing = edit;
  const body = () => ({ name: cur.name, description: cur.description, risk: cur.risk, strategy: cur.strategy });

  async function saveVersion() {
    await act(async () => {
      await put(`/api/profiles/${cur.id}`, body());
      setDirty(false);
    }, isLive ? "Saved. The live config was updated too." : "Saved.");
  }
  async function saveAsNew() {
    const name = window.prompt("Name for the new version", `${cur.name} copy`);
    if (!name) return;
    await act(async () => {
      const r = await post<{ id: number }>("/api/profiles", { ...body(), name });
      setEdit({ ...cur, id: r.id, name });
      setDirty(false);
    }, `Created "${name}".`);
  }
  async function makeLive() {
    if (!cur.id || !window.confirm(`Make "${cur.name}" the live paper-trading version?`)) return;
    await act(() => post(`/api/profiles/${cur.id}/activate`), `"${cur.name}" is now live.`);
  }
  async function remove() {
    if (!cur.id || !window.confirm(`Delete "${cur.name}"? Backtest history is kept.`)) return;
    await act(async () => {
      await del(`/api/profiles/${cur.id}`);
      setEdit(null);
    }, "Deleted.");
  }
  function backtest() {
    router.push(`/backtest?version=${cur.id}`);
  }

  return (
    <div className="space-y-5">
      <PageHeader
        title="Settings"
        sub="Save strategies as named versions, backtest any of them, and choose which one paper-trades live."
        action={
          <Badge tone={marketOpen ? "warn" : "pos"} dot>
            {marketOpen ? "Market open · live version frozen until 15:30" : "Market closed · all versions editable"}
          </Badge>
        }
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

      <Card title="Strategy versions" pad={false}>
        <ul className="divide-y divide-line">
          {versions.data.profiles.map((p) => (
            <li key={p.id}>
              <button
                onClick={() => {
                  if (dirty && !window.confirm("Discard unsaved changes?")) return;
                  setEdit(fromProfile(p));
                  setDirty(false);
                  setErr(null);
                  setNote("");
                }}
                className={`flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-left hover:bg-surface-2 ${p.id === edit.id ? "bg-surface-2" : ""}`}
              >
                <span className="text-[13px] font-semibold">{p.name}</span>
                {p.is_live && <Badge tone="pos" dot>Live</Badge>}
                {p.id === edit.id && <Badge tone="accent">Editing</Badge>}
                <span className="min-w-0 flex-1 truncate text-[12px] text-muted">{p.description}</span>
                <span className="text-[11px] text-muted">updated {when(p.updated_at)}</span>
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <Card
        title={
          <span className="flex items-center gap-2">
            Editing: {edit.name} {isLive && <Badge tone="pos" dot>Live</Badge>} {dirty && <Badge tone="warn">Unsaved</Badge>}
          </span>
        }
      >
        {frozen && (
          <div className="mb-4 rounded-lg border border-warn/30 bg-warn-soft px-3 py-2 text-[13px] text-warn">
            This is the live version and the market is open, so it can't be changed until 15:30 IST. You can still backtest
            it, or use “Save as new version” to experiment on a copy.
          </div>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Version name">
            <input className="w-full" disabled={frozen} value={edit.name} onChange={(e) => { setEdit({ ...edit, name: e.target.value }); setDirty(true); }} />
          </Field>
          <Field label="Description" hint="What's different about this version">
            <input className="w-full" disabled={frozen} value={edit.description} onChange={(e) => { setEdit({ ...edit, description: e.target.value }); setDirty(true); }} />
          </Field>
        </div>
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" disabled={frozen || !dirty || !edit.id} onClick={saveVersion}>Save</Button>
          <Button onClick={saveAsNew}>Save as new version</Button>
          <Button onClick={backtest} disabled={dirty} title={dirty ? "Save first, then backtest" : undefined}>Backtest this version</Button>
          <Button onClick={makeLive} disabled={isLive || marketOpen || dirty} title={marketOpen ? "Only outside market hours" : undefined}>
            Make live
          </Button>
          <Button variant="ghost" onClick={remove} disabled={isLive}>Delete</Button>
        </div>
        <div className="mt-3 space-y-2">
          <ErrorNote error={err} />
          {note && <div className="text-[13px] text-pos">{note}</div>}
          {marketOpen && !isLive && <div className="text-[12px] text-muted">“Make live” unlocks after 15:30 IST.</div>}
        </div>
      </Card>

      <fieldset disabled={frozen} className="space-y-5 disabled:opacity-60">
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

      <Card title="Live config history" pad={false}>
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
