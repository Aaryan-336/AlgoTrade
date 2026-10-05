"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Shell } from "@/components/shell";
import { LiveProvider } from "@/lib/live";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 3000 } },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <LiveProvider>
        <Shell>{children}</Shell>
      </LiveProvider>
    </QueryClientProvider>
  );
}
