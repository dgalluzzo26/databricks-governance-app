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
  UpsertRowFilterDimensionRequest,
} from "./types";

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
};

export function useCurrentUser() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => api.identity.me(signal),
    staleTime: 5 * 60_000,
  });
}

export function useAppInfo() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.appInfo,
    queryFn: ({ signal }) => api.appInfo.get(signal),
    staleTime: 30_000,
  });
}

export function usePolicies(params: PolicyListParams = {}) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.policies(params),
    queryFn: ({ signal }) => api.policies.list(params, signal),
    staleTime: 30_000,
  });
}

export function usePolicy(policyId: string | undefined) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.policy(policyId ?? ""),
    queryFn: ({ signal }) => api.policies.get(policyId!, signal),
    enabled: Boolean(policyId),
    staleTime: 30_000,
  });
}

export function useGovernedTags(q?: string) {
  const api = useApi();
  return useQuery({
    queryKey: ["governed-tags", q ?? ""] as const,
    queryFn: ({ signal }) => api.identityAttributes.listGovernedTags(q, signal),
    staleTime: 60_000,
  });
}

export function useAttributeDefinitions() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.attributeDefinitions,
    queryFn: ({ signal }) => api.identityAttributes.listDefinitions(signal),
    staleTime: 30_000,
  });
}

export function useAttributeAssignments(params: AssignmentListParams = {}) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.attributeAssignments(params),
    queryFn: ({ signal }) => api.identityAttributes.listAssignments(params, signal),
    staleTime: 15_000,
  });
}

export function useMyAttributes() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.myAttributes,
    queryFn: ({ signal }) => api.identityAttributes.myAssignments(signal),
    staleTime: 15_000,
  });
}

export function useUpsertAttributeDefinition() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: UpsertAttributeDefinitionRequest) => api.identityAttributes.upsertDefinition(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
  });
}

export function useDeleteAttributeDefinition() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.identityAttributes.deleteDefinition(key),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
  });
}

export function useAssignAttribute() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: AssignAttributeRequest) => api.identityAttributes.assign(body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

export function useRevokeAttribute() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (assignmentId: string) => api.identityAttributes.revoke(assignmentId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

export function useCategoryApprovers() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.categoryApprovers,
    queryFn: ({ signal }) => api.identityAttributes.listCategoryApprovers(undefined, signal),
    staleTime: 15_000,
  });
}

export function useUpsertCategoryApprover() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: UpsertCategoryApproverRequest) => api.identityAttributes.upsertCategoryApprover(body),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.categoryApprovers }),
  });
}

export function useDeleteCategoryApprover() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (approverId: string) => api.identityAttributes.deleteCategoryApprover(approverId),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.categoryApprovers }),
  });
}

export function useMyAccessRequests() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.myAccessRequests,
    queryFn: ({ signal }) => api.identityAttributes.myAccessRequests(signal),
    staleTime: 15_000,
  });
}

export function useCreateAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateAccessRequestBody) => api.identityAttributes.createAccessRequest(body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

export function useCancelAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (requestId: string) => api.identityAttributes.cancelAccessRequest(requestId),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: queryKeys.approvals });
    },
  });
}

export function useApprovalsInbox() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.approvals,
    queryFn: ({ signal }) => api.identityAttributes.approvalsInbox(signal),
    staleTime: 15_000,
  });
}

export function useApproveAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, body }: { requestId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.approveAccessRequest(requestId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

export function useDenyAccessRequest() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ requestId, body }: { requestId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.denyAccessRequest(requestId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: queryKeys.myAccessRequests });
    },
  });
}

export function useApproveRenewal() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ assignmentId, body }: { assignmentId: string; body?: DecideAccessRequestBody }) =>
      api.identityAttributes.approveRenewal(assignmentId, body),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: queryKeys.approvals });
      client.invalidateQueries({ queryKey: ["identity-assignments"] });
    },
  });
}

export function useDismissRenewal() {
  const api = useApi();
  const client = useQueryClient();
  return useMutation({
    mutationFn: (assignmentId: string) => api.identityAttributes.dismissRenewal(assignmentId),
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.approvals }),
  });
}

export function useRowFilterDimensions() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterDimensions,
    queryFn: ({ signal }) => api.rowFilters.listDimensions(signal),
  });
}

export function useRowFilterPack(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterPack,
    queryFn: ({ signal }) => api.rowFilters.packStatus(signal),
    enabled,
  });
}

export function useRowFilterAttachments() {
  const api = useApi();
  return useQuery({
    queryKey: queryKeys.rowFilterAttachments,
    queryFn: ({ signal }) => api.rowFilters.listAttachments(signal),
  });
}

function useInvalidateRowFilters() {
  const client = useQueryClient();
  return () =>
    Promise.all([
      client.invalidateQueries({ queryKey: ["row-filters"] }),
      client.invalidateQueries({ queryKey: queryKeys.attributeDefinitions }),
    ]);
}

export function useUpsertRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (body: UpsertRowFilterDimensionRequest) => api.rowFilters.upsertDimension(body),
    onSuccess: invalidate,
  });
}

export function useDeleteRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (key: string) => api.rowFilters.deleteDimension(key),
    onSuccess: invalidate,
  });
}

export function useRefreshRowFilterDimension() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (key: string) => api.rowFilters.refreshDimension(key),
    onSuccess: invalidate,
  });
}

export function usePreviewSeed() {
  const api = useApi();
  return useMutation({ mutationFn: (valueSql: string) => api.rowFilters.previewSeed(valueSql) });
}

export function useEnsureRowFilterPack() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({ mutationFn: () => api.rowFilters.ensurePack(), onSuccess: invalidate });
}

export function useAttachRowFilter() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: (body: AttachRowFilterRequest) => api.rowFilters.attach(body),
    onSuccess: invalidate,
  });
}

export function useDetachRowFilter() {
  const api = useApi();
  const invalidate = useInvalidateRowFilters();
  return useMutation({
    mutationFn: ({ tableFullName, columnName }: { tableFullName: string; columnName: string }) =>
      api.rowFilters.detach(tableFullName, columnName),
    onSuccess: invalidate,
  });
}
