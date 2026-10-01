"use client";

// Unsaved Settings edits, kept in this browser so they can be backtested
// before (or without) becoming the live config.
// eslint-disable-next-line @typescript-eslint/no-explicit-any -- validated server-side
export type Draft = { risk: Record<string, any>; strategy: Record<string, any> };
const KEY = "algotrade.settingsDraft";

export function saveDraft(d: Draft): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(d));
  } catch {
    /* storage unavailable */
  }
}

export function loadDraft(): Draft | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    return null;
  }
}

export function clearDraft(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
