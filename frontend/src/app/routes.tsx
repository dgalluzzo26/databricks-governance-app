/**
 * @file React Router route table for the app shell.
 *
 * Public routes (Home, Access explorer, Identity, Help) are available to all signed-in users.
 * Approvals is gated by {@link RequireApprover} (admins and category approvers).
 * Admin-only routes (Policies, Exceptions, Row filters, Mask rules, Data access, App status) are
 * wrapped in {@link RequireAdmin}.
 */
import type { RouteObject } from "react-router-dom";
import { RequireAdmin } from "@/components/auth/RequireAdmin";
import { RequireApprover } from "@/components/auth/RequireApprover";
import { AppShell } from "@/components/layout/AppShell";
import { AppStatusPage } from "@/pages/AppStatusPage";
import { HomePage } from "@/pages/HomePage";
import { ApprovalsPage } from "@/pages/approvals/ApprovalsPage";
import { AttributeExplorerPage } from "@/pages/attributeExplorer/AttributeExplorerPage";
import { IdentityPage } from "@/pages/identity/IdentityPage";
import { NotFoundPage } from "@/pages/NotFoundPage";
import { PoliciesPage } from "@/pages/policies/PoliciesPage";
import { PolicyDetailPage } from "@/pages/policies/PolicyDetailPage";
import { AccessExplorerPage } from "@/pages/accessExplorer/AccessExplorerPage";
import { PolicyExceptionsPage } from "@/pages/exceptions/PolicyExceptionsPage";
import { HelpPage } from "@/pages/help/HelpPage";
import { DataAccessPage } from "@/pages/dataAccess/DataAccessPage";
import { MaskRulesPage } from "@/pages/maskRules/MaskRulesPage";
import { RowFiltersPage } from "@/pages/rowFilters/RowFiltersPage";

/** Declarative route config mounted under {@link AppShell}. */
export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "access-explorer", element: <AccessExplorerPage /> },
      {
        path: "attribute-explorer",
        element: (
          <RequireApprover>
            <AttributeExplorerPage />
          </RequireApprover>
        ),
      },
      { path: "identity", element: <IdentityPage /> },
      {
        path: "approvals",
        element: (
          <RequireApprover>
            <ApprovalsPage />
          </RequireApprover>
        ),
      },
      { path: "help", element: <HelpPage /> },
      {
        path: "policies",
        element: (
          <RequireAdmin>
            <PoliciesPage />
          </RequireAdmin>
        ),
      },
      {
        path: "policies/:policyId",
        element: (
          <RequireAdmin>
            <PolicyDetailPage />
          </RequireAdmin>
        ),
      },
      {
        path: "exceptions",
        element: (
          <RequireAdmin>
            <PolicyExceptionsPage />
          </RequireAdmin>
        ),
      },
      {
        path: "row-filters",
        element: (
          <RequireAdmin>
            <RowFiltersPage />
          </RequireAdmin>
        ),
      },
      {
        path: "mask-rules",
        element: (
          <RequireAdmin>
            <MaskRulesPage />
          </RequireAdmin>
        ),
      },
      {
        path: "data-access",
        element: (
          <RequireAdmin>
            <DataAccessPage />
          </RequireAdmin>
        ),
      },
      {
        path: "status",
        element: (
          <RequireAdmin>
            <AppStatusPage />
          </RequireAdmin>
        ),
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];
