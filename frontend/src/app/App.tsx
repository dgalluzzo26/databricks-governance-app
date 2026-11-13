/**
 * @file Root application component: wires React Query, preferences, API auth, and the router.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { ApiProvider } from "@/api/ApiProvider";
import { ApiError } from "@/api/httpClient";
import { PreferencesProvider } from "./preferences";
import { routes } from "./routes";
import { ViewAsProvider } from "./viewAs";

/**
 * Builds a QueryClient with app-wide defaults.
 * @remarks Retries skip client errors (HTTP status under 500) and cap at two attempts.
 */
function createQueryClient(): QueryClient {
  return new QueryClient({
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
