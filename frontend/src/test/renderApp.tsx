import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { ApiProvider } from "@/api/ApiProvider";
import type { ApiServices } from "@/api/services";
import type {
  AccessRequestList,
  RecoveryGitSettings,
  RecoveryPreview,
  AppInfo,
  ApprovalInbox,
  AssignAttributeRequest,
  AttachRowFilterRequest,
  AttributeAssignmentList,
  AttributeDefinitionList,
  CategoryApproverList,
  CreateAccessRequestBody,
  CurrentUser,
  DecideAccessRequestBody,
  GovernedTagList,
  IdentityAttributeAssignment,
  PolicyDetail,
  PolicyListResponse,
  MaskRuleList,
  MaskRulePackStatus,
  RowFilterAttachmentList,
  RowFilterDimensionList,
  RowFilterPackStatus,
  UpsertAttributeDefinitionRequest,
  UpsertCategoryApproverRequest,
  UpsertMaskRuleRequest,
  UpsertPolicyExceptionRequest,
  UpsertRowFilterDimensionRequest,
  MaskHandlerValidateRequest,
  TableAccessInsight,
  DataAccessReadiness,
  DataAccessRun,
  DataAccessSettings,
  DataAccessSettingsView,
  DirectoryPrincipalList,
} from "@/api/types";
import { PreferencesProvider } from "@/app/preferences";
import { routes } from "@/app/routes";

export const testUser: CurrentUser = {
  userName: "ana.lopez@example.com",
  email: "ana.lopez@example.com",
  displayName: "Ana Lopez",
  userId: "1",
  groups: ["admins", "users"],
  isAdmin: true,
  isApprover: true,
  workspaceHost: "https://example.cloud.databricks.com",
};

export const testRecoverySettings: RecoveryGitSettings = {
  repoUrl: "",
  branch: "main",
  pathPrefix: "governance-export",
  username: "",
  tokenSecretKey: "git-token",
};

export const testRecoveryPreview: RecoveryPreview = {
  generatedAt: "2026-11-11T23:00:00Z",
  generatedBy: "admin@example.com",
  target: "",
  git: testRecoverySettings,
  files: [
    { path: "manifest.yaml", records: 0, content: "version: 1\n" },
    {
      path: "masking/config.yaml",
      records: 0,
      content: "joinable_salt:\n  configured: true\n  value: <redacted>\n",
    },
  ],
  warnings: ["The joinable salt is not exported (value redacted)."],
};

export const testDataAccessSettings: DataAccessSettingsView = {
  settings: {
    accessTagKey: "data_access_control",
    reviewTagKey: "clr_review_status",
    reviewTagValue: "reviewed",
    groupPrefix: "dac_",
    hierarchySeparator: ".",
    enrollmentCatalogTagKey: "data_access_controlled",
    enrollmentCatalogTagValue: "true",
    allowedCatalogs: [],
    dataPrivileges: ["SELECT"],
    intervalMinutes: 15,
    scheduleEnabled: true,
    scheduleMode: "dry_run",
    conflictPolicy: "hold",
    dryRun: true,
  },
  minIntervalMinutes: 5,
  applyAvailable: true,
  enrolledCatalogs: ["data_access_control", "dgalluzzo_sandbox_7474655957102538"],
};

export const testDataAccessReadiness: DataAccessReadiness = {
  dryRunReady: true,
  applyReady: true,
  checks: [
    { id: "credentials", label: "Account Admin SP credentials", ok: true, requiredFor: "dry_run", detail: "Present" },
    { id: "warehouse", label: "SQL warehouse", ok: true, requiredFor: "dry_run", detail: "Configured" },
  ],
  setupReady: true,
  setupChecks: [
    {
      id: "sensitivity_definition",
      label: "Sensitivity definition",
      ok: true,
      requiredFor: "setup",
      detail: "Configured as 'sensitivity'",
    },
    {
      id: "data_access_definition",
      label: "Data-access definition",
      ok: true,
      requiredFor: "setup",
      detail: "Configured as 'data_access' → tag 'data_access_control'",
    },
  ],
};

export const testDataAccessRun: DataAccessRun = {
  runId: "run-1",
  status: "success",
  trigger: "user",
  mode: "dry_run",
  startedAt: "2026-11-11T18:00:00Z",
  finishedAt: "2026-11-11T18:00:02Z",
  triggeredBy: "ana.lopez@example.com",
  error: null,
  fingerprint: "abc123",
  durationSeconds: 2,
  summary: {
    eligibleObjects: 1,
    skippedUnreviewed: 0,
    uniqueValues: 1,
    groupsToCreate: 1,
    groupsExisting: 0,
    grantsToAdd: 3,
    grantsToRevoke: 0,
    grantsUnchanged: 0,
    membersToAdd: 1,
    membersToRemove: 0,
    conflicts: 0,
    invalidValues: 0,
  },
  plan: {
    fingerprint: "abc123",
    truncated: false,
    driftSinceLastRun: ["No previous successful dry-run to compare"],
    invalidValues: [],
    applyErrors: [],
    membershipChanges: [
      {
        group: "dac_finance_budget",
        principalName: "ana.lopez@example.com",
        principalType: "USER",
        action: "add",
        reason: "Identity assignment finance.budget",
      },
    ],
    summary: {
      eligibleObjects: 1,
      skippedUnreviewed: 0,
      uniqueValues: 1,
      groupsToCreate: 1,
      groupsExisting: 0,
      grantsToAdd: 3,
      grantsToRevoke: 0,
      grantsUnchanged: 0,
      membersToAdd: 1,
      membersToRemove: 0,
      conflicts: 0,
      invalidValues: 0,
    },
    groups: [{ name: "dac_finance_budget", value: "finance.budget", parentGroup: null, exists: false }],
    grantsToAdd: [
      {
        securableType: "table",
        fullName: "sandbox.finance.budget",
        principal: "dac_finance_budget",
        privilege: "SELECT",
        reason: "tagged table",
      },
    ],
    grantsToRevoke: [],
    conflicts: [],
  },
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

export const testPolicies: PolicyListResponse = {
  metastoreName: "metastore_aws_us_east_1",
  metastore: [
    {
      id: "meta-1",
      name: "metastore_default_mask",
      policyType: "COLUMN_MASK",
      scopeKind: "metastore",
      onSecurableType: "METASTORE",
      onSecurableFullname: "metastore_aws_us_east_1",
      catalogName: null,
      forSecurableType: "TABLE",
      toPrincipals: ["account users"],
      exceptPrincipals: [],
      whenCondition: "has_tag('dga_sensitivity')",
      governedTags: [{ key: "dga_sensitivity", value: null }],
      privileges: [],
      createdBy: "admin",
    },
  ],
  catalog: [
    {
      id: "cat-1",
      name: "approved_models",
      policyType: "GRANT",
      scopeKind: "catalog",
      onSecurableType: "SCHEMA",
      onSecurableFullname: "system.ai",
      catalogName: "system",
      forSecurableType: "MODEL",
      toPrincipals: ["account users"],
      exceptPrincipals: [],
      whenCondition: "has_tag('ai.model_family')",
      governedTags: [{ key: "ai.model_family", value: null }],
      privileges: ["EXECUTE"],
      createdBy: "admin",
    },
  ],
  total: 2,
};

const sampleCatalogPolicy = testPolicies.catalog[0]!;

export const testPolicyDetail: PolicyDetail = {
  ...sampleCatalogPolicy,
  comment: "Only approved model families",
  matchColumns: [],
  columnMask: null,
  rowFilter: null,
  grant: { privileges: ["EXECUTE"] },
  deny: null,
  createdAt: 1,
  updatedAt: 2,
  updatedBy: "admin",
  affectedAssets: [],
  affectedAssetsTruncated: false,
  affectedAssetsNote:
    "Affected-asset discovery for MODEL is not available yet; showing principals, tags, and conditions only.",
};

export const testGovernedTags: GovernedTagList = {
  tags: [
    {
      tagKey: "dga_domain",
      description: "Business domain",
      values: ["finance", "finance.budget", "hr"],
      valueCount: 3,
    },
    {
      tagKey: "sub_domain",
      description: "Subdomain hierarchy",
      values: ["finance.contracts"],
      valueCount: 1,
    },
    {
      tagKey: "sensitivity_category",
      description: "Business sensitivity category",
      values: ["public", "finance", "finance.budget"],
      valueCount: 3,
    },
    {
      tagKey: "sensitivity",
      description: "Shared PII hierarchy",
      values: ["PII", "PII.Name"],
      valueCount: 2,
    },
    {
      tagKey: "data_access_control",
      description: "Tagged-level data access values",
      values: ["finance", "finance.budget", "finance.contracts"],
      valueCount: 3,
    },
  ],
  total: 5,
  truncated: false,
};

export const testDefinitions: AttributeDefinitionList = {
  definitions: [
    {
      key: "dga_domain",
      description: "Business domain",
      governedTagKeys: ["dga_domain"],
      valueSource: "governed_tags",
      valueSql: null,
      allowedValues: ["finance", "finance.budget", "hr"],
      createdBy: "admin",
      createdAt: "2026-01-01T00:00:00Z",
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
    {
      key: "sensitivity",
      description: "used to control access to masking",
      governedTagKeys: ["sensitivity_category", "sensitivity"],
      valueSource: "governed_tags",
      valueSql: null,
      allowedValues: [
        "public",
        "finance",
        "finance.budget",
        "finance.budget.PII",
        "public.PII.Name",
        "finance.budget.PII.Name",
      ],
      createdBy: "admin",
      createdAt: "2026-01-01T00:00:00Z",
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
    {
      key: "state",
      description: "Row filter dimension: US state abbreviation",
      governedTagKeys: [],
      valueSource: "sql",
      valueSql: "SELECT DISTINCT abbreviation FROM cat.sch.us_states",
      allowedValues: ["*", "CA", "NY", "TX"],
      createdBy: "admin",
      createdAt: "2026-01-01T00:00:00Z",
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
  ],
  total: 3,
};

export const testRowFilterDimensions: RowFilterDimensionList = {
  dimensions: [
    {
      key: "state",
      description: "Row filter dimension: US state abbreviation",
      valueSource: "sql",
      valueSql: "SELECT DISTINCT abbreviation FROM cat.sch.us_states",
      allowedValueCount: 3,
      sampleValues: ["CA", "NY", "TX"],
      attachmentCount: 1,
    },
  ],
  total: 1,
};

export const testRowFilterPack: RowFilterPackStatus = {
  tagKey: "row_filter",
  tagValues: ["geo_region", "state", "region"],
  functionName: "`data_governance_app`.`masking_functions`.`has_row_access`",
  policyName: "rf_by_dimension",
  policyExists: true,
  policyMatchColumns: "has_tag_value('row_filter', 'state') AS c",
  policyExceptPrincipals: ["app-sp"],
  dimensions: ["state"],
  missingTagValues: [],
  inSync: true,
};

export const testRowFilterAttachments: RowFilterAttachmentList = {
  attachments: [{ tableFullName: "cat.sch.us_states", columnName: "abbreviation", dimension: "state" }],
  total: 1,
};

export const testTableAccessInsight: TableAccessInsight = {
  tableFullName: "demo.default.customers",
  catalogName: "demo",
  schemaName: "default",
  tableName: "customers",
  summary: "3 columns; 0 unmasked governed columns; 2 still masked; row filter state hides all rows",
  columns: [
    {
      columnName: "s_name",
      dataType: "string",
      tags: {
        sensitivityCategory: "finance.budget",
        sensitivity: "PII.Name",
        maskRule: "full",
        dataType: "string",
        rowFilter: null,
      },
      accessState: "masked",
      requiredSensitivityPath: "finance.budget.PII.Name",
      userHasAccess: false,
      maskRuleKey: "full",
      maskHandler: "full",
      maskKind: "builtin",
      maskDescription: "Full redact",
      policyName: "mask_sensitivity_variant",
      notes: "Denied presentation via apply_mask_rule handler 'full'.",
    },
    {
      columnName: "id",
      dataType: "int",
      tags: {
        sensitivityCategory: null,
        sensitivity: null,
        maskRule: null,
        dataType: null,
        rowFilter: null,
      },
      accessState: "not_governed",
      requiredSensitivityPath: null,
      userHasAccess: true,
      maskRuleKey: null,
      maskHandler: null,
      maskKind: null,
      maskDescription: null,
      policyName: null,
      notes: null,
    },
  ],
  rowFilter: {
    attached: true,
    dimensionKey: "state",
    columnName: "s_open",
    policyName: "rf_by_dimension",
    functionName: "data_governance_app.masking_functions.has_row_access",
    userHasWildcard: false,
    userValues: [],
    userCanSeeRows: false,
    notes: "No 'state' attribute — has_row_access denies every row.",
  },
  suggestions: [
    {
      kind: "sensitivity",
      attributeKey: "sensitivity",
      attributeValue: "finance.budget.PII.Name",
      reason: "Unmask 1 column(s) that require sensitivity path finance.budget.PII.Name. App admins will review (no category approvers configured).",
      requestable: true,
      requestPath: "/identity?tab=request&value=finance.budget.PII.Name",
      columnNames: ["s_name"],
      coversPaths: ["finance.budget.PII.Name"],
      isHierarchyParent: false,
      routedTo: "app_admins",
      resolvedCategory: null,
      approvers: [],
    },
    {
      kind: "row_filter",
      attributeKey: "state",
      attributeValue: "*",
      reason: "Row filter uses state. Request * to see all rows. App admins will review.",
      requestable: true,
      requestPath: null,
      columnNames: ["s_open"],
      coversPaths: ["*"],
      isHierarchyParent: false,
      routedTo: "app_admins",
      resolvedCategory: null,
      approvers: [],
    },
    {
      kind: "row_filter",
      attributeKey: "state",
      attributeValue: "CA",
      reason: "Row filter on column s_open: request state=CA to see matching rows.",
      requestable: true,
      requestPath: null,
      columnNames: ["s_open"],
      coversPaths: ["CA"],
      isHierarchyParent: false,
      routedTo: "app_admins",
      resolvedCategory: null,
      approvers: [],
    },
  ],
};

export const testMyAttributes: AttributeAssignmentList = {
  assignments: [
    {
      assignmentId: "a1",
      principalName: "ana.lopez@example.com",
      principalType: "USER",
      attributeKey: "dga_domain",
      attributeValue: "finance.budget",
      source: "MANUAL",
      grantedBy: "admin",
      justification: null,
      expiresAt: "2026-12-31T23:59:00Z",
      createdAt: "2026-01-01T00:00:00Z",
      revokedAt: null,
      revokedBy: null,
      active: true,
    },
  ],
  total: 1,
};

export const testCategoryApprovers: CategoryApproverList = {
  approvers: [
    {
      approverId: "ap1",
      attributeKey: "sensitivity",
      categoryPath: "finance",
      principalName: "finance-owners",
      principalType: "GROUP",
      createdBy: "admin",
      createdAt: "2026-01-01T00:00:00Z",
    },
  ],
  total: 1,
};

export const testMyAccessRequests: AccessRequestList = {
  requests: [
    {
      requestId: "req1",
      requesterName: "ana.lopez@example.com",
      attributeKey: "sensitivity",
      attributeValue: "finance.budget.PII.Name",
      requestedExpiresAt: "2026-12-31T00:00:00Z",
      justification: "Quarterly close",
      status: "PENDING",
      decidedBy: null,
      decidedAt: null,
      decisionNote: null,
      approvedExpiresAt: null,
      createdAt: "2026-01-01T00:00:00Z",
      resolvedCategory: "finance",
      routedTo: "category_approvers",
      approvers: [{ principalName: "finance-owners", principalType: "GROUP" }],
    },
  ],
  total: 1,
};

export const testApprovals: ApprovalInbox = {
  items: [
    {
      kind: "REQUEST",
      request: testMyAccessRequests.requests[0]!,
      assignment: null,
      resolvedCategory: "finance",
      attributeValue: "finance.budget.PII.Name",
      principalName: "ana.lopez@example.com",
      expiresAt: "2026-12-31T00:00:00Z",
    },
  ],
  total: 1,
};


export const testMaskRules: MaskRuleList = {
  rules: [
    {
      ruleKey: "full",
      description: "Full redact",
      kind: "builtin",
      handler: "full",
      expression: null,
      params: {},
      validatedAt: null,
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
    {
      ruleKey: "email",
      description: "Keep email domain",
      kind: "builtin",
      handler: "email",
      expression: null,
      params: {},
      validatedAt: null,
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
    {
      ruleKey: "joinable",
      description: "Deterministic hash for joins",
      kind: "builtin",
      handler: "joinable",
      expression: null,
      params: {},
      validatedAt: null,
      updatedBy: "admin",
      updatedAt: "2026-01-01T00:00:00Z",
    },
  ],
  total: 3,
};

export const testMaskRulePack: MaskRulePackStatus = {
  config: {
    joinableSaltConfigured: true,
    tagKey: "mask_rule",
    tagValues: ["full", "email", "phone", "joinable", "keep_last_4"],
    missingTagValues: [],
    policyName: "mask_sensitivity_variant",
    policyExists: true,
    policyPassesMaskRule: true,
    functionName: "`data_governance_app`.`masking_functions`.`mask_variant`",
    udfInSync: true,
    inSync: true,
  },
  rules: testMaskRules.rules,
};

export function fakeServices(
  overrides: {
    user?: CurrentUser | Error;
    appInfo?: AppInfo;
    policies?: PolicyListResponse | Error;
    policy?: PolicyDetail | Error;
    definitions?: AttributeDefinitionList | Error;
    assignments?: AttributeAssignmentList | Error;
    myAttributes?: AttributeAssignmentList | Error;
    governedTags?: GovernedTagList | Error;
    categoryApprovers?: CategoryApproverList | Error;
    myAccessRequests?: AccessRequestList | Error;
    approvals?: ApprovalInbox | Error;
    rowFilterDimensions?: RowFilterDimensionList | Error;
    rowFilterAttachments?: RowFilterAttachmentList | Error;
    maskRules?: MaskRuleList | Error;
    dataAccessSettings?: DataAccessSettingsView | Error;
    dataAccessReadiness?: DataAccessReadiness | Error;
    dataAccessRun?: DataAccessRun | null | Error;
  } = {},
): ApiServices {
  const governedTags = overrides.governedTags ?? testGovernedTags;
  const user = overrides.user ?? testUser;
  const policies = overrides.policies ?? testPolicies;
  const policy = overrides.policy ?? testPolicyDetail;
  const definitions = overrides.definitions ?? testDefinitions;
  const assignments = overrides.assignments ?? testMyAttributes;
  const myAttributes = overrides.myAttributes ?? testMyAttributes;
  const categoryApprovers = overrides.categoryApprovers ?? testCategoryApprovers;
  const myAccessRequests = overrides.myAccessRequests ?? testMyAccessRequests;
  const approvals = overrides.approvals ?? testApprovals;
  const rowFilterDimensions = overrides.rowFilterDimensions ?? testRowFilterDimensions;
  const rowFilterAttachments = overrides.rowFilterAttachments ?? testRowFilterAttachments;
  const maskRules = overrides.maskRules ?? testMaskRules;

  return {
    identity: { me: () => (user instanceof Error ? Promise.reject(user) : Promise.resolve(user)) },
    appInfo: { get: () => Promise.resolve(overrides.appInfo ?? testAppInfo) },
    directory: {
      searchPrincipals: async () =>
        ({
          principals: [
            {
              name: "ana.lopez@example.com",
              displayName: "Ana Lopez",
              principalType: "USER",
              email: "ana.lopez@example.com",
            },
            {
              name: "data-stewards",
              displayName: "data-stewards",
              principalType: "GROUP",
              email: null,
            },
          ],
          total: 2,
          truncated: false,
        }) satisfies DirectoryPrincipalList,
    },
    recovery: {
      getSettings: async () => testRecoverySettings,
      saveSettings: async (body: RecoveryGitSettings) => body,
      previewExport: async () => testRecoveryPreview,
    },
    dataAccess: {
      getSettings: () =>
        overrides.dataAccessSettings instanceof Error
          ? Promise.reject(overrides.dataAccessSettings)
          : Promise.resolve(overrides.dataAccessSettings ?? testDataAccessSettings),
      saveSettings: async (body: DataAccessSettings) => ({
        ...(overrides.dataAccessSettings instanceof Error
          ? testDataAccessSettings
          : (overrides.dataAccessSettings ?? testDataAccessSettings)),
        settings: body,
      }),
      readiness: () =>
        overrides.dataAccessReadiness instanceof Error
          ? Promise.reject(overrides.dataAccessReadiness)
          : Promise.resolve(overrides.dataAccessReadiness ?? testDataAccessReadiness),
      listRuns: async (
        params?: { mode?: "dry_run" | "apply"; page?: number; pageSize?: number },
      ) => {
        if (overrides.dataAccessRun instanceof Error) {
          return Promise.reject(overrides.dataAccessRun);
        }
        if (overrides.dataAccessRun === null) {
          return {
            runs: [],
            total: 0,
            page: params?.page ?? 1,
            pageSize: params?.pageSize ?? 20,
            mode: params?.mode ?? null,
            retentionDays: 30,
          };
        }
        const run = overrides.dataAccessRun ?? testDataAccessRun;
        const mode = params?.mode;
        const include = !mode || run.mode === mode;
        return {
          runs: include ? [run] : [],
          total: include ? 1 : 0,
          page: params?.page ?? 1,
          pageSize: params?.pageSize ?? 20,
          mode: mode ?? null,
          retentionDays: 30,
        };
      },
      getRun: async (runId: string) => {
        if (overrides.dataAccessRun instanceof Error) {
          return Promise.reject(overrides.dataAccessRun);
        }
        if (overrides.dataAccessRun === null) {
          throw new Error("Run not found");
        }
        const run = overrides.dataAccessRun ?? testDataAccessRun;
        if (run.runId !== runId) throw new Error("Run not found");
        return run;
      },
      latestRun: async (mode?: "dry_run" | "apply") => {
        if (overrides.dataAccessRun instanceof Error) {
          return Promise.reject(overrides.dataAccessRun);
        }
        if (overrides.dataAccessRun === null) return null;
        const run = overrides.dataAccessRun ?? testDataAccessRun;
        if (mode && run.mode !== mode) return null;
        return run;
      },
      runNow: async (_mode?: "dry_run" | "apply") =>
        overrides.dataAccessRun instanceof Error
          ? Promise.reject(overrides.dataAccessRun)
          : Promise.resolve(overrides.dataAccessRun ?? testDataAccessRun),
    },
    attributeExplorer: {
      analyze: async (body: import("@/api/types").AnalyzeAttributeRequest) => {
        const value = body.attributeValue ?? "";
        if (!value) {
          return {
            attributeKey: body.attributeKey,
            attributeValue: "*",
            kind: "row_filter" as const,
            groupName: null,
            parentGroup: null,
            holders: [],
            principals: [
              {
                principalName: "ana.lopez@example.com",
                principalType: "USER" as const,
                values: ["CA", "NY"],
                expiresAt: null,
              },
              {
                principalName: "finance-analysts",
                principalType: "GROUP" as const,
                values: ["TX"],
                expiresAt: null,
              },
            ],
            tables: [],
            note: "Row-filter dimension overview.",
          };
        }
        return {
          attributeKey: body.attributeKey,
          attributeValue: value,
          kind: "data_access" as const,
          groupName: "dac_finance",
          parentGroup: null,
          holders: [
            {
              principalName: "ana.lopez@example.com",
              principalType: "USER" as const,
              attributeValue: value,
              match: "exact" as const,
              expiresAt: null,
              assignmentId: "as-1",
            },
          ],
          principals: [],
          tables: [
            {
              fullName: "sandbox.finance.budget",
              tagValue: value,
              match: "exact" as const,
              groupName: "dac_finance",
            },
          ],
          note: null,
        };
      },
    },
    policies: {
      list: () => (policies instanceof Error ? Promise.reject(policies) : Promise.resolve(policies)),
      get: () => (policy instanceof Error ? Promise.reject(policy) : Promise.resolve(policy)),
    },
    identityAttributes: {
      listDefinitions: () =>
        definitions instanceof Error ? Promise.reject(definitions) : Promise.resolve(definitions),
      listGovernedTags: () =>
        governedTags instanceof Error ? Promise.reject(governedTags) : Promise.resolve(governedTags),
      upsertDefinition: async (body: UpsertAttributeDefinitionRequest) => ({
        key: body.key ?? body.governedTagKeys[0] ?? "",
        description: body.description ?? null,
        governedTagKeys: body.governedTagKeys,
        valueSource: body.valueSource ?? "governed_tags",
        valueSql: body.valueSql ?? null,
        allowedValues: [],
        createdBy: "admin",
        createdAt: "2026-01-01T00:00:00Z",
        updatedBy: "admin",
        updatedAt: "2026-01-01T00:00:00Z",
      }),
      deleteDefinition: async () => undefined,
      listAssignments: () =>
        assignments instanceof Error ? Promise.reject(assignments) : Promise.resolve(assignments),
      myAssignments: () =>
        myAttributes instanceof Error ? Promise.reject(myAttributes) : Promise.resolve(myAttributes),
      assign: async (body: AssignAttributeRequest) =>
        ({
          assignmentId: "new",
          principalName: body.principalName,
          principalType: body.principalType,
          attributeKey: body.attributeKey,
          attributeValue: body.attributeValue,
          source: "MANUAL",
          grantedBy: "admin",
          justification: body.justification ?? null,
          expiresAt: body.expiresAt ?? null,
          createdAt: "2026-01-01T00:00:00Z",
          revokedAt: null,
          revokedBy: null,
          active: true,
        }) satisfies IdentityAttributeAssignment,
      revoke: async (assignmentId: string) => ({
        ...testMyAttributes.assignments[0]!,
        assignmentId,
        active: false,
        revokedAt: "2026-01-02T00:00:00Z",
        revokedBy: "admin",
      }),
      listCategoryApprovers: () =>
        categoryApprovers instanceof Error
          ? Promise.reject(categoryApprovers)
          : Promise.resolve(categoryApprovers),
      upsertCategoryApprover: async (body: UpsertCategoryApproverRequest) => ({
        approverId: "new-ap",
        attributeKey: body.attributeKey ?? "sensitivity",
        categoryPath: body.categoryPath,
        principalName: body.principalName,
        principalType: body.principalType,
        createdBy: "admin",
        createdAt: "2026-01-01T00:00:00Z",
      }),
      deleteCategoryApprover: async () => undefined,
      createAccessRequest: async (body: CreateAccessRequestBody) => ({
        requestId: "new-req",
        requesterName: "ana.lopez@example.com",
        attributeKey: body.attributeKey ?? "sensitivity",
        attributeValue: body.attributeValue,
        requestedExpiresAt: body.requestedExpiresAt,
        justification: body.justification,
        status: "PENDING" as const,
        decidedBy: null,
        decidedAt: null,
        decisionNote: null,
        approvedExpiresAt: null,
        createdAt: "2026-01-01T00:00:00Z",
        resolvedCategory: body.attributeKey && body.attributeKey !== "sensitivity" ? null : "finance",
        routedTo:
          body.attributeKey && body.attributeKey !== "sensitivity"
            ? ("app_admins" as const)
            : ("category_approvers" as const),
        approvers:
          body.attributeKey && body.attributeKey !== "sensitivity"
            ? []
            : [{ principalName: "finance-owners", principalType: "GROUP" }],
      }),
      myAccessRequests: () =>
        myAccessRequests instanceof Error
          ? Promise.reject(myAccessRequests)
          : Promise.resolve(myAccessRequests),
      cancelAccessRequest: async (requestId: string) => ({
        ...testMyAccessRequests.requests[0]!,
        requestId,
        status: "CANCELLED" as const,
      }),
      approvalsInbox: () =>
        approvals instanceof Error ? Promise.reject(approvals) : Promise.resolve(approvals),
      approveAccessRequest: async (requestId: string, _body?: DecideAccessRequestBody) => ({
        ...testMyAccessRequests.requests[0]!,
        requestId,
        status: "APPROVED" as const,
      }),
      denyAccessRequest: async (requestId: string, _body?: DecideAccessRequestBody) => ({
        ...testMyAccessRequests.requests[0]!,
        requestId,
        status: "DENIED" as const,
      }),
      approveRenewal: async (assignmentId: string) => ({
        ...testMyAttributes.assignments[0]!,
        assignmentId,
      }),
      dismissRenewal: async () => undefined,
    },
    maskRules: {
      list: () =>
        maskRules instanceof Error ? Promise.reject(maskRules) : Promise.resolve(maskRules),
      upsert: async (body: UpsertMaskRuleRequest) => ({
        ruleKey: body.ruleKey,
        description: body.description ?? null,
        kind: body.kind ?? "builtin",
        handler: String(body.handler ?? body.ruleKey),
        expression: body.expression ?? null,
        params: body.params ?? {},
        validatedAt: body.kind === "custom" ? "2026-01-01T00:00:00Z" : null,
        updatedBy: "admin",
        updatedAt: "2026-01-01T00:00:00Z",
      }),
      delete: async () => undefined,
      seed: async () => testMaskRules,
      packStatus: async () => testMaskRulePack,
      ensurePack: async () => testMaskRulePack,
      syncUc: async () => ({ rules: 3, salt_configured: 1 }),
      rotateSalt: async () => testMaskRulePack.config,
      draftHandler: async () => ({
        expression: "concat('ID-', sha2(lower(trim(v)), 256))",
        explanation: "Prefixed joinable hash",
        sampleInputs: ["alice@gmail.com", "CA"],
      }),
      validateHandler: async (body: MaskHandlerValidateRequest) => ({
        ok: true,
        expression: body.expression,
        samples: [
          { input: "alice@gmail.com", output: "ID-abc", error: null },
          { input: "CA", output: "ID-def", error: null },
        ],
        validationToken: "token",
      }),
    },
    rowFilters: {
      listDimensions: () =>
        rowFilterDimensions instanceof Error
          ? Promise.reject(rowFilterDimensions)
          : Promise.resolve(rowFilterDimensions),
      upsertDimension: async (body: UpsertRowFilterDimensionRequest) => ({
        key: body.key,
        description: body.description ?? null,
        valueSource: body.valueSource,
        valueSql: body.valueSql ?? null,
        allowedValueCount: body.allowedValues?.length ?? 0,
        sampleValues: body.allowedValues ?? [],
        attachmentCount: 0,
      }),
      deleteDimension: async () => undefined,
      refreshDimension: async () => testRowFilterDimensions.dimensions[0]!,
      previewSeed: async () => ({ values: ["CA", "NY", "TX"], total: 3 }),
      packStatus: () => Promise.resolve(testRowFilterPack),
      ensurePack: async () => testRowFilterPack,
      listAttachments: () =>
        rowFilterAttachments instanceof Error
          ? Promise.reject(rowFilterAttachments)
          : Promise.resolve(rowFilterAttachments),
      attach: async (body: AttachRowFilterRequest) => body,
      detach: async () => undefined,
    },
    accessExplorer: {
      search: async () => ({
        tables: [
          { catalogName: "demo", schemaName: "default", tableName: "customers" },
          { catalogName: "demo", schemaName: "default", tableName: "abac_type_test" },
        ],
        total: 2,
      }),
      analyze: async () => testTableAccessInsight,
      catalogs: async () => ({
        catalogs: [{ catalogName: "demo" }, { catalogName: "sandbox" }],
        total: 2,
        truncated: false,
      }),
      schemas: async (catalog: string) => ({
        schemas: [{ catalogName: catalog, schemaName: "default" }],
        total: 1,
        truncated: false,
      }),
      tables: async (catalog: string, schema: string) => ({
        tables: [
          { catalogName: catalog, schemaName: schema, tableName: "customers", tableType: "MANAGED" },
          { catalogName: catalog, schemaName: schema, tableName: "orders_view", tableType: "VIEW" },
        ],
        total: 2,
        truncated: false,
      }),
    },
    help: {
      flows: async () =>
        "# Developer guide\n\n## Mental model\n\nMasks and row filters.\n\n## Row filter flow\n\nUse `state=*`.\n",
      docs: async () => ({
        docs: [
          {
            slug: "masking-row-filters",
            title: "Masking & row filters",
            description: "Masks and row filters.",
            sourcePath: "docs/mask-and-row-filter-flows.md",
          },
          {
            slug: "grants-sync",
            title: "Grants sync",
            description: "dac_* groups and grants.",
            sourcePath: "docs/grants-sync.md",
          },
        ],
      }),
      doc: async (slug: string) =>
        slug === "grants-sync"
          ? "# Grants sync\n\n## Apply order and APIs\n\nAccount SCIM then GRANT.\n\n```mermaid\nflowchart TD\n  a --> b\n```\n"
          : "# Developer guide\n\n## Mental model\n\nMasks and row filters.\n\n## Row filter flow\n\nUse `state=*`.\n",
      apiReference: async () => ({
        endpoints: [
          {
            method: "POST",
            path: "/api/data-access/runs",
            tag: "data-access",
            summary: "Run a data-access sync now (dry-run or apply).",
            description: "Requires app admin.",
            access: "admin" as const,
            requestBody: "RunDataAccessRequest | None",
            responseModel: "DataAccessRun",
            parameters: [],
            handler: "backend.app.api.routes.data_access.run_now",
          },
          {
            method: "GET",
            path: "/api/me",
            tag: "identity",
            summary: "Return the signed-in user.",
            description: "",
            access: "user" as const,
            requestBody: null,
            responseModel: "CurrentUser",
            parameters: [],
            handler: "backend.app.api.routes.me.me",
          },
        ],
      }),
    },
    policyExceptions: {
      list: async () => ({
        exceptions: [
          {
            exceptionId: "system:app-sp",
            principalName: "app-sp",
            principalType: "SERVICE_PRINCIPAL",
            note: "App service principal (built-in)",
            createdBy: null,
            createdAt: "2026-01-01T00:00:00Z",
            system: true,
          },
        ],
        total: 1,
        systemPrincipals: ["app-sp"],
      }),
      upsert: async (body: UpsertPolicyExceptionRequest) => ({
        exceptionId: "ex-1",
        principalName: body.principalName,
        principalType: body.principalType,
        note: body.note ?? null,
        createdBy: "admin",
        createdAt: "2026-01-01T00:00:00Z",
        system: false,
      }),
      delete: async () => undefined,
      apply: async () => ({
        exceptPrincipals: ["app-sp", "data-services"],
        maskPackSynced: true,
        rowFilterPackSynced: true,
      }),
    },
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
