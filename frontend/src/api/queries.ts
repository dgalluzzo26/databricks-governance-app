/**
 * @file TanStack Query hooks wrapping {@link ApiServices} calls, plus the shared query-key registry.
 *
 * Query hooks pass TanStack's abort signal through to the HTTP layer. Mutation hooks
 * invalidate the queries their writes affect on success. Errors surface as {@link ApiError}.
 * App-wide defaults (no refetch on window focus, no retry on 4xx) are set on the
 * `QueryClient` in `app/App.tsx`; `staleTime` is 0 unless a hook says otherwise.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useApi } from "./ApiProvider";
import type {
  AssignAttributeRequest,
  AttachRowFilterRequest,
  AssignmentListParams,
  CreateAccessRequestBody,
  DecideAccessRequestBody,
  PolicyListParams,
  UpsertAttributeDefinitionRequest,
  UpsertCategoryApproverRequest,
  AnalyzeTableRequest,
  MaskHandlerDraftRequest,
  MaskHandlerValidateRequest,
  UpsertMaskRuleRequest,
  UpsertPolicyExceptionRequest,
  UpsertRowFilterDimensionRequest,
  DataAccessSettings,
  PrincipalKind,
} from "./types";

/**
 * Query keys for cached server state.
 *
 * @remarks
 * Keys are hierarchical so prefix invalidation works: e.g. `["identity-assignments"]` covers
 * every `attributeAssignments(params)` key and `myAttributes`, and `["row-filters"]` /
 * `["mask-rules"]` cover every key in those families. Governed-tag and Access Explorer keys
 * are built inline in their hooks and are not listed here.
 */
export const queryKeys = {
  me: ["me"] as const,
  appInfo: ["app-info"] as const,
  policies: (params: PolicyListParams) => ["policies", params] as const,
  policy: (id: string) => ["policy", id] as const,
  attributeDefinitions: ["identity-definitions"] as const,
  attributeAssignments: (params: AssignmentListParams) => ["identity-assignments", params] as const,
  myAttributes: ["identity-assignments", "me"] as const,
  categoryApprovers: ["category-approvers"] as const,
  myAccessRequests: ["access-requests", "mine"] as const,
  approvals: ["approvals"] as const,
  rowFilterDimensions: ["row-filters", "dimensions"] as const,
  rowFilterPack: ["row-filters", "pack"] as const,
  rowFilterAttachments: ["row-filters", "attachments"] as const,
  maskRules: ["mask-rules"] as const,
  maskRulePack: ["mask-rules", "pack"] as const,
  helpFlows: ["help", "flows"] as const,
  policyExceptions: ["policy-exceptions"] as const,
  dataAccessSettings: ["data-access", "settings"] as const,
  dataAccessReadiness: ["data-access", "readiness"] as const,
  dataAccessRuns: (mode: "dry_run" | "apply", page: number) =>
    ["data-access", "runs", mode, page] as const,
  dataAccessLatestRun: (mode: "dry_run" | "apply") =>
    ["data-access", "runs", "latest", mode] as const,
  directoryPrincipals: (q: string, kind?: string) => ["directory", "principals", q, kind ?? ""] as const,
};

/**
 * Fetches the signed-in user (`GET /me`).
 *
 * @remarks Key: `queryKeys.me`. Stale after 5 minutes.
 * @returns The query result for {@link CurrentUser}.
 */
export function useCurrentUser() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.identity.me(signal),
    staleTime: 5 * 60_000,
  });
}

/**
 * Fetches deployment info (`GET /app-info`, admin only).
 *
 * @remarks Key: `queryKeys.appInfo`. Stale after 30 seconds.
 * @param enabled - Set `false` to skip fetching (e.g. for non-admins). Defaults to `true`.
 * @returns The query result for {@link AppInfo}.
 */
export function useAppInfo(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.appInfo,
    queryFn: ({ signal }) => api.appInfo.get(signal),
    staleTime: 30_000,
    enabled,
  });
}

/**
 * Fetches data-access dry-run settings (`GET /data-access/settings`, admin only).
 *
 * @remarks Key: `queryKeys.dataAccessSettings`.
 * @returns The query result for {@link DataAccessSettingsView}.
 */
export function useDataAccessSettings() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.dataAccessSettings,
    queryFn: ({ signal }) => api.dataAccess.getSettings(signal),
  });
}

/**
 * Saves data-access dry-run settings (`PUT /data-access/settings`).
 *
 * @returns A mutation taking {@link DataAccessSettings}.
 */
export function useSaveDataAccessSettings() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Saving data-access settings…" },
    mutationFn: (body: DataAccessSettings) => api.dataAccess.saveSettings(body),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["data-access"] });
    },
  });
}

/**
 * Probes Account Admin SP readiness for data-access sync (`GET /data-access/readiness`).
 *
 * @remarks Key: `queryKeys.dataAccessReadiness`. Stale after 30 seconds.
 * @returns The query result for {@link DataAccessReadiness}.
 */
export function useDataAccessReadiness() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.dataAccessReadiness,
    queryFn: ({ signal }) => api.dataAccess.readiness(signal),
    staleTime: 30_000,
  });
}

/**
 * Fetches paginated data-access runs for one mode (`GET /data-access/runs`).
 *
 * @param mode - ``dry_run`` or ``apply`` (tab-scoped history).
 * @param page - 1-based page index.
 * @returns The query result for {@link DataAccessRunList}.
 */
export function useDataAccessRuns(mode: "dry_run" | "apply", page = 1) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.dataAccessRuns(mode, page),
    queryFn: ({ signal }) => api.dataAccess.listRuns({ mode, page, pageSize: 20 }, signal),
  });
}

/**
 * Fetches the latest run for one mode including its plan (`GET /data-access/runs/latest`).
 *
 * @param mode - ``dry_run`` or ``apply``.
 * @returns The query result for {@link DataAccessRun} or `null`.
 */
export function useDataAccessLatestRun(mode: "dry_run" | "apply") {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.dataAccessLatestRun(mode),
    queryFn: ({ signal }) => api.dataAccess.latestRun(mode, signal),
  });
}

/**
 * Runs a data-access sync now (`POST /data-access/runs`).
 *
 * @returns A mutation taking `dry_run` | `apply`, resolving to a {@link DataAccessRun}.
 */
export function useRunDataAccessSync() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Running data-access sync…" },
    mutationFn: (mode: "dry_run" | "apply" = "dry_run") => api.dataAccess.runNow(mode),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ["data-access"] });
    },
  });
}

/**
 * Attribute Explorer reverse lookup (`POST /attribute-explorer/analyze`).
 *
 * @returns A mutation taking `{ attributeKey, attributeValue }`.
 */
export function useAnalyzeAttribute() {
  const api = useApi();
  return useMutation({
    meta: { busyMessage: "Analyzing attribute…" },
    mutationFn: (body: { attributeKey: string; attributeValue: string }) =>
      api.attributeExplorer.analyze(body),
  });
}

/**
 * Searches workspace users/groups for the principal picker (`GET /directory/principals`).
 *
 * @remarks Disabled until the query has at least 2 characters. Stale after 30 seconds.
 * @param q - Search text.
 * @param kind - Optional `USER` or `GROUP` filter.
 * @returns The query result for {@link DirectoryPrincipalList}.
 */
export function usePrincipalSearch(q: string, kind?: PrincipalKind) {
  const api = useApi();
  const trimmed = q.trim();
  return useQuery({
    queryKey: queryKeys.directoryPrincipals(trimmed, kind),
    queryFn: ({ signal }) => api.directory.searchPrincipals(trimmed, kind, signal),
    enabled: trimmed.length >= 2,
    staleTime: 30_000,
  });
}

/**
 * Fetches ABAC policies matching the given filters (`GET /policies`).
 *
 * @remarks Key: `queryKeys.policies(params)`, so each filter combination is cached separately. Stale after 30 seconds.
 * @param params - Optional scope/type/catalog/search filters.
 * @returns The query result for {@link PolicyListResponse}.
 */
export function usePolicies(params: PolicyListParams = {}) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.policies(params),
    queryFn: ({ signal }) => api.policies.list(params, signal),
    staleTime: 30_000,
  });
}

/**
 * Fetches one policy's detail (`GET /policies/{id}`).
 *
 * @remarks Key: `queryKeys.policy(policyId)`. Disabled until `policyId` is truthy. Stale after 30 seconds.
 * @param policyId - Policy id, typically from the route; `undefined` disables the query.
 * @returns The query result for {@link PolicyDetail}.
 */
export function usePolicy(policyId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.policy(policyId ?? ""),
    queryFn: ({ signal }) => api.policies.get(policyId!, signal),
    enabled: Boolean(policyId),
    staleTime: 30_000,
  });
}

/**
 * Searches governed tags (`GET /identity/governed-tags`, up to 100 results).
 *
 * @remarks Key: `["governed-tags", q ?? ""]`. Stale after 60 seconds.
 * @param q - Optional search text.
 * @returns The query result for {@link GovernedTagList}.
 */
export function useGovernedTags(q?: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["governed-tags", q ?? ""] as const,
    queryFn: ({ signal }) => api.identityAttributes.listGovernedTags(q, signal),
    staleTime: 60_000,
  });
}

/**
 * Fetches all identity attribute definitions (`GET /identity/definitions`).
 *
 * @remarks Key: `queryKeys.attributeDefinitions`. Stale after 30 seconds.
 * @returns The query result for {@link AttributeDefinitionList}.
 */
export function useAttributeDefinitions() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.attributeDefinitions,
    queryFn: ({ signal }) => api.identityAttributes.listDefinitions(signal),
    staleTime: 30_000,
  });
}

/**
 * Fetches attribute assignments across principals (`GET /identity/assignments`, admin only).
 *
 * @remarks Key: `queryKeys.attributeAssignments(params)`. Stale after 15 seconds.
 * @param params - Optional principal/attribute filters and `includeInactive`.
 * @returns The query result for {@link AttributeAssignmentList}.
 */
export function useAttributeAssignments(params: AssignmentListParams = {}) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.attributeAssignments(params),
    queryFn: ({ signal }) => api.identityAttributes.listAssignments(params, signal),
    staleTime: 15_000,
  });
}

/**
 * Fetches the signed-in user's own attribute assignments (`GET /identity/assignments/me`).
 *
 * @remarks Key: `queryKeys.myAttributes`. Stale after 15 seconds.
 * @returns The query result for {@link AttributeAssignmentList}.
 */
export function useMyAttributes() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.myAttributes,
    queryFn: ({ signal }) => api.identityAttributes.myAssignments(signal),
    staleTime: 15_000,
  });
}

/**
 * Creates or replaces an attribute definition (`PUT /identity/definitions`).
 *
 * @remarks Invalidates `queryKeys.attributeDefinitions` on success.
 * @returns A mutation taking an {@link UpsertAttributeDefinitionRequest}.
 */
export function useUpsertAttributeDefinition() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Saving definition…" },
    mutationFn: (body: UpsertAttributeDefinitionRequest) => api.identityAttributes.upsertDefinition(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
  });
}

/**
 * Deletes an attribute definition by key (`DELETE /identity/definitions/{key}`).
 *
 * @remarks Invalidates `queryKeys.attributeDefinitions` on success.
 * @returns A mutation taking the attribute key.
 */
export function useDeleteAttributeDefinition() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Deleting definition…" },
    mutationFn: (key: string) => api.identityAttributes.deleteDefinition(key),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
  });
}

/**
 * Grants an attribute value to a principal (`POST /identity/assignments`).
 *
 * @remarks Invalidates every `["identity-assignments", ...]` query (admin lists and `myAttributes`) on success.
 * @returns A mutation taking an {@link AssignAttributeRequest}.
 */
export function useAssignAttribute() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Assigning attribute…" },
    mutationFn: (body: AssignAttributeRequest) => api.identityAttributes.assign(body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

/**
 * Revokes an attribute assignment (`POST /identity/assignments/{id}/revoke`).
 *
 * @remarks Invalidates every `["identity-assignments", ...]` query on success.
 * @returns A mutation taking the assignment id.
 */
export function useRevokeAttribute() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Revoking attribute…" },
    mutationFn: (assignmentId: string) => api.identityAttributes.revoke(assignmentId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

/**
 * Fetches all category approvers (`GET /identity/category-approvers`, admin only).
 *
 * @remarks Key: `queryKeys.categoryApprovers`. Stale after 15 seconds.
 * @returns The query result for {@link CategoryApproverList}.
 */
export function useCategoryApprovers() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.categoryApprovers,
    queryFn: ({ signal }) => api.identityAttributes.listCategoryApprovers(undefined, signal),
    staleTime: 15_000,
  });
}

/**
 * Adds or updates a category approver (`PUT /identity/category-approvers`).
 *
 * @remarks Invalidates `queryKeys.categoryApprovers` on success.
 * @returns A mutation taking an {@link UpsertCategoryApproverRequest}.
 */
export function useUpsertCategoryApprover() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Saving approver…" },
    mutationFn: (body: UpsertCategoryApproverRequest) => api.identityAttributes.upsertCategoryApprover(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.categoryApprovers }),
  });
}

/**
 * Removes a category approver (`DELETE /identity/category-approvers/{id}`).
 *
 * @remarks Invalidates `queryKeys.categoryApprovers` on success.
 * @returns A mutation taking the approver id.
 */
export function useDeleteCategoryApprover() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Removing approver…" },
    mutationFn: (approverId: string) => api.identityAttributes.deleteCategoryApprover(approverId),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.categoryApprovers }),
  });
}

/**
 * Fetches the signed-in user's own access requests (`GET /identity/access-requests/mine`).
 *
 * @remarks Key: `queryKeys.myAccessRequests`. Stale after 15 seconds.
 * @returns The query result for {@link AccessRequestList}.
 */
export function useMyAccessRequests() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.myAccessRequests,
    queryFn: ({ signal }) => api.identityAttributes.myAccessRequests(signal),
    staleTime: 15_000,
  });
}

/**
 * Submits an access request as the signed-in user (`POST /identity/access-requests`).
 *
 * @remarks Invalidates `queryKeys.myAccessRequests` and `queryKeys.approvals` on success.
 * @returns A mutation taking a {@link CreateAccessRequestBody}.
 */
export function useCreateAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Submitting request…" },
    mutationFn: (body: CreateAccessRequestBody) => api.identityAttributes.createAccessRequest(body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

/**
 * Cancels one of the caller's pending requests (`POST /identity/access-requests/{id}/cancel`).
 *
 * @remarks Invalidates `queryKeys.myAccessRequests` and `queryKeys.approvals` on success.
 * @returns A mutation taking the request id.
 */
export function useCancelAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Cancelling request…" },
    mutationFn: (requestId: string) => api.identityAttributes.cancelAccessRequest(requestId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

/**
 * Fetches the caller's approval inbox: pending requests and due renewals (`GET /identity/approvals`).
 *
 * @param enabled - When false, skips the fetch (e.g. nav badge for non-approvers).
 * @remarks Key: `queryKeys.approvals`. Stale after 15 seconds.
 * @returns The query result for {@link ApprovalInbox}.
 */
export function useApprovalsInbox(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: ({ signal }) => api.identityAttributes.approvalsInbox(signal),
    staleTime: 15_000,
    enabled,
  });
}

/**
 * Approves an access request (`POST /identity/access-requests/{id}/approve`), which grants the attribute.
 *
 * @remarks
 * Invalidates `queryKeys.approvals`, `queryKeys.myAccessRequests`, and every
 * `["identity-assignments", ...]` query on success.
 *
 * @returns A mutation taking `{ requestId, body? }`, where `body` can override the expiry and add a note.
 */
export function useApproveAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Approving request…" },
    mutationFn: ({ requestId, body }: { requestId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.approveAccessRequest(requestId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

/**
 * Denies an access request (`POST /identity/access-requests/{id}/deny`).
 *
 * @remarks Invalidates `queryKeys.approvals` and `queryKeys.myAccessRequests` on success.
 * @returns A mutation taking `{ requestId, body? }`, where `body` can carry a decision note.
 */
export function useDenyAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Denying request…" },
    mutationFn: ({ requestId, body }: { requestId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.denyAccessRequest(requestId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
    },
  });
}

/**
 * Extends an expiring sensitivity assignment (`POST /identity/renewals/{id}/approve`).
 *
 * @remarks Invalidates `queryKeys.approvals` and every `["identity-assignments", ...]` query on success.
 * @returns A mutation taking `{ assignmentId, body? }`, where `body` can set the new expiry and a note.
 */
export function useApproveRenewal() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Approving renewal…" },
    mutationFn: ({ assignmentId, body }: { assignmentId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.approveRenewal(assignmentId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

/**
 * Dismisses a renewal from the inbox without changing the assignment (`POST /identity/renewals/{id}/dismiss`).
 *
 * @remarks Invalidates `queryKeys.approvals` on success.
 * @returns A mutation taking the assignment id.
 */
export function useDismissRenewal() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Dismissing renewal…" },
    mutationFn: (assignmentId: string) => api.identityAttributes.dismissRenewal(assignmentId),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.approvals }),
  });
}

/**
 * Fetches all row-filter dimensions (`GET /row-filters/dimensions`).
 *
 * @remarks Key: `queryKeys.rowFilterDimensions`. Uses the default `staleTime` (0).
 * @returns The query result for {@link RowFilterDimensionList}.
 */
export function useRowFilterDimensions() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterDimensions,
    queryFn: ({ signal }) => api.rowFilters.listDimensions(signal),
  });
}

/**
 * Fetches row-filter pack sync status (`GET /row-filters/pack`, admin only).
 *
 * @remarks Key: `queryKeys.rowFilterPack`.
 * @param enabled - Set `false` to skip fetching (e.g. for non-admins). Defaults to `true`.
 * @returns The query result for {@link RowFilterPackStatus}.
 */
export function useRowFilterPack(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterPack,
    queryFn: ({ signal }) => api.rowFilters.packStatus(signal),
    enabled,
  });
}

/**
 * Fetches all row-filter column attachments (`GET /row-filters/attachments`, admin only).
 *
 * @remarks Key: `queryKeys.rowFilterAttachments`.
 * @returns The query result for {@link RowFilterAttachmentList}.
 */
export function useRowFilterAttachments() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterAttachments,
    queryFn: ({ signal }) => api.rowFilters.listAttachments(signal),
  });
}

/**
 * Returns a callback that invalidates every `["row-filters", ...]` query and `queryKeys.attributeDefinitions`.
 *
 * @remarks Row-filter dimensions are also identity attribute definitions, so both caches must refresh together.
 * @returns A function resolving once both invalidations complete.
 */
function useInvalidateRowFilters() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["row-filters"] }),
      client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
    ]);
}

/**
 * Creates or replaces a row-filter dimension (`PUT /row-filters/dimensions`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation taking an {@link UpsertRowFilterDimensionRequest}.
 */
export function useUpsertRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (body: UpsertRowFilterDimensionRequest) => api.rowFilters.upsertDimension(body),
    onSuccess: invalidate,
  });
}

/**
 * Deletes a row-filter dimension (`DELETE /row-filters/dimensions/{key}`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation taking the dimension key.
 */
export function useDeleteRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (key: string) => api.rowFilters.deleteDimension(key),
    onSuccess: invalidate,
  });
}

/**
 * Re-runs a SQL-backed dimension's seed query (`POST /row-filters/dimensions/{key}/refresh`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation taking the dimension key.
 */
export function useRefreshRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (key: string) => api.rowFilters.refreshDimension(key),
    onSuccess: invalidate,
  });
}

/**
 * Dry-runs a seed SQL query without saving (`POST /row-filters/seed-preview`).
 *
 * @remarks Read-only; invalidates nothing.
 * @returns A mutation taking the SQL text and resolving to a {@link SeedPreview}.
 */
export function usePreviewSeed() {
  const api = useApi();
  return useMutation({ mutationFn: (valueSql: string) => api.rowFilters.previewSeed(valueSql) });
}

/**
 * Reconciles the row-filter tag, UDF, and policy with the current dimensions (`POST /row-filters/pack/ensure`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation with no variables.
 */
export function useEnsureRowFilterPack() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    meta: { busyMessage: "Syncing row-filter pack…" },
    mutationFn: () => api.rowFilters.ensurePack(),
    onSuccess: invalidate,
  });
}

/**
 * Tags a column with a row-filter dimension (`POST /row-filters/attachments`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation taking an {@link AttachRowFilterRequest}.
 */
export function useAttachRowFilter() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (body: AttachRowFilterRequest) => api.rowFilters.attach(body),
    onSuccess: invalidate,
  });
}

/**
 * Removes a column's row-filter tag (`DELETE /row-filters/attachments`).
 *
 * @remarks Invalidates all row-filter queries and attribute definitions on success.
 * @returns A mutation taking `{ tableFullName, columnName }`.
 */
export function useDetachRowFilter() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: ({ tableFullName, columnName }: { tableFullName: string; columnName: string }) =>
      api.rowFilters.detach(tableFullName, columnName),
    onSuccess: invalidate,
  });
}

/**
 * Fetches all mask rules (`GET /mask-rules`, admin only).
 *
 * @remarks Key: `queryKeys.maskRules`.
 * @returns The query result for {@link MaskRuleList}.
 */
export function useMaskRules() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.maskRules,
    queryFn: ({ signal }) => api.maskRules.list(signal),
  });
}

/**
 * Fetches mask pack sync status and rules (`GET /mask-rules/pack`, admin only).
 *
 * @remarks Key: `queryKeys.maskRulePack`.
 * @param enabled - Set `false` to skip fetching (e.g. for non-admins). Defaults to `true`.
 * @returns The query result for {@link MaskRulePackStatus}.
 */
export function useMaskRulePack(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.maskRulePack,
    queryFn: ({ signal }) => api.maskRules.packStatus(signal),
    enabled,
  });
}

/**
 * Returns a callback that invalidates every `["mask-rules", ...]` query (rule list and pack status).
 *
 * @returns A function resolving once invalidation completes.
 */
function useInvalidateMaskRules() {
  const client = useQueryClient();
  return () => client.invalidateQueries({ queryKey: ["mask-rules"] });
}

/**
 * Creates or replaces a mask rule (`PUT /mask-rules`).
 *
 * @remarks Invalidates all mask-rule queries on success.
 * @returns A mutation taking an {@link UpsertMaskRuleRequest}.
 */
export function useUpsertMaskRule() {
  const api = useApi();
  const invalidate = useInvalidateMaskRules();
  return useMutation({
    mutationFn: (body: UpsertMaskRuleRequest) => api.maskRules.upsert(body),
    onSuccess: invalidate,
  });
}

/**
 * Deletes a mask rule (`DELETE /mask-rules/{ruleKey}`).
 *
 * @remarks Invalidates all mask-rule queries on success.
 * @returns A mutation taking the rule key.
 */
export function useDeleteMaskRule() {
  const api = useApi();
  const invalidate = useInvalidateMaskRules();
  return useMutation({
    mutationFn: (ruleKey: string) => api.maskRules.delete(ruleKey),
    onSuccess: invalidate,
  });
}

/**
 * Adds any missing default builtin mask rules (`POST /mask-rules/seed`).
 *
 * @remarks Invalidates all mask-rule queries on success.
 * @returns A mutation with no variables.
 */
export function useSeedMaskRules() {
  const api = useApi();
  const invalidate = useInvalidateMaskRules();
  return useMutation({
    meta: { busyMessage: "Seeding mask rules…" },
    mutationFn: () => api.maskRules.seed(),
    onSuccess: invalidate,
  });
}

/**
 * Reconciles the mask salt, tag, UC registry, UDF, and policy (`POST /mask-rules/pack/ensure`).
 *
 * @remarks Invalidates all mask-rule queries on success.
 * @returns A mutation with no variables.
 */
export function useEnsureMaskRulePack() {
  const api = useApi();
  const invalidate = useInvalidateMaskRules();
  return useMutation({
    meta: { busyMessage: "Syncing mask pack…" },
    mutationFn: () => api.maskRules.ensurePack(),
    onSuccess: invalidate,
  });
}

/**
 * Rotates the `joinable` mask salt (`POST /mask-rules/salt/rotate`).
 *
 * @remarks Invalidates all mask-rule queries on success.
 * @returns A mutation with no variables.
 */
export function useRotateMaskSalt() {
  const api = useApi();
  const invalidate = useInvalidateMaskRules();
  return useMutation({
    meta: { busyMessage: "Rotating mask salt…" },
    mutationFn: () => api.maskRules.rotateSalt(),
    onSuccess: invalidate,
  });
}

/**
 * Asks the LLM to draft a custom mask expression (`POST /mask-rules/handlers/draft`).
 *
 * @remarks Has no server-side effect; invalidates nothing.
 * @returns A mutation taking a {@link MaskHandlerDraftRequest} and resolving to a {@link MaskHandlerDraft}.
 */
export function useDraftMaskHandler() {
  const api = useApi();
  return useMutation({
    mutationFn: (body: MaskHandlerDraftRequest) => api.maskRules.draftHandler(body),
  });
}

/**
 * Validates a custom mask expression against sample inputs (`POST /mask-rules/handlers/validate`).
 *
 * @remarks Invalidates nothing; a successful result carries the token needed to save the rule.
 * @returns A mutation taking a {@link MaskHandlerValidateRequest} and resolving to a {@link MaskHandlerValidateResult}.
 */
export function useValidateMaskHandler() {
  const api = useApi();
  return useMutation({
    mutationFn: (body: MaskHandlerValidateRequest) => api.maskRules.validateHandler(body),
  });
}

/**
 * Searches tables by name for the Access Explorer (`GET /access-explorer/search`, limit 40).
 *
 * @remarks
 * Key: `["access-explorer", "search", trimmedQuery]`. Only runs when `enabled` is `true` and the
 * trimmed query has at least 2 characters (the backend minimum).
 *
 * @param q - Raw search text; leading and trailing whitespace is ignored.
 * @param enabled - Set `false` to suspend searching. Defaults to `true`.
 * @returns The query result for {@link TableSearchResult}.
 */
export function useTableSearch(q: string, enabled = true) {
  const api = useApi();
  const trimmed = q.trim();
  return useQuery({
    queryKey: ["access-explorer", "search", trimmed],
    queryFn: ({ signal }) => api.accessExplorer.search(trimmed, 40, signal),
    enabled: enabled && trimmed.length >= 2,
  });
}

/**
 * Analyzes how masks and row filters apply to the caller for one table (`POST /access-explorer/analyze`).
 *
 * @remarks Exposed as a mutation so analysis runs on demand; results are not cached and nothing is invalidated.
 * @returns A mutation taking an {@link AnalyzeTableRequest} and resolving to a {@link TableAccessInsight}.
 */
export function useAnalyzeTable() {
  const api = useApi();
  return useMutation({
    mutationFn: (body: AnalyzeTableRequest) =>
      api.accessExplorer.analyze({
        fullName: body.fullName ?? body.tableFullName,
        tableFullName: body.tableFullName ?? body.fullName,
      }),
  });
}

/**
 * Fetches catalogs for the Access Explorer tree (`GET /access-explorer/tree/catalogs`).
 *
 * @remarks Key: `["access-explorer", "tree", "catalogs"]`.
 * @returns The query result for {@link TreeCatalogList}.
 */
export function useTreeCatalogs() {
  const api = useApi();
  return useQuery({
    queryKey: ["access-explorer", "tree", "catalogs"],
    queryFn: ({ signal }) => api.accessExplorer.catalogs(signal),
  });
}

/**
 * Fetches schemas in a catalog for the Access Explorer tree (`GET /access-explorer/tree/schemas`).
 *
 * @remarks Key: `["access-explorer", "tree", "schemas", catalog]`.
 * @param catalog - Catalog name.
 * @param enabled - Typically `true` only while the catalog node is expanded.
 * @returns The query result for {@link TreeSchemaList}.
 */
export function useTreeSchemas(catalog: string, enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: ["access-explorer", "tree", "schemas", catalog],
    queryFn: ({ signal }) => api.accessExplorer.schemas(catalog, signal),
    enabled,
  });
}

/**
 * Fetches tables in a schema for the Access Explorer tree (`GET /access-explorer/tree/tables`).
 *
 * @remarks Key: `["access-explorer", "tree", "tables", catalog, schema]`.
 * @param catalog - Catalog name.
 * @param schema - Schema name.
 * @param enabled - Typically `true` only while the schema node is expanded.
 * @returns The query result for {@link TreeTableList}.
 */
export function useTreeTables(catalog: string, schema: string, enabled: boolean) {
  const api = useApi();
  return useQuery({
    queryKey: ["access-explorer", "tree", "tables", catalog, schema],
    queryFn: ({ signal }) => api.accessExplorer.tables(catalog, schema, signal),
    enabled,
  });
}

/**
 * Fetches the mask/row-filter developer guide as Markdown (`GET /help/flows`).
 *
 * @remarks Key: `queryKeys.helpFlows`. Stale after 10 minutes.
 * @returns The query result for the Markdown string.
 */
export function useHelpFlows() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.helpFlows,
    queryFn: ({ signal }) => api.help.flows(signal),
    staleTime: 10 * 60_000,
  });
}

/**
 * Fetches built-in and configured policy exceptions (`GET /policy-exceptions`, admin only).
 *
 * @remarks Key: `queryKeys.policyExceptions`.
 * @param enabled - Set `false` to skip fetching (e.g. for non-admins). Defaults to `true`.
 * @returns The query result for {@link PolicyExceptionList}.
 */
export function usePolicyExceptions(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.policyExceptions,
    queryFn: ({ signal }) => api.policyExceptions.list(signal),
    enabled,
  });
}

/**
 * Adds or updates a policy exception (`PUT /policy-exceptions`).
 *
 * @remarks
 * The backend rebuilds the mask and row-filter packs after saving, so this invalidates
 * `queryKeys.policyExceptions`, `queryKeys.maskRulePack`, and `queryKeys.rowFilterPack` on success.
 *
 * @returns A mutation taking an {@link UpsertPolicyExceptionRequest}.
 */
export function useUpsertPolicyException() {
  const api = useApi();
  const invalidate = useInvalidatePolicyExceptionState();
  return useMutation({
    meta: { busyMessage: "Saving exception…" },
    mutationFn: (body: UpsertPolicyExceptionRequest) => api.policyExceptions.upsert(body),
    onSuccess: invalidate,
  });
}

/**
 * Removes a configured policy exception (`DELETE /policy-exceptions/{id}`).
 *
 * @remarks
 * The backend rebuilds the mask and row-filter packs after deleting, so this invalidates
 * `queryKeys.policyExceptions`, `queryKeys.maskRulePack`, and `queryKeys.rowFilterPack` on success.
 *
 * @returns A mutation taking the exception id.
 */
export function useDeletePolicyException() {
  const api = useApi();
  const invalidate = useInvalidatePolicyExceptionState();
  return useMutation({
    meta: { busyMessage: "Removing exception…" },
    mutationFn: (exceptionId: string) => api.policyExceptions.delete(exceptionId),
    onSuccess: invalidate,
  });
}

/**
 * Returns a callback that invalidates the exception list and both policy pack status queries.
 *
 * @returns A function suitable for a mutation's `onSuccess`.
 */
function useInvalidatePolicyExceptionState() {
  const client = useQueryClient();
  return () => {
    void client.invalidateQueries({ queryKey: queryKeys.policyExceptions });
    void client.invalidateQueries({ queryKey: queryKeys.maskRulePack });
    void client.invalidateQueries({ queryKey: queryKeys.rowFilterPack });
  };
}

/**
 * Rebuilds the mask and row-filter packs with the current exceptions (`POST /policy-exceptions/apply`).
 *
 * @remarks Invalidates `queryKeys.policyExceptions`, `queryKeys.maskRulePack`, and `queryKeys.rowFilterPack` on success.
 * @returns A mutation with no variables, resolving to an {@link ApplyPolicyExceptionsResult}.
 */
export function useApplyPolicyExceptions() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    meta: { busyMessage: "Applying exceptions…" },
    mutationFn: () => api.policyExceptions.apply(),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: queryKeys.policyExceptions });
      void client.invalidateQueries({ queryKey: queryKeys.maskRulePack });
      void client.invalidateQueries({ queryKey: queryKeys.rowFilterPack });
    },
  });
}
