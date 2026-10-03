"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { API_URL, setToken } from "@/lib/api";
import { Button } from "./ui";

/** Shown when the API rejects us (API_TOKEN set) or cannot be reached. */
export function TokenGate({ error }: { error?: string }) {
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  if (!error) return null;
  const unauthorized = /invalid token|401/i.test(error);
  return (
    <div className="border-b border-line bg-surface-2">
      <div className="mx-auto flex max-w-[1400px] flex-col gap-2 px-4 py-2.5 text-[13px] sm:flex-row sm:items-center">
        <span className="text-ink-2">
          {unauthorized ? "The API needs your access token." : `Cannot reach the API at ${API_URL}. Is the backend running?`}
        </span>
        {unauthorized && (
          <form
            className="flex gap-2 sm:ml-auto"
            onSubmit={(e) => {
              e.preventDefault();
              setToken(value.trim());
              qc.invalidateQueries();
            }}
          >
            <input type="password" placeholder="API token" value={value} onChange={(e) => setValue(e.target.value)} />
            <Button variant="primary" type="submit">
              Save
            </Button>
          </form>
        )}
      </div>
    </div>
  );
}
