const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2, minimumFractionDigits: 2 });
const inr0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export function money(v: number | null | undefined, digits: 0 | 2 = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = (digits === 0 ? inr0 : inr).format(Math.abs(v));
  return `${v < 0 ? "−" : ""}₹${s}`;
}

export function signedMoney(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v > 0 ? "+" : ""}${money(v)}`;
}

export function pct(v: number | null | undefined, digits = 2, signed = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = `${Math.abs(v).toFixed(digits)}%`;
  if (v < 0) return `−${s}`;
  return signed && v > 0 ? `+${s}` : s;
}

export function tone(v: number | null | undefined): string {
  if (!v) return "text-ink-2";
  return v > 0 ? "text-pos" : "text-neg";
}

const dt = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const d = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric" });

export const when = (iso: string | null | undefined) => (iso ? dt.format(new Date(iso)) : "—");
export const day = (iso: string | null | undefined) => (iso ? d.format(new Date(iso)) : "—");

export function ago(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "never";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

export function humanize(s: string): string {
  return s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}
