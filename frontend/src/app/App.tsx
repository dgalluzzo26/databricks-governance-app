/**
 * @file Root application component: wires React Query, preferences, API auth, and the router.
 */
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ApiProvider } from "@/api/ApiProvider";
import { ApiError } from "@/api/httpClient";
import { queryLabel, recordError } from "./errorLog";
import { PreferencesProvider } from "./preferences";
import { routes } from "./routes";
import { ViewAsProvider } from "./viewAs";

/**
 * Builds a QueryClient with app-wide defaults.
 * @remarks Retries skip client errors (HTTP status under 500) and cap at two attempts. Final
 * query and mutation failures are recorded in the session error log for the Activity panel.
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if ((query.meta as { silent?: boolean } | undefined)?.silent) return;
        recordError(error, "query", queryLabel(query.queryKey));
      },
    }),
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        const meta = mutation.meta as { busyMessage?: string } | undefined;
        const label = meta?.busyMessage?.replace(/…$/, "") ?? queryLabel(mutation.options.mutationKey ?? ["Action"]);
        recordError(error, "mutation", label);
      },
    }),
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        // Retrying client errors (403, 404, 422) never helps.
        retry: (count, error) => !(error instanceof ApiError && error.status < 500) && count < 2,
      },
      mutations: {
        // Surfaced by BusyBar / BusyStatus while any mutation is in flight.
        meta: { busyMessage: "Working…" },
      },
    },
  });
}

/**
 * Top-level provider tree for the governance app.
 * @remarks QueryClient and the browser router are created once per mount via useState initializers.
 */
export function App() {
  const [queryClient] = useState(createQueryClient);
  const [router] = useState(() => createBrowserRouter(routes));

  return (
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <ApiProvider>
          <ViewAsProvider>
            <RouterProvider router={router} />
          </ViewAsProvider>
        </ApiProvider>
      </PreferencesProvider>
    </QueryClientProvider>
  );
}
