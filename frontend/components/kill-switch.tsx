"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { post } from "@/lib/api";
import { useLive } from "@/lib/live";
import { Button, ErrorNote } from "./ui";

export function KillSwitchButton() {
  const { status } = useLive();
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [action, setAction] = useState<"block" | "flatten">("block");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const active = status?.kill_switch.active;

  async function submit() {
    setBusy(true);
    setErr(null);
    try {
      if (active) await post("/api/kill-switch/release", { note: reason });
      else await post("/api/kill-switch", { action, reason });
      await qc.invalidateQueries();
      setOpen(false);
      setReason("");
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Button variant={active ? "secondary" : "danger"} onClick={() => setOpen(true)}>
        {active ? "Kill switch on" : "Kill switch"}
      </Button>
      {open && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={() => setOpen(false)}>
          <div className="card w-full max-w-md p-5" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-[15px] font-semibold">{active ? "Release kill switch" : "Activate kill switch"}</h3>
            <p className="mt-1 text-[13px] text-ink-2">
              {active
                ? "New entries resume only after you release it. Write down what you checked."
                : "Stops all new entries immediately. Stop-losses keep protecting open positions."}
            </p>
            {!active && (
              <div className="mt-4 grid grid-cols-2 gap-2">
                {(["block", "flatten"] as const).map((a) => (
                  <button
                    key={a}
                    onClick={() => setAction(a)}
                    className={`rounded-lg border px-3 py-2 text-left text-[13px] ${action === a ? "border-ink bg-surface-2" : "border-line"}`}
                  >
                    <div className="font-medium">{a === "block" ? "Block entries" : "Block and sell all"}</div>
                    <div className="text-[11px] text-muted">{a === "block" ? "Keep positions" : "Flatten every position"}</div>
                  </button>
                ))}
              </div>
            )}
            <label className="mt-4 block">
              <span className="label">{active ? "Review note" : "Reason"}</span>
              <textarea className="mt-1 w-full" rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="mt-3">
              <ErrorNote error={err} />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button variant={active ? "primary" : "danger"} disabled={busy || reason.trim().length < 3} onClick={submit}>
                {active ? "Release" : "Activate"}
              </Button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
