export interface CurrentUser {
  userName: string;
  email: string;
  displayName: string;
  userId: string | null;
  groups: string[];
  isAdmin: boolean;
  isApprover: boolean;
  workspaceHost: string | null;
}

export interface StorageHealth {
  backend: string;
  healthy: boolean;
  detail: string | null;
}

export interface AppInfo {
  appName: string;
  version: string;
  workspaceHost: string | null;
  authMode: string;
  warehouseId: string | null;
  appCatalog: string;
  appSchema: string;
  adminGroup: string;
  adminSecretScope: string;
  adminSpConfigured: boolean;
  storage: StorageHealth;
}

export type PolicyScopeKind = "metastore" | "catalog";

export interface TagRef {
  key: string;
  value: string | null;
}

export interface MatchColumnInfo {
  alias: string;
  condition: string | null;
}

export interface ColumnMaskInfo {
  functionName: string;
  onColumn: string;
}

export interface RowFilterInfo {
  functionName: string;
  using: string[];
}

export interface GrantDenyInfo {
  privileges: string[];
}

export interface AffectedAsset {
  assetType: string;
  fullName: string;
  catalogName: string | null;
  schemaName: string | null;
  tableName: string | null;
  columnName: string | null;
  matchedTagKey: string | null;
  matchedTagValue: string | null;
}

export interface PolicySummary {
  id: string;
  name: string;
  policyType: string;
  scopeKind: PolicyScopeKind;
  onSecurableType: string;
  onSecurableFullname: string;
  catalogName: string | null;
  forSecurableType: string | null;
  toPrincipals: string[];
  exceptPrincipals: string[];
  whenCondition: string | null;
  governedTags: TagRef[];
  privileges: string[];
  createdBy: string | null;
}

export interface PolicyDetail extends PolicySummary {
  comment: string | null;
  matchColumns: MatchColumnInfo[];
  columnMask: ColumnMaskInfo | null;
  rowFilter: RowFilterInfo | null;
  grant: GrantDenyInfo | null;
  deny: GrantDenyInfo | null;
  createdAt: number | null;
  updatedAt: number | null;
  updatedBy: string | null;
  affectedAssets: AffectedAsset[];
  affectedAssetsTruncated: boolean;
  affectedAssetsNote: string | null;
}

export interface PolicyListResponse {
  metastoreName: string | null;
  metastore: PolicySummary[];
  catalog: PolicySummary[];
  total: number;
}

export interface PolicyListParams {
  scope?: PolicyScopeKind;
  policyType?: string;
  catalog?: string;
  q?: string;
}

export type PrincipalKind = "USER" | "GROUP" | "SERVICE_PRINCIPAL";

export interface GovernedTagSummary {
  tagKey: string;
  description: string | null;
  values: string[];
  valueCount: number;
}

export interface GovernedTagList {
  tags: GovernedTagSummary[];
  total: number;
  truncated: boolean;
}

export type ValueSource = "governed_tags" | "free_form" | "sql";

export interface AttributeDefinition {
  key: string;
  description: string | null;
  governedTagKeys: string[];
  valueSource: ValueSource;
  valueSql: string | null;
  /** Governed tags: resolved live. Free-form / SQL: stored, always including "*". */
  allowedValues: string[];
  createdBy: string | null;
  createdAt: string | null;
  updatedBy: string | null;
  updatedAt: string | null;
}

export interface AttributeDefinitionList {
  definitions: AttributeDefinition[];
  total: number;
}

export interface UpsertAttributeDefinitionRequest {
  key?: string | null;
  description?: string | null;
  valueSource?: ValueSource;
  governedTagKeys: string[];
  allowedValues?: string[];
  valueSql?: string | null;
}

export interface RowFilterDimension {
  key: string;
  description: string | null;
  valueSource: ValueSource;
  valueSql: string | null;
  allowedValueCount: number;
  sampleValues: string[];
  attachmentCount: number;
}

export interface RowFilterDimensionList {
  dimensions: RowFilterDimension[];
  total: number;
}

export interface UpsertRowFilterDimensionRequest {
  key: string;
  description?: string | null;
  valueSource: "free_form" | "sql";
  allowedValues?: string[];
  valueSql?: string | null;
}

export interface SeedPreview {
  values: string[];
  total: number;
}

export interface RowFilterPackStatus {
  tagKey: string;
  tagValues: string[];
  functionName: string;
  policyName: string;
  policyExists: boolean;
  policyMatchColumns: string | null;
  policyExceptPrincipals: string[];
  dimensions: string[];
  missingTagValues: string[];
  inSync: boolean;
}

export interface AttachRowFilterRequest {
  tableFullName: string;
  columnName: string;
  dimension: string;
}

export interface RowFilterAttachment {
  tableFullName: string;
  columnName: string;
  dimension: string;
}

export interface RowFilterAttachmentList {
  attachments: RowFilterAttachment[];
  total: number;
}

export interface IdentityAttributeAssignment {
  assignmentId: string;
  principalName: string;
  principalType: PrincipalKind;
  attributeKey: string;
  attributeValue: string;
  source: string;
  grantedBy: string | null;
  justification: string | null;
  expiresAt: string | null;
  createdAt: string;
  revokedAt: string | null;
  revokedBy: string | null;
  active: boolean;
}

export interface AttributeAssignmentList {
  assignments: IdentityAttributeAssignment[];
  total: number;
}

export interface AssignAttributeRequest {
  principalName: string;
  principalType: PrincipalKind;
  attributeKey: string;
  attributeValue: string;
  justification?: string | null;
  expiresAt?: string | null;
}

export interface AssignmentListParams {
  principalName?: string;
  principalType?: PrincipalKind;
  attributeKey?: string;
  includeInactive?: boolean;
}

export type AccessRequestStatus = "PENDING" | "APPROVED" | "DENIED" | "CANCELLED";
export type ApprovalItemKind = "REQUEST" | "RENEWAL";
export type RequestRouteKind = "category_approvers" | "app_admins";

export interface ApproverPreview {
  principalName: string;
  principalType: string;
}

export interface CategoryApprover {
  approverId: string;
  categoryPath: string;
  principalName: string;
  principalType: PrincipalKind;
  createdBy: string | null;
  createdAt: string;
}

export interface CategoryApproverList {
  approvers: CategoryApprover[];
  total: number;
}

export interface PolicyException {
  exceptionId: string;
  principalName: string;
  principalType: PrincipalKind;
  note: string | null;
  createdBy: string | null;
  createdAt: string;
  system: boolean;
}

export interface PolicyExceptionList {
  exceptions: PolicyException[];
  total: number;
  systemPrincipals: string[];
}

export interface UpsertPolicyExceptionRequest {
  principalName: string;
  principalType: PrincipalKind;
  note?: string | null;
}

export interface ApplyPolicyExceptionsResult {
  exceptPrincipals: string[];
  maskPackSynced: boolean;
  rowFilterPackSynced: boolean;
}

export interface UpsertCategoryApproverRequest {
  categoryPath: string;
  principalName: string;
  principalType: PrincipalKind;
}

export interface AccessRequest {
  requestId: string;
  requesterName: string;
  attributeKey: string;
  attributeValue: string;
  requestedExpiresAt: string;
  justification: string;
  status: AccessRequestStatus;
  decidedBy: string | null;
  decidedAt: string | null;
  decisionNote: string | null;
  approvedExpiresAt: string | null;
  createdAt: string;
  resolvedCategory: string | null;
  routedTo: RequestRouteKind | null;
  approvers: ApproverPreview[];
}

export interface AccessRequestList {
  requests: AccessRequest[];
  total: number;
}

export interface CreateAccessRequestBody {
  attributeKey?: string;
  attributeValue: string;
  requestedExpiresAt: string;
  justification: string;
}

export interface DecideAccessRequestBody {
  expiresAt?: string | null;
  note?: string | null;
}

export interface ApprovalInboxItem {
  kind: ApprovalItemKind;
  request: AccessRequest | null;
  assignment: IdentityAttributeAssignment | null;
  resolvedCategory: string | null;
  attributeValue: string;
  principalName: string;
  expiresAt: string | null;
}

export interface ApprovalInbox {
  items: ApprovalInboxItem[];
  total: number;
}


export type MaskHandler = "full" | "joinable" | "email" | "phone" | "keep_last_n";
export type MaskRuleKind = "builtin" | "custom";

export interface MaskRuleDefinition {
  ruleKey: string;
  description: string | null;
  kind: MaskRuleKind;
  handler: string;
  expression: string | null;
  params: Record<string, unknown>;
  validatedAt: string | null;
  updatedBy: string | null;
  updatedAt: string | null;
}

export interface MaskRuleList {
  rules: MaskRuleDefinition[];
  total: number;
}

export interface UpsertMaskRuleRequest {
  ruleKey: string;
  description?: string | null;
  kind?: MaskRuleKind;
  handler?: MaskHandler | string | null;
  expression?: string | null;
  params?: Record<string, unknown>;
  validationToken?: string | null;
}

export interface MaskHandlerDraftRequest {
  prompt: string;
  expression?: string | null;
  errors?: string[];
}

export interface MaskHandlerDraft {
  expression: string;
  explanation: string;
  sampleInputs: string[];
}

export interface MaskHandlerValidateRequest {
  expression: string;
  sampleInputs?: string[];
}

export interface MaskHandlerSampleResult {
  input: string;
  output: string | null;
  error: string | null;
}

export interface MaskHandlerValidateResult {
  ok: boolean;
  expression: string;
  samples: MaskHandlerSampleResult[];
  validationToken: string | null;
}

export interface MaskingConfigStatus {
  joinableSaltConfigured: boolean;
  tagKey: string;
  tagValues: string[];
  missingTagValues: string[];
  policyName: string;
  policyExists: boolean;
  policyPassesMaskRule: boolean;
  functionName: string;
  udfInSync: boolean;
  inSync: boolean;
}

export interface MaskRulePackStatus {
  config: MaskingConfigStatus;
  rules: MaskRuleDefinition[];
}


export type ColumnAccessState = "unmasked" | "masked" | "not_governed" | "geo_masked";
export type SuggestedAttributeKind = "sensitivity" | "row_filter";

export interface TableSearchHit {
  catalogName: string;
  schemaName: string;
  tableName: string;
}

export interface TableSearchResult {
  tables: TableSearchHit[];
  total: number;
}

export interface ColumnTagMap {
  sensitivityCategory: string | null;
  sensitivity: string | null;
  maskRule: string | null;
  dataType: string | null;
  rowFilter: string | null;
}

export interface ColumnAccessInsight {
  columnName: string;
  dataType: string | null;
  tags: ColumnTagMap;
  accessState: ColumnAccessState;
  requiredSensitivityPath: string | null;
  userHasAccess: boolean;
  maskRuleKey: string | null;
  maskHandler: string | null;
  maskKind: string | null;
  maskDescription: string | null;
  policyName: string | null;
  notes: string | null;
}

export interface RowFilterInsight {
  attached: boolean;
  dimensionKey: string | null;
  columnName: string | null;
  policyName: string | null;
  functionName: string | null;
  userHasWildcard: boolean;
  userValues: string[];
  userCanSeeRows: boolean;
  notes: string | null;
}

export interface SuggestedAttribute {
  kind: SuggestedAttributeKind;
  attributeKey: string;
  attributeValue: string;
  reason: string;
  requestable: boolean;
  requestPath: string | null;
  columnNames: string[];
  coversPaths: string[];
  isHierarchyParent: boolean;
  routedTo: RequestRouteKind | null;
  resolvedCategory: string | null;
  approvers: ApproverPreview[];
}

export interface TableAccessInsight {
  tableFullName: string;
  catalogName: string;
  schemaName: string;
  tableName: string;
  columns: ColumnAccessInsight[];
  rowFilter: RowFilterInsight;
  suggestions: SuggestedAttribute[];
  summary: string;
}

export interface AnalyzeTableRequest {
  tableFullName: string;
}

export interface TreeCatalogList {
  catalogs: { catalogName: string }[];
  total: number;
  truncated: boolean;
}

export interface TreeSchemaList {
  schemas: { catalogName: string; schemaName: string }[];
  total: number;
  truncated: boolean;
}

export interface TreeTableList {
  tables: { catalogName: string; schemaName: string; tableName: string; tableType: string }[];
  total: number;
  truncated: boolean;
}
