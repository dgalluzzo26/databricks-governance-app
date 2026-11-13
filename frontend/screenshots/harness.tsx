/**
 * @file Browser harness for Help guide screenshots (dev only, never built).
 *
 * Renders the real app routes with in-memory demo data. Open
 * `/screenshots/index.html?role=user|approver&path=/access-explorer` under `npm run dev`;
 * `capture.mjs` drives it with Playwright and writes `public/help-images/*.png`.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { ApiProvider } from "@/api/ApiProvider";
import type {
  AccessRequest,
  AnalyzeAttributeRequest,
  ApprovalInbox,
  AttributeAssignmentList,
  AttributeDefinition,
  CurrentUser,
  IdentityAttributeAssignment,
  SuggestedAttribute,
  TableAccessInsight,
} from "@/api/types";
import { PreferencesProvider } from "@/app/preferences";
import { routes } from "@/app/routes";
import { ViewAsProvider } from "@/app/viewAs";
import { fakeServices, testDefinitions } from "@/test/renderApp";
import "@/styles/global.css";

const params = new URLSearchParams(window.location.search);
const role = params.get("role") === "approver" ? "approver" : "user";
const path = params.get("path") ?? "/";

const users: Record<typeof role, CurrentUser> = {
  user: {
    userName: "jordan.lee@example.com",
    email: "jordan.lee@example.com",
    displayName: "Jordan Lee",
    userId: "101",
    groups: ["users", "sales-analysts"],
    isAdmin: false,
    isApprover: false,
    workspaceHost: "https://example.cloud.databricks.com",
  },
  approver: {
    userName: "priya.shah@example.com",
    email: "priya.shah@example.com",
    displayName: "Priya Shah",
    userId: "102",
    groups: ["users", "finance-owners"],
    isAdmin: false,
    isApprover: true,
    workspaceHost: "https://example.cloud.databricks.com",
  },
};
const user = users[role];

const created = "2026-11-02T15:30:00Z";

function assignment(
  id: string,
  attributeKey: string,
  attributeValue: string,
  expiresAt: string | null,
): IdentityAttributeAssignment {
  return {
    assignmentId: id,
    principalName: user.userName,
    principalType: "USER",
    attributeKey,
    attributeValue,
    source: "ACCESS_REQUEST",
    grantedBy: "priya.shah@example.com",
    justification: null,
    expiresAt,
    createdAt: created,
    revokedAt: null,
    revokedBy: null,
    active: true,
  };
}

const myAttributes: AttributeAssignmentList =
  role === "user"
    ? {
        assignments: [
          assignment("as-1", "data_access_control", "marketing", "2027-03-31T23:59:00Z"),
          assignment("as-2", "sensitivity", "sales.PII.Phone", "2027-01-31T23:59:00Z"),
          assignment("as-3", "state", "WA", null),
        ],
        total: 3,
      }
    : {
        assignments: [
          assignment("as-1", "data_access_control", "finance", null),
          assignment("as-2", "sensitivity", "finance.budget", null),
        ],
        total: 2,
      };

function request(
  id: string,
  requester: string,
  attributeKey: string,
  attributeValue: string,
  status: AccessRequest["status"],
  justification: string,
  approvers: AccessRequest["approvers"],
  category: string | null,
): AccessRequest {
  return {
    requestId: id,
    requesterName: requester,
    attributeKey,
    attributeValue,
    requestedExpiresAt: "2027-02-28T23:59:00Z",
    justification,
    status,
    decidedBy: status === "PENDING" ? null : "priya.shah@example.com",
    decidedAt: status === "PENDING" ? null : "2026-11-04T10:00:00Z",
    decisionNote: status === "DENIED" ? "Request WA only — that is your territory." : null,
    approvedExpiresAt: status === "APPROVED" ? "2027-02-28T23:59:00Z" : null,
    createdAt: created,
    resolvedCategory: category,
    routedTo: approvers.length ? "category_approvers" : "app_admins",
    approvers,
  };
}

const salesOwners = [{ principalName: "sales-data-owners", principalType: "GROUP" }];
const financeOwners = [{ principalName: "finance-owners", principalType: "GROUP" }];

const myRequests = {
  requests: [
    request("r1", user.userName, "sensitivity", "sales.PII.Name", "PENDING", "Q4 churn analysis on sales.crm.customers", salesOwners, "sales"),
    request("r2", user.userName, "data_access_control", "marketing", "APPROVED", "Campaign attribution dashboard", salesOwners, "marketing"),
    request("r3", user.userName, "state", "*", "DENIED", "Need all customers", [], null),
  ],
  total: 3,
};

const approvals: ApprovalInbox = {
  items: [
    {
      kind: "REQUEST",
      request: request("a1", "jordan.lee@example.com", "sensitivity", "finance.budget.PII.Name", "PENDING", "Budget owner report for FY27 planning", financeOwners, "finance"),
      assignment: null,
      resolvedCategory: "finance",
      attributeValue: "finance.budget.PII.Name",
      principalName: "jordan.lee@example.com",
      expiresAt: "2027-02-28T23:59:00Z",
    },
    {
      kind: "REQUEST",
      request: request("a2", "sam.rivera@example.com", "data_access_control", "finance.budget", "PENDING", "Monthly variance review", financeOwners, "finance"),
      assignment: null,
      resolvedCategory: "finance",
      attributeValue: "finance.budget",
      principalName: "sam.rivera@example.com",
      expiresAt: "2027-01-31T23:59:00Z",
    },
    {
      kind: "RENEWAL",
      request: null,
      assignment: {
        ...assignment("as-9", "sensitivity", "finance.budget", "2026-11-30T23:59:00Z"),
        principalName: "alex.kim@example.com",
      },
      resolvedCategory: "finance",
      attributeValue: "finance.budget",
      principalName: "alex.kim@example.com",
      expiresAt: "2027-05-31T23:59:00Z",
    },
  ],
  total: 3,
};

function suggestion(
  kind: SuggestedAttribute["kind"],
  attributeKey: string,
  attributeValue: string,
  columnNames: string[],
  approvers: SuggestedAttribute["approvers"],
  extra: Partial<SuggestedAttribute> = {},
): SuggestedAttribute {
  return {
    kind,
    attributeKey,
    attributeValue,
    reason: "",
    requestable: true,
    alreadyHeld: false,
    requestPath: null,
    columnNames,
    coversPaths: [attributeValue],
    isHierarchyParent: false,
    routedTo: approvers.length ? "category_approvers" : "app_admins",
    resolvedCategory: approvers.length ? "sales" : null,
    approvers,
    ...extra,
  };
}

function column(
  columnName: string,
  dataType: string,
  accessState: TableAccessInsight["columns"][number]["accessState"],
  requiredSensitivityPath: string | null,
  maskRuleKey: string | null,
  notes: string | null,
): TableAccessInsight["columns"][number] {
  const governed = accessState !== "not_governed";
  return {
    columnName,
    dataType,
    tags: {
      sensitivityCategory: requiredSensitivityPath?.split(".").slice(0, -2).join(".") || null,
      sensitivity: requiredSensitivityPath?.split(".").slice(-2).join(".") || null,
      maskRule: maskRuleKey,
      dataType: null,
      rowFilter: null,
    },
    accessState,
    requiredSensitivityPath,
    userHasAccess: accessState === "unmasked" || accessState === "not_governed",
    maskRuleKey,
    maskHandler: maskRuleKey,
    maskKind: governed ? "builtin" : null,
    maskDescription: null,
    policyName: governed ? "mask_sensitivity_variant" : null,
    notes,
  };
}

const states = ["CA", "IL", "NY", "OR", "TX", "WA"];

const insight: TableAccessInsight = {
  tableFullName: "sales.crm.customers",
  catalogName: "sales",
  schemaName: "crm",
  tableName: "customers",
  tableType: "MANAGED",
  summary:
    "7 columns; 1 unmasked governed column; 2 still masked; row filter on state shows WA rows only. Table access needs data_access_control=sales.crm.",
  columns: [
    column("customer_id", "bigint", "not_governed", null, null, null),
    column("full_name", "string", "masked", "sales.PII.Name", "full", "Shown as ***** until you hold sales.PII.Name."),
    column("email", "string", "masked", "sales.PII.Email", "email_hash", "Hashed; joins still work."),
    column("phone", "string", "unmasked", "sales.PII.Phone", "full", "You hold sales.PII.Phone."),
    column("state", "string", "not_governed", null, null, "Row filter column."),
    column("lifetime_value", "decimal(12,2)", "not_governed", null, null, null),
    column("created_at", "timestamp", "not_governed", null, null, null),
  ],
  rowFilter: {
    attached: true,
    dimensionKey: "state",
    columnName: "state",
    policyName: "rf_by_dimension",
    functionName: "data_governance_app.masking_functions.has_row_access",
    userHasWildcard: false,
    userValues: ["WA"],
    userCanSeeRows: true,
    notes: "You see rows where state = WA.",
  },
  suggestions: [
    suggestion("data_access", "data_access_control", "sales.crm", [], salesOwners),
    suggestion("sensitivity", "sensitivity", "sales.PII.Name", ["full_name"], salesOwners),
    suggestion("sensitivity", "sensitivity", "sales.PII.Email", ["email"], salesOwners),
    suggestion("sensitivity", "sensitivity", "sales.PII.Phone", ["phone"], salesOwners, {
      requestable: false,
      alreadyHeld: true,
    }),
    suggestion("row_filter", "state", "*", ["state"], []),
    ...states
      .filter((value) => value !== "WA")
      .map((value) => suggestion("row_filter", "state", value, ["state"], [])),
  ],
};

const definitions = {
  definitions: [
    ...testDefinitions.definitions.filter((item) => item.key !== "state"),
    {
      ...(testDefinitions.definitions.find((item) => item.key === "state") as AttributeDefinition),
      allowedValues: ["*", ...states],
    },
  ],
  total: testDefinitions.total,
};

const base = fakeServices({
  user,
  myAttributes,
  assignments: myAttributes,
  myAccessRequests: myRequests,
  approvals,
  definitions,
});

const tree: Record<string, Record<string, string[]>> = {
  finance: { budget: ["forecasts", "actuals"], ledger: ["journal_entries"] },
  sales: { crm: ["customers", "opportunities", "accounts_view"], marketing: ["campaigns"] },
};

const services = {
  ...base,
  help: {
    ...base.help,
    // capture.cjs serves docs/*.md under /__docs/ (Vite cannot read outside frontend/).
    doc: async (slug: string) => {
      const response = await fetch(`/__docs/${slug}-guide.md`).catch(() => null);
      const isMarkdown = response?.ok && !response.headers.get("content-type")?.includes("html");
      return isMarkdown ? response.text() : base.help.doc(slug);
    },
  },
  accessExplorer: {
    ...base.accessExplorer,
    analyze: async () => insight,
    search: async (q: string) => {
      const hits = Object.entries(tree).flatMap(([catalogName, schemas]) =>
        Object.entries(schemas).flatMap(([schemaName, tables]) =>
          tables.map((tableName) => ({ catalogName, schemaName, tableName })),
        ),
      );
      const matches = hits.filter((hit) =>
        `${hit.catalogName}.${hit.schemaName}.${hit.tableName}`.includes(q.toLowerCase()),
      );
      return { tables: matches, total: matches.length };
    },
    catalogs: async () => ({
      catalogs: Object.keys(tree).map((catalogName) => ({ catalogName })),
      total: Object.keys(tree).length,
      truncated: false,
    }),
    schemas: async (catalog: string) => {
      const schemas = Object.keys(tree[catalog] ?? {});
      return {
        schemas: schemas.map((schemaName) => ({ catalogName: catalog, schemaName })),
        total: schemas.length,
        truncated: false,
      };
    },
    tables: async (catalog: string, schema: string) => {
      const tables = tree[catalog]?.[schema] ?? [];
      return {
        tables: tables.map((tableName) => ({
          catalogName: catalog,
          schemaName: schema,
          tableName,
          tableType: tableName.endsWith("_view") ? "VIEW" : "MANAGED",
        })),
        total: tables.length,
        truncated: false,
      };
    },
  },
  attributeExplorer: {
    analyze: async (body: AnalyzeAttributeRequest) => {
      if (!body.attributeValue) {
        return {
          attributeKey: body.attributeKey,
          attributeValue: "*",
          kind: "row_filter" as const,
          groupName: null,
          parentGroup: null,
          holders: [],
          principals: [
            { principalName: "jordan.lee@example.com", principalType: "USER" as const, values: ["WA"], expiresAt: null },
            { principalName: "sam.rivera@example.com", principalType: "USER" as const, values: ["CA", "OR"], expiresAt: "2027-01-31T23:59:00Z" },
            { principalName: "finance-analysts", principalType: "GROUP" as const, values: ["*"], expiresAt: null },
          ],
          tables: [],
          note: null,
        };
      }
      return {
        attributeKey: body.attributeKey,
        attributeValue: body.attributeValue,
        kind: "sensitivity" as const,
        groupName: null,
        parentGroup: null,
        holders: [
          { principalName: "alex.kim@example.com", principalType: "USER" as const, attributeValue: body.attributeValue, match: "exact" as const, expiresAt: "2026-11-30T23:59:00Z", assignmentId: "h1" },
          { principalName: "finance-analysts", principalType: "GROUP" as const, attributeValue: `${body.attributeValue}.PII`, match: "descendant" as const, expiresAt: null, assignmentId: "h2" },
          { principalName: "sam.rivera@example.com", principalType: "USER" as const, attributeValue: `${body.attributeValue}.PII.Name`, match: "descendant" as const, expiresAt: "2027-01-31T23:59:00Z", assignmentId: "h3" },
        ],
        principals: [],
        tables: [],
        note: null,
      };
    },
  },
};

localStorage.setItem("dga.theme", "light");
sessionStorage.clear();

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
const router = createMemoryRouter(routes, { initialEntries: [path] });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <PreferencesProvider>
        <ApiProvider services={services}>
          <ViewAsProvider>
            <RouterProvider router={router} />
          </ViewAsProvider>
        </ApiProvider>
      </PreferencesProvider>
    </QueryClientProvider>
  </StrictMode>,
);
