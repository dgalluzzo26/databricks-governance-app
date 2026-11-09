import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { ApiProvider } from "@/api/ApiProvider";
import type { ApiServices } from "@/api/services";
import type { AppInfo, CurrentUser } from "@/api/types";
import { PreferencesProvider } from "@/app/preferences";
import { routes } from "@/app/routes";

export const testUser: CurrentUser = {
  userName: "ana.lopez@example.com",
  email: "ana.lopez@example.com",
  displayName: "Ana Lopez",
  userId: "1",
  groups: ["admins", "users"],
  isAdmin: true,
  workspaceHost: "https://example.cloud.databricks.com",
};

export const testAppInfo: AppInfo = {
  appName: "data-governance-app",
  version: "0.1.0",
  workspaceHost: "https://example.cloud.databricks.com",
  authMode: "service_principal",
  warehouseId: "abc123",
  appCatalog: "data_governance_app",
  appSchema: "app",
  adminGroup: "admins",
  adminSecretScope: "databricks-governance-app",
  adminSpConfigured: true,
  storage: { backend: "lakebase", healthy: true, detail: "governance.governance_app" },
};

export function fakeServices(overrides: { user?: CurrentUser | Error; appInfo?: AppInfo } = {}): ApiServices {
  const user = overrides.user ?? testUser;
  return {
    identity: { me: () => (user instanceof Error ? Promise.reject(user) : Promise.resolve(user)) },
    appInfo: { get: () => Promise.resolve(overrides.appInfo ?? testAppInfo) },
  } as unknown as ApiServices;
}

export function renderApp(path = "/", services: ApiServices = fakeServices()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  return render(
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <ApiProvider services={services}>
          <RouterProvider router={router} />
        </ApiProvider>
      </PreferencesProvider>
    </QueryClientProvider>,
  );
}
