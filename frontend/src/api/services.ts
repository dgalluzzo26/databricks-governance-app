/**
 * @file Typed service classes, one per backend router, that map method calls to `/api` endpoints.
 *
 * Every method delegates to {@link HttpClient}, so any non-2xx response rejects with
 * {@link ApiError}. Path segments are URI-encoded; query parameters are snake_case.
 */
import { HttpClient } from "./httpClient";
import type {
  RecoveryGitSettings,
  RecoveryPreview,
  RecoveryPushResult,
  AccessRequest,
  AccessRequestList,
  AnalyzeAttributeRequest,
  ApiReference,
  AppInfo,
  AttributeExplorerResult,
  DataAccessReadiness,
  DataAccessRun,
  DataAccessRunList,
  DataAccessSettings,
  DataAccessSettingsView,
  HelpDocList,
  DirectoryPrincipalList,
  PrincipalKind,
  AttachRowFilterRequest,
  ApprovalInbox,
  AssignAttributeRequest,
  AssignmentListParams,
  AttributeAssignmentList,
  AttributeDefinition,
  AttributeDefinitionList,
  CategoryApprover,
  CategoryApproverList,
  CreateAccessRequestBody,
  CurrentUser,
  DecideAccessRequestBody,
  GovernedTagList,
  IdentityAttributeAssignment,
  ApplyPolicyExceptionsResult,
  PolicyDetail,
  PolicyException,
  PolicyExceptionList,
  PolicyListParams,
  PolicyListResponse,
  UpsertPolicyExceptionRequest,
  RowFilterAttachment,
  RowFilterAttachmentList,
  RowFilterDimension,
  RowFilterDimensionList,
  MaskHandlerDraft,
  MaskHandlerDraftRequest,
  MaskHandlerValidateRequest,
  MaskHandlerValidateResult,
  MaskRuleDefinition,
  MaskRuleList,
  MaskRulePackStatus,
  MaskingConfigStatus,
  RowFilterPackStatus,
  SeedPreview,
  UpsertAttributeDefinitionRequest,
  UpsertCategoryApproverRequest,
  UpsertMaskRuleRequest,
  UpsertRowFilterDimensionRequest,
  AnalyzeTableRequest,
  TableAccessInsight,
  TableSearchResult,
  TreeCatalogList,
  TreeSchemaList,
  TreeTableList,
} from "./types";

/**
 * Builds a query string from the given parameters, skipping `undefined` and empty values.
 *
 * @param params - Query parameter names (already snake_case) to values.
 * @returns `"?a=1&b=2"`, or `""` when no parameter has a value.
 */
function toQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

/** Current-user identity endpoint. */
export class IdentityApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /me` — the signed-in user, including admin/approver flags.
   *
   * @param signal - Optional abort signal.
   * @returns The current user.
   * @throws {@link ApiError} On a non-2xx response.
   */
  me(signal?: AbortSignal): Promise<CurrentUser> {
    return this.http.get<CurrentUser>("/me", signal);
  }
}

/** Workspace directory search for users and groups (admin only). */
export class DirectoryApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /directory/principals` — search users and/or groups by name substring.
   *
   * @param q - Search text (at least 2 characters).
   * @param kind - Optional `USER` or `GROUP` filter.
   * @param signal - Optional abort signal.
   * @returns Matching principals.
   */
  searchPrincipals(
    q: string,
    kind?: PrincipalKind,
    signal?: AbortSignal,
  ): Promise<DirectoryPrincipalList> {
    return this.http.get<DirectoryPrincipalList>(
      `/directory/principals${toQuery({
        q,
        kind,
        limit: "20",
      })}`,
      signal,
    );
  }
}

/** App deployment info endpoint. */
export class AppInfoApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /app-info` — deployment and storage configuration (admin only).
   *
   * @param signal - Optional abort signal.
   * @returns The app info.
   * @throws {@link ApiError} On a non-2xx response (403 for non-admins).
   */
  get(signal?: AbortSignal): Promise<AppInfo> {
    return this.http.get<AppInfo>("/app-info", signal);
  }
}

/** Tag-driven data-access group dry-run (admin only). */
export class DataAccessApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /data-access/settings` — current dry-run settings (admin only).
   *
   * @param signal - Optional abort signal.
   * @returns Settings plus the minimum allowed interval.
   */
  getSettings(signal?: AbortSignal): Promise<DataAccessSettingsView> {
    return this.http.get<DataAccessSettingsView>("/data-access/settings", signal);
  }

  /**
   * `PUT /data-access/settings` — persist dry-run settings (admin only).
   *
   * @param body - Settings to store. Apply mode is rejected until that phase ships.
   * @returns The stored settings view.
   */
  saveSettings(body: DataAccessSettings): Promise<DataAccessSettingsView> {
    return this.http.put<DataAccessSettingsView>("/data-access/settings", body);
  }

  /**
   * `GET /data-access/readiness` — Account Admin SP probes (admin only).
   *
   * @param signal - Optional abort signal.
   * @returns Per-check results and rolled-up ready flags.
   */
  readiness(signal?: AbortSignal): Promise<DataAccessReadiness> {
    return this.http.get<DataAccessReadiness>("/data-access/readiness", signal);
  }

  /**
   * `GET /data-access/runs` — paginated runs for the last 30 days (summaries only).
   *
   * @param params - Optional mode filter and page.
   * @param signal - Optional abort signal.
   * @returns Paginated run list.
   */
  listRuns(
    params?: { mode?: "dry_run" | "apply"; page?: number; pageSize?: number },
    signal?: AbortSignal,
  ): Promise<DataAccessRunList> {
    return this.http.get<DataAccessRunList>(
      `/data-access/runs${toQuery({
        mode: params?.mode,
        page: params?.page != null ? String(params.page) : undefined,
        page_size: params?.pageSize != null ? String(params.pageSize) : undefined,
      })}`,
      signal,
    );
  }

  /**
   * `GET /data-access/runs/latest` — most recent run including its truncated plan.
   *
   * @param mode - Optional ``dry_run`` or ``apply`` filter.
   * @param signal - Optional abort signal.
   * @returns The latest matching run, or `null` when none exist.
   */
  latestRun(mode?: "dry_run" | "apply", signal?: AbortSignal): Promise<DataAccessRun | null> {
    return this.http.get<DataAccessRun | null>(
      `/data-access/runs/latest${toQuery({ mode })}`,
      signal,
    );
  }

  /**
   * `GET /data-access/runs/{runId}` — one stored run including its sampled plan.
   *
   * @param runId - Identifier from the history list.
   * @param signal - Optional abort signal.
   * @returns The run.
   */
  getRun(runId: string, signal?: AbortSignal): Promise<DataAccessRun> {
    return this.http.get<DataAccessRun>(`/data-access/runs/${encodeURIComponent(runId)}`, signal);
  }

  /**
   * `POST /data-access/runs` — run a sync now (`dry_run` or `apply`).
   *
   * @param mode - Plan only, or mutate groups / membership / grants.
   * @returns The finished run.
   */
  runNow(mode: "dry_run" | "apply" = "dry_run"): Promise<DataAccessRun> {
    return this.http.post<DataAccessRun>("/data-access/runs", { mode });
  }
}

/** Attribute Explorer — holders and unlocked tables for a path. */
export class AttributeExplorerApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `POST /attribute-explorer/analyze` — reverse lookup for admins / path approvers.
   *
   * @param body - Attribute key and value.
   * @returns Holders, optional tables, and group metadata.
   */
  analyze(body: AnalyzeAttributeRequest): Promise<AttributeExplorerResult> {
    return this.http.post<AttributeExplorerResult>("/attribute-explorer/analyze", body);
  }
}

/** Read-only access to Unity Catalog ABAC policies (admin only). */
export class PoliciesApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /policies` — policies grouped into metastore and catalog buckets.
   *
   * @param params - Optional filters, sent as `scope`, `policy_type`, `catalog`, and `q`.
   * @param signal - Optional abort signal.
   * @returns The grouped policy list.
   * @throws {@link ApiError} On a non-2xx response.
   */
  list(params: PolicyListParams = {}, signal?: AbortSignal): Promise<PolicyListResponse> {
    return this.http.get<PolicyListResponse>(
      `/policies${toQuery({
        scope: params.scope,
        policy_type: params.policyType,
        catalog: params.catalog,
        q: params.q,
      })}`,
      signal,
    );
  }

  /**
   * `GET /policies/{policyId}` — one policy with its clauses and affected assets.
   *
   * @param policyId - Policy id.
   * @param signal - Optional abort signal.
   * @returns The policy detail.
   * @throws {@link ApiError} On a non-2xx response (404 if not found).
   */
  get(policyId: string, signal?: AbortSignal): Promise<PolicyDetail> {
    return this.http.get<PolicyDetail>(`/policies/${encodeURIComponent(policyId)}`, signal);
  }
}

/**
 * Identity attributes, assignments, category approvers, access requests, and renewals
 * (all routes under `/identity`).
 */
export class IdentityAttributesApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /identity/governed-tags?q=&limit=100` — governed tags available to back attribute definitions.
   *
   * @param q - Optional search text.
   * @param signal - Optional abort signal.
   * @returns Up to 100 governed tags.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listGovernedTags(q?: string, signal?: AbortSignal): Promise<GovernedTagList> {
    return this.http.get<GovernedTagList>(`/identity/governed-tags${toQuery({ q, limit: "100" })}`, signal);
  }

  /**
   * `GET /identity/definitions` — all attribute definitions.
   *
   * @param signal - Optional abort signal.
   * @returns The definitions.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listDefinitions(signal?: AbortSignal): Promise<AttributeDefinitionList> {
    return this.http.get<AttributeDefinitionList>("/identity/definitions", signal);
  }

  /**
   * `PUT /identity/definitions` — create or replace an attribute definition (admin only).
   *
   * @param body - Definition to save.
   * @returns The saved definition.
   * @throws {@link ApiError} On a non-2xx response (422 on validation errors).
   */
  upsertDefinition(body: UpsertAttributeDefinitionRequest): Promise<AttributeDefinition> {
    return this.http.put<AttributeDefinition>("/identity/definitions", body);
  }

  /**
   * `DELETE /identity/definitions/{key}` — delete an attribute definition (admin only).
   *
   * @param key - Attribute key.
   * @returns Resolves when deleted (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  deleteDefinition(key: string): Promise<void> {
    return this.http.delete<void>(`/identity/definitions/${encodeURIComponent(key)}`);
  }

  /**
   * `GET /identity/assignments` — assignments across all principals (admin only).
   *
   * @param params - Optional filters, sent as `principal_name`, `principal_type`, `attribute_key`, and
   *   `include_inactive=true` when requested.
   * @param signal - Optional abort signal.
   * @returns Matching assignments.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listAssignments(params: AssignmentListParams = {}, signal?: AbortSignal): Promise<AttributeAssignmentList> {
    return this.http.get<AttributeAssignmentList>(
      `/identity/assignments${toQuery({
        principal_name: params.principalName,
        principal_type: params.principalType,
        attribute_key: params.attributeKey,
        include_inactive: params.includeInactive ? "true" : undefined,
      })}`,
      signal,
    );
  }

  /**
   * `GET /identity/assignments/me` — the signed-in user's assignments.
   *
   * @param signal - Optional abort signal.
   * @returns The caller's assignments.
   * @throws {@link ApiError} On a non-2xx response.
   */
  myAssignments(signal?: AbortSignal): Promise<AttributeAssignmentList> {
    return this.http.get<AttributeAssignmentList>("/identity/assignments/me", signal);
  }

  /**
   * `POST /identity/assignments` — grant an attribute value to a principal (admin only).
   *
   * @param body - Assignment to create.
   * @returns The created assignment.
   * @throws {@link ApiError} On a non-2xx response.
   */
  assign(body: AssignAttributeRequest): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>("/identity/assignments", body);
  }

  /**
   * `POST /identity/assignments/{assignmentId}/revoke` — revoke an assignment.
   *
   * App admins may revoke any assignment. Approvers may revoke assignments for paths they
   * can decide (same scope as Attribute Explorer).
   *
   * @param assignmentId - Assignment id.
   * @returns The assignment with `revokedAt`/`revokedBy` set.
   * @throws {@link ApiError} On a non-2xx response.
   */
  revoke(assignmentId: string): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>(
      `/identity/assignments/${encodeURIComponent(assignmentId)}/revoke`,
    );
  }

  /**
   * `GET /identity/category-approvers` — category approvers (admin only).
   *
   * @param categoryPath - Optional filter, sent as `category_path`.
   * @param signal - Optional abort signal.
   * @returns The approvers.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listCategoryApprovers(
    categoryPath?: string,
    signal?: AbortSignal,
    attributeKey?: string,
  ): Promise<CategoryApproverList> {
    return this.http.get<CategoryApproverList>(
      `/identity/category-approvers${toQuery({
        category_path: categoryPath,
        attribute_key: attributeKey,
      })}`,
      signal,
    );
  }

  /**
   * `PUT /identity/category-approvers` — add an approver for a category path (admin only).
   *
   * @param body - Approver to save.
   * @returns The saved approver.
   * @throws {@link ApiError} On a non-2xx response.
   */
  upsertCategoryApprover(body: UpsertCategoryApproverRequest): Promise<CategoryApprover> {
    return this.http.put<CategoryApprover>("/identity/category-approvers", body);
  }

  /**
   * `DELETE /identity/category-approvers/{approverId}` — remove a category approver (admin only).
   *
   * @param approverId - Approver id.
   * @returns Resolves when deleted (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  deleteCategoryApprover(approverId: string): Promise<void> {
    return this.http.delete<void>(`/identity/category-approvers/${encodeURIComponent(approverId)}`);
  }

  /**
   * `POST /identity/access-requests` — submit an access request as the signed-in user.
   *
   * @param body - Requested attribute value, expiry, and justification.
   * @returns The created request (status `PENDING`) with routing and approvers.
   * @throws {@link ApiError} On a non-2xx response.
   */
  createAccessRequest(body: CreateAccessRequestBody): Promise<AccessRequest> {
    return this.http.post<AccessRequest>("/identity/access-requests", body);
  }

  /**
   * `GET /identity/access-requests/mine` — the signed-in user's own requests.
   *
   * @param signal - Optional abort signal.
   * @returns The caller's requests.
   * @throws {@link ApiError} On a non-2xx response.
   */
  myAccessRequests(signal?: AbortSignal): Promise<AccessRequestList> {
    return this.http.get<AccessRequestList>("/identity/access-requests/mine", signal);
  }

  /**
   * `POST /identity/access-requests/{requestId}/cancel` — cancel one of the caller's pending requests.
   *
   * @param requestId - Request id.
   * @returns The request with status `CANCELLED`.
   * @throws {@link ApiError} On a non-2xx response.
   */
  cancelAccessRequest(requestId: string): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/cancel`,
    );
  }

  /**
   * `GET /identity/approvals` — pending requests and due renewals the caller may decide.
   *
   * @param signal - Optional abort signal.
   * @returns The caller's approval inbox.
   * @throws {@link ApiError} On a non-2xx response.
   */
  approvalsInbox(signal?: AbortSignal): Promise<ApprovalInbox> {
    return this.http.get<ApprovalInbox>("/identity/approvals", signal);
  }

  /**
   * `POST /identity/access-requests/{requestId}/approve` — approve a request, granting the attribute.
   *
   * @param requestId - Request id.
   * @param body - Optional expiry override and note.
   * @returns The request with status `APPROVED`.
   * @throws {@link ApiError} On a non-2xx response (403 if the caller cannot decide it).
   */
  approveAccessRequest(requestId: string, body: DecideAccessRequestBody = {}): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/approve`,
      body,
    );
  }

  /**
   * `POST /identity/access-requests/{requestId}/deny` — deny a request.
   *
   * @param requestId - Request id.
   * @param body - Optional note (`expiresAt` is ignored for denials).
   * @returns The request with status `DENIED`.
   * @throws {@link ApiError} On a non-2xx response (403 if the caller cannot decide it).
   */
  denyAccessRequest(requestId: string, body: DecideAccessRequestBody = {}): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/deny`,
      body,
    );
  }

  /**
   * `POST /identity/renewals/{assignmentId}/approve` — extend an active sensitivity assignment
   * that is within its renewal window.
   *
   * @param assignmentId - Assignment id.
   * @param body - Optional new expiry (defaults to the current expiry) and note.
   * @returns The assignment with its updated `expiresAt`.
   * @throws {@link ApiError} On a non-2xx response.
   */
  approveRenewal(
    assignmentId: string,
    body: DecideAccessRequestBody = {},
  ): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>(
      `/identity/renewals/${encodeURIComponent(assignmentId)}/approve`,
      body,
    );
  }

  /**
   * `POST /identity/renewals/{assignmentId}/dismiss` — hide a renewal from the inbox without
   * changing the assignment, which still expires as scheduled.
   *
   * @param assignmentId - Assignment id.
   * @returns Resolves when dismissed (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  dismissRenewal(assignmentId: string): Promise<void> {
    return this.http.post<void>(`/identity/renewals/${encodeURIComponent(assignmentId)}/dismiss`);
  }
}

/** Row-filter dimensions, the row-filter policy pack, and column attachments (routes under `/row-filters`). */
export class RowFiltersApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /row-filters/dimensions` — all row-filter dimensions.
   *
   * @param signal - Optional abort signal.
   * @returns The dimensions.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listDimensions(signal?: AbortSignal): Promise<RowFilterDimensionList> {
    return this.http.get<RowFilterDimensionList>("/row-filters/dimensions", signal);
  }

  /**
   * `PUT /row-filters/dimensions` — create or replace a dimension (admin only).
   *
   * @param body - Dimension to save.
   * @returns The saved dimension.
   * @throws {@link ApiError} On a non-2xx response.
   */
  upsertDimension(body: UpsertRowFilterDimensionRequest): Promise<RowFilterDimension> {
    return this.http.put<RowFilterDimension>("/row-filters/dimensions", body);
  }

  /**
   * `DELETE /row-filters/dimensions/{key}` — delete a dimension (admin only).
   *
   * @param key - Dimension key.
   * @returns Resolves when deleted (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  deleteDimension(key: string): Promise<void> {
    return this.http.delete<void>(`/row-filters/dimensions/${encodeURIComponent(key)}`);
  }

  /**
   * `POST /row-filters/dimensions/{key}/refresh` — re-run a SQL-backed dimension's seed query (admin only).
   *
   * @param key - Dimension key.
   * @returns The dimension with refreshed values.
   * @throws {@link ApiError} On a non-2xx response.
   */
  refreshDimension(key: string): Promise<RowFilterDimension> {
    return this.http.post<RowFilterDimension>(`/row-filters/dimensions/${encodeURIComponent(key)}/refresh`);
  }

  /**
   * `POST /row-filters/seed-preview` — dry-run a seed query without saving (admin only).
   *
   * @param valueSql - SQL returning candidate dimension values.
   * @returns The values the query would produce.
   * @throws {@link ApiError} On a non-2xx response (e.g. invalid SQL).
   */
  previewSeed(valueSql: string): Promise<SeedPreview> {
    return this.http.post<SeedPreview>("/row-filters/seed-preview", { valueSql });
  }

  /**
   * `GET /row-filters/pack` — sync status of the row-filter tag, UDF, and policy (admin only).
   *
   * @param signal - Optional abort signal.
   * @returns The pack status.
   * @throws {@link ApiError} On a non-2xx response.
   */
  packStatus(signal?: AbortSignal): Promise<RowFilterPackStatus> {
    return this.http.get<RowFilterPackStatus>("/row-filters/pack", signal);
  }

  /**
   * `POST /row-filters/pack/ensure` — make the tag values, UDF, and metastore policy match the
   * current dimensions (admin only).
   *
   * @returns The pack status after reconciliation.
   * @throws {@link ApiError} On a non-2xx response.
   */
  ensurePack(): Promise<RowFilterPackStatus> {
    return this.http.post<RowFilterPackStatus>("/row-filters/pack/ensure");
  }

  /**
   * `GET /row-filters/attachments` — columns tagged with a row-filter dimension (admin only).
   *
   * @param signal - Optional abort signal.
   * @returns The attachments.
   * @throws {@link ApiError} On a non-2xx response.
   */
  listAttachments(signal?: AbortSignal): Promise<RowFilterAttachmentList> {
    return this.http.get<RowFilterAttachmentList>("/row-filters/attachments", signal);
  }

  /**
   * `POST /row-filters/attachments` — tag a column with a dimension so its rows are filtered (admin only).
   *
   * @param body - Table, column, and dimension.
   * @returns The created attachment.
   * @throws {@link ApiError} On a non-2xx response.
   */
  attach(body: AttachRowFilterRequest): Promise<RowFilterAttachment> {
    return this.http.post<RowFilterAttachment>("/row-filters/attachments", body);
  }

  /**
   * `DELETE /row-filters/attachments?table_full_name=&column_name=` — remove a column's row-filter tag (admin only).
   *
   * @param tableFullName - `catalog.schema.table`.
   * @param columnName - Column name.
   * @returns Resolves when removed (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  detach(tableFullName: string, columnName: string): Promise<void> {
    return this.http.delete<void>(
      `/row-filters/attachments${toQuery({ table_full_name: tableFullName, column_name: columnName })}`,
    );
  }
}

/** Mask rules, the mask policy pack, and custom-handler authoring (admin-only routes under `/mask-rules`). */
export class MaskRulesApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /mask-rules` — all mask rules.
   *
   * @param signal - Optional abort signal.
   * @returns The rules.
   * @throws {@link ApiError} On a non-2xx response.
   */
  list(signal?: AbortSignal): Promise<MaskRuleList> {
    return this.http.get<MaskRuleList>("/mask-rules", signal);
  }

  /**
   * `PUT /mask-rules` — create or replace a mask rule.
   *
   * @param body - Rule to save; custom rules need a validation token.
   * @returns The saved rule.
   * @throws {@link ApiError} On a non-2xx response.
   */
  upsert(body: UpsertMaskRuleRequest): Promise<MaskRuleDefinition> {
    return this.http.put<MaskRuleDefinition>("/mask-rules", body);
  }

  /**
   * `DELETE /mask-rules/{ruleKey}` — delete a mask rule.
   *
   * @param ruleKey - Rule key.
   * @returns Resolves when deleted (HTTP 204).
   * @throws {@link ApiError} On a non-2xx response.
   */
  delete(ruleKey: string): Promise<void> {
    return this.http.delete<void>(`/mask-rules/${encodeURIComponent(ruleKey)}`);
  }

  /**
   * `POST /mask-rules/seed` — add any missing default builtin rules (and the joinable salt) without
   * overwriting existing rules.
   *
   * @returns All rules after seeding.
   * @throws {@link ApiError} On a non-2xx response.
   */
  seed(): Promise<MaskRuleList> {
    return this.http.post<MaskRuleList>("/mask-rules/seed");
  }

  /**
   * `GET /mask-rules/pack` — sync status of the mask salt, tag, UDF, and policy, plus the rules.
   *
   * @param signal - Optional abort signal.
   * @returns The pack status.
   * @throws {@link ApiError} On a non-2xx response.
   */
  packStatus(signal?: AbortSignal): Promise<MaskRulePackStatus> {
    return this.http.get<MaskRulePackStatus>("/mask-rules/pack", signal);
  }

  /**
   * `POST /mask-rules/pack/ensure` — seed defaults if empty, ensure the salt and tag values, sync the
   * UC registry, and recreate the mask UDF and policy.
   *
   * @returns The pack status after reconciliation.
   * @throws {@link ApiError} On a non-2xx response.
   */
  ensurePack(): Promise<MaskRulePackStatus> {
    return this.http.post<MaskRulePackStatus>("/mask-rules/pack/ensure");
  }

  /**
   * `POST /mask-rules/sync-uc` — replace the Unity Catalog registry tables from primary storage.
   *
   * @returns Row counts written, keyed by table.
   * @throws {@link ApiError} On a non-2xx response.
   */
  syncUc(): Promise<Record<string, number>> {
    return this.http.post<Record<string, number>>("/mask-rules/sync-uc");
  }

  /**
   * `POST /mask-rules/salt/rotate` — generate a new salt for the `joinable` handler.
   *
   * @remarks
   * Rotating changes every `joinable` masked output, so previously captured masked values no longer match.
   *
   * @returns The masking config status after rotation.
   * @throws {@link ApiError} On a non-2xx response.
   */
  rotateSalt(): Promise<MaskingConfigStatus> {
    return this.http.post<MaskingConfigStatus>("/mask-rules/salt/rotate");
  }

  /**
   * `POST /mask-rules/handlers/draft` — ask the configured LLM serving endpoint to draft a custom mask expression.
   *
   * @param body - Prompt, plus optional current expression and validation errors to fix.
   * @returns The drafted expression, explanation, and sample inputs.
   * @throws {@link ApiError} On a non-2xx response (e.g. LLM endpoint not configured or failed).
   */
  draftHandler(body: MaskHandlerDraftRequest): Promise<MaskHandlerDraft> {
    return this.http.post<MaskHandlerDraft>("/mask-rules/handlers/draft", body);
  }

  /**
   * `POST /mask-rules/handlers/validate` — evaluate a custom expression against sample inputs on the SQL warehouse.
   *
   * @param body - Expression and optional sample inputs.
   * @returns Per-sample results, and a validation token when every sample succeeded.
   * @throws {@link ApiError} On a non-2xx response.
   */
  validateHandler(body: MaskHandlerValidateRequest): Promise<MaskHandlerValidateResult> {
    return this.http.post<MaskHandlerValidateResult>("/mask-rules/handlers/validate", body);
  }
}

/** Access Explorer: table search, catalog tree, and per-user table access analysis (routes under `/access-explorer`). */
export class AccessExplorerApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /access-explorer/search?q=&limit=` — tables whose full name contains `q`.
   *
   * @param q - Search text (the backend requires at least 2 characters).
   * @param limit - Maximum number of results. Defaults to 40.
   * @param signal - Optional abort signal.
   * @returns Matching tables.
   * @throws {@link ApiError} On a non-2xx response.
   */
  search(q: string, limit = 40, signal?: AbortSignal): Promise<TableSearchResult> {
    return this.http.get<TableSearchResult>(
      `/access-explorer/search${toQuery({ q, limit: String(limit) })}`,
      signal,
    );
  }

  /**
   * `POST /access-explorer/analyze` — explain how masks and row filters apply to the caller for one table.
   *
   * @param body - Table to analyze.
   * @returns Column, row-filter, and suggested-attribute insights.
   * @throws {@link ApiError} On a non-2xx response.
   */
  analyze(body: AnalyzeTableRequest): Promise<TableAccessInsight> {
    return this.http.post<TableAccessInsight>("/access-explorer/analyze", body);
  }

  /**
   * `GET /access-explorer/tree/catalogs` — catalogs for the browse tree.
   *
   * @param signal - Optional abort signal.
   * @returns The catalogs.
   * @throws {@link ApiError} On a non-2xx response.
   */
  catalogs(signal?: AbortSignal): Promise<TreeCatalogList> {
    return this.http.get<TreeCatalogList>("/access-explorer/tree/catalogs", signal);
  }

  /**
   * `GET /access-explorer/tree/schemas?catalog=` — schemas in a catalog.
   *
   * @param catalog - Catalog name.
   * @param signal - Optional abort signal.
   * @returns The schemas.
   * @throws {@link ApiError} On a non-2xx response.
   */
  schemas(catalog: string, signal?: AbortSignal): Promise<TreeSchemaList> {
    return this.http.get<TreeSchemaList>(
      `/access-explorer/tree/schemas${toQuery({ catalog })}`,
      signal,
    );
  }

  /**
   * `GET /access-explorer/tree/tables?catalog=&schema=` — tables in a schema.
   *
   * @param catalog - Catalog name.
   * @param schema - Schema name.
   * @param signal - Optional abort signal.
   * @returns The tables.
   * @throws {@link ApiError} On a non-2xx response.
   */
  tables(catalog: string, schema: string, signal?: AbortSignal): Promise<TreeTableList> {
    return this.http.get<TreeTableList>(
      `/access-explorer/tree/tables${toQuery({ catalog, schema })}`,
      signal,
    );
  }
}

/** In-app help content. */
export class HelpApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /help/flows` — the mask and row-filter developer guide.
   *
   * @param signal - Optional abort signal.
   * @returns The guide as raw Markdown.
   * @throws {@link ApiError} On a non-2xx response (404 if the document is missing).
   */
  flows(signal?: AbortSignal): Promise<string> {
    return this.http.getText("/help/flows", signal);
  }

  /**
   * `GET /help/docs` — the developer guides shown as Help tabs.
   *
   * @param signal - Optional abort signal.
   * @returns Guides in display order.
   */
  docs(signal?: AbortSignal): Promise<HelpDocList> {
    return this.http.get<HelpDocList>("/help/docs", signal);
  }

  /**
   * `GET /help/docs/{slug}` — one developer guide.
   *
   * @param slug - Guide key from {@link HelpApi.docs}.
   * @param signal - Optional abort signal.
   * @returns The guide as raw Markdown.
   * @throws {@link ApiError} On a non-2xx response (404 for an unknown slug).
   */
  doc(slug: string, signal?: AbortSignal): Promise<string> {
    return this.http.getText(`/help/docs/${encodeURIComponent(slug)}`, signal);
  }

  /**
   * `GET /help/api-reference` — every backend endpoint from the live route table.
   *
   * @param signal - Optional abort signal.
   * @returns Endpoints in registration order.
   */
  apiReference(signal?: AbortSignal): Promise<ApiReference> {
    return this.http.get<ApiReference>("/help/api-reference", signal);
  }
}

/** Global ABAC policy exceptions (admin-only routes under `/policy-exceptions`). */
export class PolicyExceptionsApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /policy-exceptions` — built-in system exceptions plus configured ones.
   *
   * @param signal - Optional abort signal.
   * @returns The exceptions.
   * @throws {@link ApiError} On a non-2xx response.
   */
  list(signal?: AbortSignal): Promise<PolicyExceptionList> {
    return this.http.get<PolicyExceptionList>("/policy-exceptions", signal);
  }

  /**
   * `PUT /policy-exceptions` — add or update an exception.
   *
   * @remarks
   * The backend rebuilds the mask and row-filter packs after saving, so the change takes effect immediately.
   *
   * @param body - Exception to save.
   * @returns The saved exception.
   * @throws {@link ApiError} On a non-2xx response (e.g. the principal is already a system exception).
   */
  upsert(body: UpsertPolicyExceptionRequest): Promise<PolicyException> {
    return this.http.put<PolicyException>("/policy-exceptions", body);
  }

  /**
   * `DELETE /policy-exceptions/{exceptionId}` — remove a configured exception and rebuild the packs.
   *
   * @remarks
   * The backend responds with `{ ok: true }`; the body is not used. System exceptions cannot be deleted.
   *
   * @param exceptionId - Exception id.
   * @returns Resolves when deleted.
   * @throws {@link ApiError} On a non-2xx response.
   */
  delete(exceptionId: string): Promise<void> {
    return this.http.delete<void>(`/policy-exceptions/${encodeURIComponent(exceptionId)}`);
  }

  /**
   * `POST /policy-exceptions/apply` — rebuild the mask and row-filter packs so their `EXCEPT` lists
   * match the current exceptions.
   *
   * @returns The applied principal list and which packs were synced.
   * @throws {@link ApiError} On a non-2xx response.
   */
  apply(): Promise<ApplyPolicyExceptionsResult> {
    return this.http.post<ApplyPolicyExceptionsResult>("/policy-exceptions/apply");
  }
}

/** The full set of API services exposed to components via `useApi()`. */
/** Recovery export preview and Git target settings (`/api/recovery`, admin only). */
export class RecoveryApi {
  /** @param http - Shared HTTP client. */
  constructor(private readonly http: HttpClient) {}

  /**
   * `GET /recovery/settings` — saved Git target settings, or defaults.
   *
   * @param signal - Optional abort signal.
   * @returns The settings (never includes the token).
   */
  getSettings(signal?: AbortSignal): Promise<RecoveryGitSettings> {
    return this.http.get<RecoveryGitSettings>("/recovery/settings", signal);
  }

  /**
   * `PUT /recovery/settings` — save the Git target settings.
   *
   * @param body - Settings to store.
   * @returns The settings as stored.
   */
  saveSettings(body: RecoveryGitSettings): Promise<RecoveryGitSettings> {
    return this.http.put<RecoveryGitSettings>("/recovery/settings", body);
  }

  /**
   * `POST /recovery/export/preview` — build the export files without writing to Git.
   *
   * @returns The files, target, and warnings for this snapshot.
   */
  previewExport(): Promise<RecoveryPreview> {
    return this.http.post<RecoveryPreview>("/recovery/export/preview", {});
  }

  /**
   * `POST /recovery/export/push` — commit the export to the saved repository and branch.
   *
   * @returns The commit reference and whether a new commit was pushed.
   */
  pushExport(): Promise<RecoveryPushResult> {
    return this.http.post<RecoveryPushResult>("/recovery/export/push", {});
  }
}

export interface ApiServices {
  identity: IdentityApi;
  directory: DirectoryApi;
  appInfo: AppInfoApi;
  dataAccess: DataAccessApi;
  policies: PoliciesApi;
  identityAttributes: IdentityAttributesApi;
  rowFilters: RowFiltersApi;
  maskRules: MaskRulesApi;
  accessExplorer: AccessExplorerApi;
  attributeExplorer: AttributeExplorerApi;
  help: HelpApi;
  policyExceptions: PolicyExceptionsApi;
  recovery: RecoveryApi;
}

/**
 * Creates every service bound to one shared HTTP client.
 *
 * @param http - Client to use. Defaults to a new {@link HttpClient} targeting `/api`.
 * @returns The services bundle.
 */
export function createApiServices(http: HttpClient = new HttpClient()): ApiServices {
  return {
    identity: new IdentityApi(http),
    directory: new DirectoryApi(http),
    appInfo: new AppInfoApi(http),
    dataAccess: new DataAccessApi(http),
    recovery: new RecoveryApi(http),
    policies: new PoliciesApi(http),
    identityAttributes: new IdentityAttributesApi(http),
    rowFilters: new RowFiltersApi(http),
    maskRules: new MaskRulesApi(http),
    accessExplorer: new AccessExplorerApi(http),
    attributeExplorer: new AttributeExplorerApi(http),
    help: new HelpApi(http),
    policyExceptions: new PolicyExceptionsApi(http),
  };
}
