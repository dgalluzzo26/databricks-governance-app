import type { RouteObject } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { AppStatusPage } from "@/pages/AppStatusPage";
import { HomePage } from "@/pages/HomePage";
import { IdentityPage } from "@/pages/identity/IdentityPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PoliciesPage } from "@/pages/policies/PoliciesPage";
import { PolicyDetailPage } from "@/pages/policies/PolicyDetailPage";
import { AccessExplorerPage } from "@/pages/accessExplorer/AccessExplorerPage";
import { MaskRulesPage } from "@/pages/maskRules/MaskRulesPage";
import { RowFiltersPage } from "@/pages/rowFilters/RowFiltersPage";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "access-explorer", element: <AccessExplorerPage /> },
      { path: "policies", element: <PoliciesPage /> },
      { path: "policies/:policyId", element: <PolicyDetailPage /> },
      { path: "identity", element: <IdentityPage /> },
      { path: "row-filters", element: <RowFiltersPage /> },
      { path: "mask-rules", element: <MaskRulesPage /> },
      { path: "status", element: <AppStatusPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
