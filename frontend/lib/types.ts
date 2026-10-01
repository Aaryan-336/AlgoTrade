export type Intent = {
  decision_id: string;
  symbol: string;
  side: "BUY" | "SELL";
  kind: "ENTRY" | "EXIT";
  strategy: string;
  reason: string;
  ref_price: string;
  expires_at: string;
  stop: string | null;
  target: string | null;
  score: number;
};

export type Breaker = { reason: string; tripped_at: string; severity: "block_entries" | "halt" };

export type Status = {
  mode: string;
  config_version: number;
  market_open: boolean;
  equity: number;
  cash: number;
  starting_capital: number;
  day_pnl: number;
  total_pnl: number;
  realized: number;
  fees: number;
  drawdown_pct: number;
  kill_switch: { active: boolean; action: string; reason: string; ts: string | null };
  breakers: Record<string, Breaker>;
  halted: boolean;
  entries_blocked: boolean;
  pending_intents: Intent[];
  last_cycle: string | null;
  last_bar_ts: string | null;
  regime: {
    enabled?: boolean;
    entries_blocked?: boolean;
    reason?: string;
    index_close?: number;
    index_ema?: number;
    vix?: number | null;
    size_multiplier?: number;
  };
  timeframe: string;
  now: string;
  now_ist: string;
  provider: { name: string; connected: boolean; last_update: string | null; message: string; ready: boolean };
  upstox: { configured: boolean; logged_in: boolean; since: string | null };
  groq: { configured: boolean; model: string; last_error: string };
  news: { last_run: string | null; counts: Record<string, number>; errors: string[] };
  runner: { running: boolean; last_error: string; last_tick_count: number; history_source?: string };
  heartbeat: { ts: string; running: boolean } | null;
  telegram: boolean;
  demo: boolean;
  alerts: { ts: string; level: "info" | "warning" | "critical"; text: string }[];
};

export type PositionRow = {
  symbol: string;
  qty: number;
  avg_price: number;
  ltp: number;
  value: number;
  pnl: number;
  pnl_pct: number;
  weight_pct: number;
  stop: number | null;
  target: number | null;
  strategy: string;
  sector: string;
  opened_at: string;
  stop_live: boolean;
  sentiment: number | null;
};

export type Portfolio = {
  equity: number;
  cash: number;
  invested: number;
  unrealized: number;
  realized: number;
  fees: number;
  positions: PositionRow[];
  sectors: Record<string, { value: number; pct: number }>;
  limits: {
    max_position_pct: number;
    max_sector_pct: number;
    max_open_positions: number;
    max_daily_loss_pct: number;
    max_drawdown_pct: number;
  };
};

export type OrderRow = {
  id: string;
  symbol: string;
  side: "BUY" | "SELL";
  qty: number;
  order_type: string;
  state: string;
  purpose: string;
  trigger_price: number | null;
  avg_fill_price: number | null;
  filled_qty: number;
  reason: string;
  strategy: string | null;
  created_at: string;
};

export type Decision = {
  id: string;
  ts: string;
  symbol: string;
  kind: string;
  score: number;
  components: {
    trend?: number;
    momentum?: number;
    volume?: number;
    sentiment?: number;
    signal?: { strategy: string; strength: number; reasons: string[] };
    rotation_to?: string;
  };
  outcome: string;
  reason: string;
};

export type RiskEvent = {
  id: number;
  ts: string;
  symbol: string | null;
  check: string;
  result: string;
  details: Record<string, unknown>;
};

export type NewsItem = {
  id: string;
  publisher: string;
  url: string;
  headline: string;
  published_at: string;
  symbols: string[];
  credibility: number;
  scored: boolean;
  sentiment: {
    symbol: string;
    score: number;
    confidence: number;
    event_type: string;
    is_material: boolean;
    summary: string;
  }[];
};

export type UniverseRow = {
  symbol: string;
  name: string;
  sector: string;
  ltp: number | null;
  change_pct: number | null;
  held: boolean;
  sentiment: number | null;
  news_count: number;
  negative_material: boolean;
};

export type Trade = {
  symbol: string;
  strategy: string;
  opened_at?: string;
  closed_at?: string;
  opened?: string;
  closed?: string;
  qty: number;
  entry: number;
  exit: number;
  pnl: number;
  fees: number;
};

export type BacktestResult = {
  id: number;
  params: {
    symbols: string[];
    start: string;
    end: string;
    capital: number;
    failed?: string[];
    config_version?: number | string;
  };
  metrics: Record<string, number | string | null | string[]>;
  equity_curve: { date: string; equity: number }[];
  trades: Trade[];
  rejections?: Record<string, number>;
};
