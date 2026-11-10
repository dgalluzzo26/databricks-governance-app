import { HttpClient } from "./httpClient";
import type {
  AccessRequest,
  AccessRequestList,
  AppInfo,
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
  PolicyDetail,
  PolicyListParams,
  PolicyListResponse,
  RowFilterAttachment,
  RowFilterAttachmentList,
  RowFilterDimension,
  RowFilterDimensionList,
  RowFilterPackStatus,
  SeedPreview,
  UpsertAttributeDefinitionRequest,
  UpsertCategoryApproverRequest,
  UpsertRowFilterDimensionRequest,
} from "./types";

function toQuery(params: Record<string, string | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export class IdentityApi {
  constructor(private readonly http: HttpClient) {}

  me(signal?: AbortSignal): Promise<CurrentUser> {
    return this.http.get<CurrentUser>("/me", signal);
  }
}

export class AppInfoApi {
  constructor(private readonly http: HttpClient) {}

  get(signal?: AbortSignal): Promise<AppInfo> {
    return this.http.get<AppInfo>("/app-info", signal);
  }
}

export class PoliciesApi {
  constructor(private readonly http: HttpClient) {}

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

  get(policyId: string, signal?: AbortSignal): Promise<PolicyDetail> {
    return this.http.get<PolicyDetail>(`/policies/${encodeURIComponent(policyId)}`, signal);
  }
}

export class IdentityAttributesApi {
  constructor(private readonly http: HttpClient) {}

  listGovernedTags(q?: string, signal?: AbortSignal): Promise<GovernedTagList> {
    return this.http.get<GovernedTagList>(`/identity/governed-tags${toQuery({ q, limit: "100" })}`, signal);
  }

  listDefinitions(signal?: AbortSignal): Promise<AttributeDefinitionList> {
    return this.http.get<AttributeDefinitionList>("/identity/definitions", signal);
  }

  upsertDefinition(body: UpsertAttributeDefinitionRequest): Promise<AttributeDefinition> {
    return this.http.put<AttributeDefinition>("/identity/definitions", body);
  }

  deleteDefinition(key: string): Promise<void> {
    return this.http.delete<void>(`/identity/definitions/${encodeURIComponent(key)}`);
  }

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

  myAssignments(signal?: AbortSignal): Promise<AttributeAssignmentList> {
    return this.http.get<AttributeAssignmentList>("/identity/assignments/me", signal);
  }

  assign(body: AssignAttributeRequest): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>("/identity/assignments", body);
  }

  revoke(assignmentId: string): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>(
      `/identity/assignments/${encodeURIComponent(assignmentId)}/revoke`,
    );
  }

  listCategoryApprovers(categoryPath?: string, signal?: AbortSignal): Promise<CategoryApproverList> {
    return this.http.get<CategoryApproverList>(
      `/identity/category-approvers${toQuery({ category_path: categoryPath })}`,
      signal,
    );
  }

  upsertCategoryApprover(body: UpsertCategoryApproverRequest): Promise<CategoryApprover> {
    return this.http.put<CategoryApprover>("/identity/category-approvers", body);
  }

  deleteCategoryApprover(approverId: string): Promise<void> {
    return this.http.delete<void>(`/identity/category-approvers/${encodeURIComponent(approverId)}`);
  }

  createAccessRequest(body: CreateAccessRequestBody): Promise<AccessRequest> {
    return this.http.post<AccessRequest>("/identity/access-requests", body);
  }

  myAccessRequests(signal?: AbortSignal): Promise<AccessRequestList> {
    return this.http.get<AccessRequestList>("/identity/access-requests/mine", signal);
  }

  cancelAccessRequest(requestId: string): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/cancel`,
    );
  }

  approvalsInbox(signal?: AbortSignal): Promise<ApprovalInbox> {
    return this.http.get<ApprovalInbox>("/identity/approvals", signal);
  }

  approveAccessRequest(requestId: string, body: DecideAccessRequestBody = {}): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/approve`,
      body,
    );
  }

  denyAccessRequest(requestId: string, body: DecideAccessRequestBody = {}): Promise<AccessRequest> {
    return this.http.post<AccessRequest>(
      `/identity/access-requests/${encodeURIComponent(requestId)}/deny`,
      body,
    );
  }

  approveRenewal(
    assignmentId: string,
    body: DecideAccessRequestBody = {},
  ): Promise<IdentityAttributeAssignment> {
    return this.http.post<IdentityAttributeAssignment>(
      `/identity/renewals/${encodeURIComponent(assignmentId)}/approve`,
      body,
    );
  }

  dismissRenewal(assignmentId: string): Promise<void> {
    return this.http.post<void>(`/identity/renewals/${encodeURIComponent(assignmentId)}/dismiss`);
  }
}

export class RowFiltersApi {
  constructor(private readonly http: HttpClient) {}

  listDimensions(signal?: AbortSignal): Promise<RowFilterDimensionList> {
    return this.http.get<RowFilterDimensionList>("/row-filters/dimensions", signal);
  }

  upsertDimension(body: UpsertRowFilterDimensionRequest): Promise<RowFilterDimension> {
    return this.http.put<RowFilterDimension>("/row-filters/dimensions", body);
  }

  deleteDimension(key: string): Promise<void> {
    return this.http.delete<void>(`/row-filters/dimensions/${encodeURIComponent(key)}`);
  }

  refreshDimension(key: string): Promise<RowFilterDimension> {
    return this.http.post<RowFilterDimension>(`/row-filters/dimensions/${encodeURIComponent(key)}/refresh`);
  }

  previewSeed(valueSql: string): Promise<SeedPreview> {
    return this.http.post<SeedPreview>("/row-filters/seed-preview", { valueSql });
  }

  packStatus(signal?: AbortSignal): Promise<RowFilterPackStatus> {
    return this.http.get<RowFilterPackStatus>("/row-filters/pack", signal);
  }

  ensurePack(): Promise<RowFilterPackStatus> {
    return this.http.post<RowFilterPackStatus>("/row-filters/pack/ensure");
  }

  listAttachments(signal?: AbortSignal): Promise<RowFilterAttachmentList> {
    return this.http.get<RowFilterAttachmentList>("/row-filters/attachments", signal);
  }

  attach(body: AttachRowFilterRequest): Promise<RowFilterAttachment> {
    return this.http.post<RowFilterAttachment>("/row-filters/attachments", body);
  }

  detach(tableFullName: string, columnName: string): Promise<void> {
    return this.http.delete<void>(
      `/row-filters/attachments${toQuery({ table_full_name: tableFullName, column_name: columnName })}`,
    );
  }
}

export interface ApiServices {
  identity: IdentityApi;
  appInfo: AppInfoApi;
  policies: PoliciesApi;
  identityAttributes: IdentityAttributesApi;
  rowFilters: RowFiltersApi;
}

export function createApiServices(http: HttpClient = new HttpClient()): ApiServices {
  return {
    identity: new IdentityApi(http),
    appInfo: new AppInfoApi(http),
    policies: new PoliciesApi(http),
    identityAttributes: new IdentityAttributesApi(http),
    rowFilters: new RowFiltersApi(http),
  };
}
