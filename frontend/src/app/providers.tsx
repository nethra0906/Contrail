"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 4_000,
            // No global refetchInterval: the live map gets its updates from
            // the WS feed (see ws-client.ts), not polling, and the remaining
            // useQuery calls (scorecard, aircraft track) don't need
            // auto-refetching beyond React Query's default (refetch on
            // window focus). Add refetchInterval on a specific query if it
            // ever needs one.
            retry: 1,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
