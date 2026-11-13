/**
 * @file Wire types for the governance backend API.
 *
 * Each interface is a camelCase mirror of a backend pydantic model (`backend/app/models`).
 * Backend `datetime` fields arrive as ISO-8601 strings; optional backend fields arrive as
 * `null` rather than being omitted.
 */

/** The signed-in user as resolved by the backend from the Databricks Apps proxy identity (`GET /me`). */
export interface CurrentUser {
  /** Workspace user name (usually the email address); used as the principal name in assignments and requests. */
  userName: string;
  email: string;
  displayName: string;
  /** Databricks SCIM user id, when the backend could resolve it. */
  userId: string | null;
  /** Workspace group names the user belongs to. */
  groups: string[];
  /** Member of the configured app admin group; unlocks admin-only pages and endpoints. */
  isAdmin: boolean;
  /** Can decide access requests: always `true` for admins, otherwise `true` when the user or one of their groups is a category approver. */
  isApprover: boolean;
  /** Databricks workspace host URL, when known. */
  workspaceHost: string | null;
}

/** Health of the app's primary storage backend. */
export interface StorageHealth {
  /** Storage backend kind (e.g. Lakebase, Delta, or in-memory). */
  backend: string;
  healthy: boolean;
  /** Storage location when healthy, or the error reason when unhealthy. */
  detail: string | null;
}

/** Deployment and configuration summary (`GET /app-info`, admin only). */
export interface AppInfo {
  appName: string;
  version: string;
  workspaceHost: string | null;
  /** How the backend authenticates to Databricks (backend `AUTH_MODE`). */
  authMode: string;
  /** SQL warehouse used for UC queries; `null` when not configured. */
  warehouseId: string | null;
  /** Unity Catalog catalog holding the app's mirror tables and UDFs. */
  appCatalog: string;
  /** Unity Catalog schema holding the app's mirror tables and UDFs. */
  appSchema: string;
  /** Workspace group whose members are app admins. */
  adminGroup: string;
  /** Secret scope holding the admin service principal credentials. */
  adminSecretScope: string;
  /** Whether admin service principal credentials are available. */
  adminSpConfigured: boolean;
  storage: StorageHealth;
}

/** Where an ABAC policy is attached: the metastore itself or a specific catalog. */
export type PolicyScopeKind = "metastore" | "catalog";

/** A governed tag reference on a policy; `value` is `null` when the policy matches on tag key only. */
export interface TagRef {
  key: string;
  value: string | null;
}

/** A `MATCH COLUMNS` entry of an ABAC policy. */
export interface MatchColumnInfo {
  /** Alias the policy uses to refer to the matched column. */
  alias: string;
  /** Tag condition expression the column must satisfy; `null` when unconditional. */
  condition: string | null;
}

/** Column-mask clause of an ABAC policy. */
export interface ColumnMaskInfo {
  /** Fully qualified mask UDF name. */
  functionName: string;
  /** Match-column alias the mask is applied to. */
  onColumn: string;
}

/** Row-filter clause of an ABAC policy. */
export interface RowFilterInfo {
  /** Fully qualified row-filter UDF name. */
  functionName: string;
  /** Match-column aliases passed to the filter function, in order. */
  using: string[];
}

/** Privileges granted or denied by a GRANT/DENY policy. */
export interface GrantDenyInfo {
  privileges: string[];
}

/** A securable (table or column) that a policy currently matches via its governed tags. */
export interface AffectedAsset {
  /** Kind of securable, e.g. `TABLE` or `COLUMN`. */
  assetType: string;
  /** Dotted full name (`catalog.schema.table[.column]`). */
  fullName: string;
  catalogName: string | null;
  schemaName: string | null;
  tableName: string | null;
  /** Set only for column-level assets. */
  columnName: string | null;
  /** Tag key that caused the match, when known. */
  matchedTagKey: string | null;
  /** Tag value that caused the match, when known. */
  matchedTagValue: string | null;
}

/** List-view projection of a Unity Catalog ABAC policy. */
export interface PolicySummary {
  id: string;
  name: string;
  /** Upper-case policy type with the `POLICY_TYPE_` prefix stripped (e.g. `COLUMN_MASK`, `ROW_FILTER`), or `UNKNOWN`. */
  policyType: string;
  scopeKind: PolicyScopeKind;
  /** Upper-case securable type the policy is attached to (e.g. `METASTORE`, `CATALOG`). */
  onSecurableType: string;
  /** Full name of the securable the policy is attached to (metastore name for metastore-level policies). */
  onSecurableFullname: string;
  catalogName: string | null;
  /** Upper-case securable type the policy applies to (e.g. `TABLE`); `null` when unspecified. */
  forSecurableType: string | null;
  /** Principals the policy applies to (`TO` clause). */
  toPrincipals: string[];
  /** Principals exempt from the policy (`EXCEPT` clause). */
  exceptPrincipals: string[];
  /** `WHEN` condition SQL, if any. */
  whenCondition: string | null;
  governedTags: TagRef[];
  privileges: string[];
  createdBy: string | null;
}

/** Full policy details, including clauses and the assets it currently matches (`GET /policies/{id}`). */
export interface PolicyDetail extends PolicySummary {
  comment: string | null;
  matchColumns: MatchColumnInfo[];
  /** Present only for column-mask policies. */
  columnMask: ColumnMaskInfo | null;
  /** Present only for row-filter policies. */
  rowFilter: RowFilterInfo | null;
  /** Present only for GRANT policies. */
  grant: GrantDenyInfo | null;
  /** Present only for DENY policies. */
  deny: GrantDenyInfo | null;
  /** Creation time as epoch milliseconds (from the Databricks SDK), when available. */
  createdAt: number | null;
  /** Last-update time as epoch milliseconds (from the Databricks SDK), when available. */
  updatedAt: number | null;
  updatedBy: string | null;
  affectedAssets: AffectedAsset[];
  /** `true` when `affectedAssets` was capped by the backend's asset limit. */
  affectedAssetsTruncated: boolean;
  /** Explanation when the asset list is truncated or discovery is unsupported for this policy. */
  affectedAssetsNote: string | null;
}

/** Policies split into metastore-level and catalog-level buckets (`GET /policies`). */
export interface PolicyListResponse {
  metastoreName: string | null;
  metastore: PolicySummary[];
  catalog: PolicySummary[];
  /** Total across both buckets. */
  total: number;
}

/** Optional filters for `GET /policies`; unset or empty values are omitted from the query string. */
export interface PolicyListParams {
  scope?: PolicyScopeKind;
  /** Sent as `policy_type`. */
  policyType?: string;
  catalog?: string;
  /** Free-text search. */
  q?: string;
}

/** Kind of Databricks principal an attribute, approver, or exception targets. */
export type PrincipalKind = "USER" | "GROUP" | "SERVICE_PRINCIPAL";

/** A Unity Catalog governed tag and its allowed values. */
export interface GovernedTagSummary {
  tagKey: string;
  description: string | null;
  /** Allowed values defined by the tag policy (may be a subset; see `valueCount`). */
  values: string[];
  /** Total number of allowed values. */
  valueCount: number;
}

/** Governed tags matching a search (`GET /identity/governed-tags`). */
export interface GovernedTagList {
  tags: GovernedTagSummary[];
  total: number;
  /** `true` when the backend stopped at its result limit. */
  truncated: boolean;
}

/**
 * Where an attribute definition gets its allowed values: live from governed tags, from an
 * admin-maintained list, or from a seed SQL query.
 */
export type ValueSource = "governed_tags" | "free_form" | "sql";

/** An app-managed identity attribute (Unity Catalog cannot attach attributes to identities yet). */
export interface AttributeDefinition {
  /** Attribute key; for governed-tag definitions it defaults to the first governed tag key. */
  key: string;
  description: string | null;
  /** Governed tags whose values back this attribute; empty for free-form and SQL definitions. */
  governedTagKeys: string[];
  valueSource: ValueSource;
  /** Seed query for `valueSource: "sql"`; refreshed into `allowedValues`. `null` for other sources. */
  valueSql: string | null;
  /** Governed tags: resolved live. Free-form / SQL: stored, always including "*". */
  allowedValues: string[];
  createdBy: string | null;
  /** ISO-8601 timestamp. */
  createdAt: string | null;
  updatedBy: string | null;
  /** ISO-8601 timestamp. */
  updatedAt: string | null;
}

/** All attribute definitions (`GET /identity/definitions`). */
export interface AttributeDefinitionList {
  definitions: AttributeDefinition[];
  total: number;
}

/**
 * Body for `PUT /identity/definitions` (create or replace by key).
 *
 * @remarks
 * The backend requires at least one `governedTagKeys` entry for `governed_tags`, a `key` for
 * `free_form`/`sql`, and `valueSql` for `sql`. Lists are trimmed and de-duplicated server-side.
 */
export interface UpsertAttributeDefinitionRequest {
  /** Optional for governed-tag definitions (defaults to the first governed tag key). */
  key?: string | null;
  description?: string | null;
  /** Defaults to `"governed_tags"` on the backend. */
  valueSource?: ValueSource;
  governedTagKeys: string[];
  /** Used for `free_form` definitions. */
  allowedValues?: string[];
  /** Required when `valueSource` is `"sql"`; ignored otherwise. */
  valueSql?: string | null;
}

/** A row-filter dimension (e.g. `state`, `region`): an identity attribute used to filter rows on tagged columns. */
export interface RowFilterDimension {
  /** Dimension key; also the `row_filter` tag value on columns and the identity attribute key. */
  key: string;
  description: string | null;
  valueSource: ValueSource;
  valueSql: string | null;
  allowedValueCount: number;
  /** A few allowed values for display. */
  sampleValues: string[];
  /** Number of table columns currently tagged with this dimension. */
  attachmentCount: number;
}

/** All row-filter dimensions (`GET /row-filters/dimensions`). */
export interface RowFilterDimensionList {
  dimensions: RowFilterDimension[];
  total: number;
}

/** Body for `PUT /row-filters/dimensions` (create or replace by key). */
export interface UpsertRowFilterDimensionRequest {
  /** Must match `^[a-z][a-z0-9_]{0,62}$`; lower-cased server-side. */
  key: string;
  description?: string | null;
  /** Row-filter dimensions cannot be backed by governed tags. */
  valueSource: "free_form" | "sql";
  /** Used for `free_form` dimensions. */
  allowedValues?: string[];
  /** Seed query for `sql` dimensions. */
  valueSql?: string | null;
}

/** Result of dry-running a seed SQL query (`POST /row-filters/seed-preview`). */
export interface SeedPreview {
  /** Distinct values the query returned (possibly a sample). */
  values: string[];
  total: number;
}

/** Sync state of the metastore row-filter "pack": the `row_filter` tag, its UDF, and the ABAC policy. */
export interface RowFilterPackStatus {
  /** Governed tag key used to mark filtered columns (e.g. `row_filter`). */
  tagKey: string;
  /** Values currently allowed on the governed tag. */
  tagValues: string[];
  /** Fully qualified row-filter UDF name. */
  functionName: string;
  policyName: string;
  policyExists: boolean;
  /** The policy's current `MATCH COLUMNS` condition, if the policy exists. */
  policyMatchColumns: string | null;
  /** Principals in the policy's `EXCEPT` clause. */
  policyExceptPrincipals: string[];
  /** Dimension keys the pack should cover. */
  dimensions: string[];
  /** Dimension keys not yet present as governed tag values. */
  missingTagValues: string[];
  /** `true` when tag values, UDF, and policy all match the current dimensions. */
  inSync: boolean;
}

/** Body for `POST /row-filters/attachments`: tag a column so it is filtered by a dimension. */
export interface AttachRowFilterRequest {
  /** `catalog.schema.table`. */
  tableFullName: string;
  columnName: string;
  /** Dimension key (same pattern as {@link UpsertRowFilterDimensionRequest.key}). */
  dimension: string;
}

/** A column tagged with a row-filter dimension. */
export interface RowFilterAttachment {
  /** `catalog.schema.table`. */
  tableFullName: string;
  columnName: string;
  /** Dimension key. */
  dimension: string;
}

/** All row-filter attachments (`GET /row-filters/attachments`). */
export interface RowFilterAttachmentList {
  attachments: RowFilterAttachment[];
  total: number;
}

/** A record that a principal holds one value of an identity attribute. */
export interface IdentityAttributeAssignment {
  assignmentId: string;
  principalName: string;
  principalType: PrincipalKind;
  attributeKey: string;
  /** Held value; `"*"` grants every value (e.g. all rows for a row-filter dimension). */
  attributeValue: string;
  /** How the assignment was created: `MANUAL`, `ACCESS_REQUEST`, or `IDENTITY_POLICY`. */
  source: string;
  grantedBy: string | null;
  justification: string | null;
  /** ISO-8601 expiry; `null` means the assignment never expires. */
  expiresAt: string | null;
  /** ISO-8601 timestamp. */
  createdAt: string;
  /** ISO-8601 revocation time; `null` while not revoked. */
  revokedAt: string | null;
  revokedBy: string | null;
  /** Computed server-side: not revoked and not yet expired. */
  active: boolean;
}

/** A list of attribute assignments. */
export interface AttributeAssignmentList {
  assignments: IdentityAttributeAssignment[];
  total: number;
}

/** Body for `POST /identity/assignments` (admin grants an attribute value to a principal). */
export interface AssignAttributeRequest {
  principalName: string;
  principalType: PrincipalKind;
  attributeKey: string;
  attributeValue: string;
  justification?: string | null;
  /** ISO-8601 expiry; omit or `null` for no expiry. */
  expiresAt?: string | null;
}

/** Optional filters for `GET /identity/assignments`; sent as snake_case query parameters. */
export interface AssignmentListParams {
  principalName?: string;
  principalType?: PrincipalKind;
  attributeKey?: string;
  /** Include revoked and expired assignments (default `false`). */
  includeInactive?: boolean;
}

/** Lifecycle state of an access request. */
export type AccessRequestStatus = "PENDING" | "APPROVED" | "DENIED" | "CANCELLED";
/** Approval inbox entry kind: a new access request, or an expiring assignment due for renewal. */
export type ApprovalItemKind = "REQUEST" | "RENEWAL";
/** Who reviews a request: the approvers of its sensitivity category, or app admins as the fallback. */
export type RequestRouteKind = "category_approvers" | "app_admins";

/** A principal who can decide a request, shown to the requester. */
export interface ApproverPreview {
  principalName: string;
  /** Principal kind as a string (typically a {@link PrincipalKind} value). */
  principalType: string;
}

/** A principal allowed to decide sensitivity requests under a category path. */
export interface CategoryApprover {
  approverId: string;
  /** `sensitivity` for category routing, or a row-filter dimension key. */
  attributeKey: string;
  /**
   * For sensitivity: dotted category path prefix (most specific match wins).
   * For row filters: the dimension key (exact match).
   */
  categoryPath: string;
  principalName: string;
  principalType: PrincipalKind;
  createdBy: string | null;
  /** ISO-8601 timestamp. */
  createdAt: string;
}

/** Category approvers (`GET /identity/category-approvers`). */
export interface CategoryApproverList {
  approvers: CategoryApprover[];
  total: number;
}

/** A principal exempt from every app-managed mask and row-filter policy (added to their `EXCEPT` lists). */
export interface PolicyException {
  /** Stable id; built-in system exceptions use `system:<principalName>`. */
  exceptionId: string;
  principalName: string;
  principalType: PrincipalKind;
  note: string | null;
  createdBy: string | null;
  /** ISO-8601 timestamp. */
  createdAt: string;
  /** `true` for built-in app/admin service principals, which cannot be edited or deleted. */
  system: boolean;
}

/** Built-in plus configured policy exceptions (`GET /policy-exceptions`). */
export interface PolicyExceptionList {
  /** System exceptions first, then configured ones (duplicates of system principals are hidden). */
  exceptions: PolicyException[];
  total: number;
  /** Principal names of the built-in system exceptions. */
  systemPrincipals: string[];
}

/** Body for `PUT /policy-exceptions`. */
export interface UpsertPolicyExceptionRequest {
  principalName: string;
  principalType: PrincipalKind;
  note?: string | null;
}

/** Result of rebuilding the mask and row-filter packs with the current exception list (`POST /policy-exceptions/apply`). */
export interface ApplyPolicyExceptionsResult {
  /** Ordered principal names now in the policies' `EXCEPT` clauses. */
  exceptPrincipals: string[];
  maskPackSynced: boolean;
  rowFilterPackSynced: boolean;
}

/** Body for `PUT /identity/category-approvers`. Values are trimmed and must be non-empty. */
export interface UpsertCategoryApproverRequest {
  /** `sensitivity` (default) or a row-filter dimension key. */
  attributeKey?: string;
  categoryPath: string;
  principalName: string;
  principalType: PrincipalKind;
}

/** A user's request to be granted an identity attribute value (typically a sensitivity path). */
export interface AccessRequest {
  requestId: string;
  requesterName: string;
  attributeKey: string;
  attributeValue: string;
  /** ISO-8601 expiry the requester asked for. */
  requestedExpiresAt: string;
  justification: string;
  status: AccessRequestStatus;
  decidedBy: string | null;
  /** ISO-8601 decision time; `null` while pending. */
  decidedAt: string | null;
  decisionNote: string | null;
  /** ISO-8601 expiry set by the approver; may differ from `requestedExpiresAt`. */
  approvedExpiresAt: string | null;
  /** ISO-8601 timestamp. */
  createdAt: string;
  /** Configured approver category the request routed to (sensitivity requests only). */
  resolvedCategory: string | null;
  routedTo: RequestRouteKind | null;
  /** Principals who can decide this request. */
  approvers: ApproverPreview[];
}

/** A list of access requests. */
export interface AccessRequestList {
  requests: AccessRequest[];
  total: number;
}

/** Body for `POST /identity/access-requests`. */
export interface CreateAccessRequestBody {
  /** Defaults to `"sensitivity"` on the backend; lower-cased server-side. */
  attributeKey?: string;
  attributeValue: string;
  /** ISO-8601 expiry; must be in the future. */
  requestedExpiresAt: string;
  justification: string;
}

/** Optional body for approving/denying a request or approving a renewal. */
export interface DecideAccessRequestBody {
  /** ISO-8601 expiry override; omitted means keep the requested (or current) expiry. */
  expiresAt?: string | null;
  /** Decision note, up to 2000 characters. */
  note?: string | null;
}

/**
 * One entry in the caller's approval inbox.
 *
 * @remarks
 * Exactly one of `request` (for `REQUEST`) or `assignment` (for `RENEWAL`) is set.
 */
export interface ApprovalInboxItem {
  kind: ApprovalItemKind;
  request: AccessRequest | null;
  assignment: IdentityAttributeAssignment | null;
  resolvedCategory: string | null;
  attributeValue: string;
  /** Requester (for requests) or assignment holder (for renewals). */
  principalName: string;
  /** Requested expiry (for requests) or current expiry (for renewals), ISO-8601. */
  expiresAt: string | null;
}

/** Pending requests and due renewals the caller may decide (`GET /identity/approvals`). */
export interface ApprovalInbox {
  items: ApprovalInboxItem[];
  total: number;
}


/** Builtin mask algorithm ids. Custom handlers use their rule key as the handler id. */
export type MaskHandler = "full" | "joinable" | "email" | "phone" | "keep_last_n";
/** Whether a mask rule uses a builtin handler or an admin-authored SQL expression. */
export type MaskRuleKind = "builtin" | "custom";

/**
 * A presentation mask rule: a `mask_rule` tag value mapped to how a denied value is shown.
 *
 * @remarks
 * Authorization (masked vs unmasked) is decided by sensitivity tags; the mask rule only
 * chooses the denied presentation.
 */
export interface MaskRuleDefinition {
  /** Rule key; also the `mask_rule` tag value on columns. */
  ruleKey: string;
  description: string | null;
  kind: MaskRuleKind;
  /** Builtin {@link MaskHandler} id, or the rule key for custom rules. */
  handler: string;
  /** SQL expression over the input `v`, for custom rules; `null` for builtins. */
  expression: string | null;
  /** Handler parameters, e.g. `{ n: 4 }` for `keep_last_n`; empty for other handlers. */
  params: Record<string, unknown>;
  /** ISO-8601 time the custom expression was last validated. */
  validatedAt: string | null;
  updatedBy: string | null;
  /** ISO-8601 timestamp. */
  updatedAt: string | null;
}

/** All mask rules. */
export interface MaskRuleList {
  rules: MaskRuleDefinition[];
  total: number;
}

/**
 * Body for `PUT /mask-rules` (create or replace by rule key).
 *
 * @remarks
 * Custom rules require `expression` and a `validationToken` from a successful
 * validate call. Builtin rules default `handler` to `ruleKey`.
 */
export interface UpsertMaskRuleRequest {
  /** Must match `^[a-z][a-z0-9_]{0,62}$`; lower-cased server-side. */
  ruleKey: string;
  description?: string | null;
  /** Defaults to `"builtin"`. */
  kind?: MaskRuleKind;
  /** Builtin handler id; ignored for custom rules. */
  handler?: MaskHandler | string | null;
  /** Custom SQL expression over `v`; required when `kind` is `"custom"`. */
  expression?: string | null;
  /** Only `n` (1–64, default 4) for `keep_last_n` is honored. */
  params?: Record<string, unknown>;
  /** Token from {@link MaskHandlerValidateResult}; required for custom rules. */
  validationToken?: string | null;
}

/** Body for `POST /mask-rules/handlers/draft`: ask the LLM to write or fix a custom mask expression. */
export interface MaskHandlerDraftRequest {
  /** Natural-language description of the desired mask (3–4000 characters). */
  prompt: string;
  /** Current expression to revise, if any. */
  expression?: string | null;
  /** Validation errors from a previous attempt, fed back to the LLM. */
  errors?: string[];
}

/** An LLM-drafted custom mask expression. */
export interface MaskHandlerDraft {
  expression: string;
  explanation: string;
  /** Suggested sample inputs for validation (at most 10). */
  sampleInputs: string[];
}

/** Body for `POST /mask-rules/handlers/validate`: run an expression against sample inputs on the warehouse. */
export interface MaskHandlerValidateRequest {
  expression: string;
  /** Inputs to test; the backend uses default samples when empty. */
  sampleInputs?: string[];
}

/** Output of a mask expression for one sample input; exactly one of `output`/`error` is meaningful. */
export interface MaskHandlerSampleResult {
  input: string;
  /** Masked value; `null` if the expression returned NULL or errored. */
  output: string | null;
  /** SQL error for this input, if it failed. */
  error: string | null;
}

/** Result of validating a custom mask expression. */
export interface MaskHandlerValidateResult {
  /** `true` when every sample evaluated without error. */
  ok: boolean;
  /** The sanitized expression that was evaluated. */
  expression: string;
  samples: MaskHandlerSampleResult[];
  /** Issued only when `ok`; pass it to {@link UpsertMaskRuleRequest.validationToken} to save a custom rule. */
  validationToken: string | null;
}

/** Sync state of the metastore mask "pack": salt, `mask_rule` tag, `apply_mask_rule` UDF, and ABAC policy. */
export interface MaskingConfigStatus {
  /** Whether the salt used by the `joinable` (hash) handler exists. */
  joinableSaltConfigured: boolean;
  /** Governed tag key used on columns to pick a mask rule (e.g. `mask_rule`). */
  tagKey: string;
  /** Values currently allowed on the governed tag. */
  tagValues: string[];
  /** Rule keys not yet present as governed tag values. */
  missingTagValues: string[];
  policyName: string;
  policyExists: boolean;
  /** Whether the policy passes the column's mask rule to the UDF. */
  policyPassesMaskRule: boolean;
  /** Fully qualified mask UDF name. */
  functionName: string;
  /** Whether the UDF body includes every custom rule's handler. */
  udfInSync: boolean;
  /** `true` when every component is in sync. */
  inSync: boolean;
}

/** Mask pack status plus the current rules (`GET /mask-rules/pack`). */
export interface MaskRulePackStatus {
  config: MaskingConfigStatus;
  rules: MaskRuleDefinition[];
}


/**
 * How the current user sees a column: in the clear, masked, outside any app policy, or
 * returned as NULL by the geometry/geography policies.
 */
export type ColumnAccessState = "unmasked" | "masked" | "not_governed" | "geo_masked";
/** Kind of attribute the Access Explorer suggests requesting. */
export type SuggestedAttributeKind = "sensitivity" | "row_filter" | "data_access";

/** A user or group from the workspace directory (`GET /directory/principals`). */
export interface DirectoryPrincipal {
  name: string;
  displayName: string;
  principalType: PrincipalKind;
  email: string | null;
}

/** Directory search results for the principal picker. */
export interface DirectoryPrincipalList {
  principals: DirectoryPrincipal[];
  total: number;
  truncated: boolean;
}

/** A table matching an Access Explorer search. */
export interface TableSearchHit {
  catalogName: string;
  schemaName: string;
  tableName: string;
}

/** Tables whose `catalog.schema.table` name contains the query (`GET /access-explorer/search`). */
export interface TableSearchResult {
  tables: TableSearchHit[];
  total: number;
}

/** Governance tag values found on a column; each is `null` when the tag is absent. */
export interface ColumnTagMap {
  /** `sensitivity_category` tag (first segment of the required sensitivity path). */
  sensitivityCategory: string | null;
  /** `sensitivity` tag (second segment of the required sensitivity path). */
  sensitivity: string | null;
  /** `mask_rule` tag selecting the denied presentation; missing means `full`. */
  maskRule: string | null;
  /** `data_type` governance tag (not the SQL type); geometry/geography route to geo policies. */
  dataType: string | null;
  /** `row_filter` tag naming a row-filter dimension. */
  rowFilter: string | null;
}

/** Per-column explanation of how masking applies to the current user. */
export interface ColumnAccessInsight {
  columnName: string;
  /** SQL data type from `information_schema`, when known. */
  dataType: string | null;
  tags: ColumnTagMap;
  accessState: ColumnAccessState;
  /** `<sensitivity_category>.<sensitivity>` the user must hold to unmask; `null` when the column lacks those tags. */
  requiredSensitivityPath: string | null;
  /** Whether the user holds the required sensitivity path (or the column is untagged). */
  userHasAccess: boolean;
  /** Effective mask rule key (`full` when the tag is missing); `null` for ungoverned and geo columns. */
  maskRuleKey: string | null;
  maskHandler: string | null;
  /** {@link MaskRuleKind} as a string. */
  maskKind: string | null;
  maskDescription: string | null;
  /** ABAC policy that governs the column. */
  policyName: string | null;
  notes: string | null;
}

/** Row-filter state of the analyzed table for the current user. */
export interface RowFilterInsight {
  /** Whether a column on the table carries a `row_filter` tag. */
  attached: boolean;
  dimensionKey: string | null;
  columnName: string | null;
  policyName: string | null;
  functionName: string | null;
  /** User holds `"*"` for the dimension and sees every row. */
  userHasWildcard: boolean;
  /** Dimension values the user holds. */
  userValues: string[];
  /** Whether the user will see any rows at all (no assignment means no rows). */
  userCanSeeRows: boolean;
  notes: string | null;
}

/** An attribute the user could request to gain access to masked columns or filtered rows. */
export interface SuggestedAttribute {
  kind: SuggestedAttributeKind;
  attributeKey: string;
  attributeValue: string;
  /** Human-readable explanation of what the attribute would unlock. */
  reason: string;
  /** Whether a request can be submitted now (e.g. `false` when the dimension has no definition yet). */
  requestable: boolean;
  /** In-app route that pre-fills the request form, e.g. `/identity?tab=request&key=...&value=...`. */
  requestPath: string | null;
  /** Columns that would be unmasked or unfiltered. */
  columnNames: string[];
  /** Required sensitivity paths this value covers (`["*"]` for row-filter wildcards). */
  coversPaths: string[];
  /** `true` for a broader parent path covering several required leaf paths. */
  isHierarchyParent: boolean;
  routedTo: RequestRouteKind | null;
  resolvedCategory: string | null;
  approvers: ApproverPreview[];
}

/** Full Access Explorer analysis of a catalog, schema, or table (`POST /access-explorer/analyze`). */
export interface TableAccessInsight {
  tableFullName: string;
  /** `catalog`, `schema`, or `table`. */
  securableLevel?: "catalog" | "schema" | "table";
  catalogName: string;
  schemaName: string;
  tableName: string;
  /** Unity Catalog table type, e.g. `MANAGED`, `EXTERNAL`, or `VIEW`. */
  tableType?: string;
  columns: ColumnAccessInsight[];
  /** First row-filter insight (or a not-attached placeholder). */
  rowFilter: RowFilterInsight;
  /** Every row-filter dimension in effect; views can inherit several from their sources. */
  rowFilters?: RowFilterInsight[];
  /** Ordered data-access, sensitivity, then row-filter suggestions. */
  suggestions: SuggestedAttribute[];
  /** Human-readable summary of the analysis. */
  summary: string;
}

/** Body for `POST /access-explorer/analyze`. */
export interface AnalyzeTableRequest {
  /** `catalog`, `catalog.schema`, or `catalog.schema.table`. Preferred field. */
  fullName?: string;
  /** Legacy alias still accepted by the backend. */
  tableFullName?: string;
}

/** Catalogs for the Access Explorer tree (`GET /access-explorer/tree/catalogs`). */
export interface TreeCatalogList {
  catalogs: { catalogName: string }[];
  total: number;
  /** `true` when the backend stopped at its result limit. */
  truncated: boolean;
}

/** Schemas in one catalog for the Access Explorer tree. */
export interface TreeSchemaList {
  schemas: { catalogName: string; schemaName: string }[];
  total: number;
  /** `true` when the backend stopped at its result limit. */
  truncated: boolean;
}

/** Tables in one schema for the Access Explorer tree; `tableType` is the UC table type (e.g. `MANAGED`, `VIEW`). */
export interface TreeTableList {
  tables: { catalogName: string; schemaName: string; tableName: string; tableType: string }[];
  total: number;
  /** `true` when the backend stopped at its result limit. */
  truncated: boolean;
}

/** Legacy setting; SELECT is table-only so parent/child mismatches are not reported. */
export type DataAccessConflictPolicy = "hold" | "warn";

/** Admin-editable configuration for the data-access group dry-run. */
export interface DataAccessSettings {
  accessTagKey: string;
  reviewTagKey: string;
  reviewTagValue: string;
  groupPrefix: string;
  hierarchySeparator: string;
  /** Catalog tag key that enrolls a catalog in the governance perimeter. */
  enrollmentCatalogTagKey: string;
  /** Required value of the enrollment catalog tag. */
  enrollmentCatalogTagValue: string;
  /** @deprecated Ignored; perimeter is enrollment-tag discovery only. */
  allowedCatalogs: string[];
  dataPrivileges: string[];
  intervalMinutes: number;
  /** When false, the background scheduler is paused. */
  scheduleEnabled: boolean;
  /** What each scheduled tick does: plan only, or plan-then-apply. */
  scheduleMode: "dry_run" | "apply";
  conflictPolicy: DataAccessConflictPolicy;
  dryRun: boolean;
}

/** Settings plus constants the UI needs (`GET /data-access/settings`). */
export interface DataAccessSettingsView {
  settings: DataAccessSettings;
  minIntervalMinutes: number;
  applyAvailable: boolean;
  /** Catalogs currently carrying the enrollment tag (from UC). */
  enrolledCatalogs: string[];
}

/** One Account Admin SP / warehouse capability probe. */
export interface DataAccessReadinessCheck {
  id: string;
  label: string;
  ok: boolean;
  requiredFor: string;
  detail: string | null;
}

/** Rolled-up readiness for dry-run vs apply (`GET /data-access/readiness`). */
export interface DataAccessReadiness {
  dryRunReady: boolean;
  applyReady: boolean;
  checks: DataAccessReadinessCheck[];
  /** Identity definition setup (sensitivity + data-access). */
  setupChecks: DataAccessReadinessCheck[];
  /** True when both required Identity definitions are configured. */
  setupReady: boolean;
}

/** A managed group the dry-run would create or reuse. */
export interface DataAccessPlannedGroup {
  name: string;
  value: string;
  parentGroup: string | null;
  exists: boolean;
}

/** One privilege on one securable for one managed group. */
export interface DataAccessPlannedGrant {
  securableType: string;
  fullName: string;
  principal: string;
  privilege: string;
  reason: string;
}

/** Ancestor grant that would expose a descendant the tags do not allow. */
export interface DataAccessConflict {
  ancestorType: string;
  ancestorName: string;
  ancestorValue: string;
  descendantName: string;
  descendantValue: string | null;
  reason: string;
}

/** Counts for a dry-run / apply plan. */
export interface DataAccessPlanSummary {
  eligibleObjects: number;
  skippedUnreviewed: number;
  uniqueValues: number;
  groupsToCreate: number;
  groupsExisting: number;
  grantsToAdd: number;
  grantsToRevoke: number;
  grantsUnchanged: number;
  membersToAdd: number;
  membersToRemove: number;
  conflicts: number;
  invalidValues: number;
}

/** Identity-driven membership change for a managed group. */
export interface DataAccessMembershipChange {
  group: string;
  principalName: string;
  principalType: string;
  action: "add" | "remove";
  reason: string;
}

/** Truncated dry-run / apply plan. */
export interface DataAccessPlan {
  summary: DataAccessPlanSummary;
  fingerprint: string;
  groups: DataAccessPlannedGroup[];
  grantsToAdd: DataAccessPlannedGrant[];
  grantsToRevoke: DataAccessPlannedGrant[];
  membershipChanges: DataAccessMembershipChange[];
  conflicts: DataAccessConflict[];
  invalidValues: string[];
  truncated: boolean;
  driftSinceLastRun: string[];
  applyErrors: string[];
}

/** Attribute Explorer analyze request / response. */
export type AttributeExplorerKind = "data_access" | "sensitivity" | "row_filter" | "other";
export type AttributeHolderMatch = "exact" | "descendant" | "ancestor" | "related";

export interface AnalyzeAttributeRequest {
  attributeKey: string;
  /** Omit or empty for row-filter overview (all principals + values). */
  attributeValue?: string;
}

export interface AttributeHolder {
  principalName: string;
  principalType: PrincipalKind;
  attributeValue: string;
  match: AttributeHolderMatch;
  expiresAt: string | null;
  assignmentId: string;
}

/** Row-filter overview: one principal with every value they hold. */
export interface AttributePrincipalSummary {
  principalName: string;
  principalType: PrincipalKind;
  values: string[];
  expiresAt: string | null;
}

export interface UnlockedTable {
  fullName: string;
  tagValue: string;
  match: AttributeHolderMatch;
  groupName: string | null;
}

export interface AttributeExplorerResult {
  attributeKey: string;
  attributeValue: string;
  kind: AttributeExplorerKind;
  groupName: string | null;
  parentGroup: string | null;
  holders: AttributeHolder[];
  principals: AttributePrincipalSummary[];
  tables: UnlockedTable[];
  note: string | null;
}

/** One developer guide listed by `GET /help/docs`. */
export interface HelpDoc {
  slug: string;
  title: string;
  description: string;
  sourcePath: string;
}

/** Guides available in the Help page, in display order. */
export interface HelpDocList {
  docs: HelpDoc[];
}

/** Minimum caller role an endpoint requires. */
export type EndpointAccess = "public" | "user" | "admin";

/** A path or query parameter of a backend endpoint. */
export interface ApiParameter {
  name: string;
  location: "path" | "query";
  required: boolean;
  type: string;
  description: string | null;
}

/** One backend route, generated from the FastAPI route table. */
export interface ApiEndpoint {
  method: string;
  path: string;
  tag: string;
  summary: string;
  description: string;
  access: EndpointAccess;
  requestBody: string | null;
  responseModel: string | null;
  parameters: ApiParameter[];
  handler: string;
}

/** Every `/api` endpoint (`GET /help/api-reference`). */
export interface ApiReference {
  endpoints: ApiEndpoint[];
}

/** Paginated sync history (`GET /data-access/runs`). */
export interface DataAccessRunList {
  runs: DataAccessRun[];
  total: number;
  page: number;
  pageSize: number;
  mode: "dry_run" | "apply" | null;
  retentionDays: number;
}

/** One stored dry-run execution (`GET/POST /data-access/runs`). */
export interface DataAccessRun {
  runId: string;
  status: "running" | "success" | "failed";
  trigger: "schedule" | "user";
  mode: string;
  startedAt: string;
  finishedAt: string | null;
  triggeredBy: string;
  error: string | null;
  summary: DataAccessPlanSummary | null;
  plan: DataAccessPlan | null;
  fingerprint: string | null;
  /** Wall-clock seconds from start to finish; null while running. */
  durationSeconds?: number | null;
}

/** Git target for recovery exports. The token is never sent to the browser; only its secret key name. */
export interface RecoveryGitSettings {
  repoUrl: string;
  branch: string;
  pathPrefix: string;
  username: string;
  tokenSecretKey: string;
}

/** One YAML file a recovery push would commit. */
export interface RecoveryFile {
  path: string;
  records: number;
  content: string;
}

/** Dry-run recovery export (`POST /recovery/export/preview`). */
export interface RecoveryPreview {
  generatedAt: string;
  generatedBy: string;
  target: string;
  git: RecoveryGitSettings;
  files: RecoveryFile[];
  warnings: string[];
}

/** Result of pushing the export to Git (`POST /recovery/export/push`). The token is never included. */
export interface RecoveryPushResult {
  pushedAt: string;
  pushedBy: string;
  target: string;
  commitSha: string;
  committed: boolean;
  branchCreated: boolean;
  files: number;
}

/** Current app occupancy used to warn before recover. */
export interface RecoveryOccupancy {
  appHasState: boolean;
  definitions: number;
  assignments: number;
  approvers: number;
  maskRules: number;
  policyExceptions: number;
  dataAccessConfigured: boolean;
  summary: string;
}

/** Summary of the imported manifest.yaml. */
export interface RecoveryManifestSummary {
  version?: number | null;
  generatedAt?: string | null;
  generatedBy?: string | null;
}

/** Dry-run import from Git (`POST /recovery/import`). Writes nothing. */
export interface RecoveryImportPreview {
  importedAt: string;
  importedBy: string;
  source: string;
  commitSha: string;
  files: RecoveryFile[];
  warnings: string[];
  occupancy: RecoveryOccupancy;
  manifest: RecoveryManifestSummary;
}

/** Body for `POST /recovery/recover`. */
export interface RecoveryApplyRequest {
  confirm: boolean;
}

/** Counts written by recover. */
export interface RecoveryApplyCounts {
  definitions: number;
  assignments: number;
  approvers: number;
  maskRules: number;
  policyExceptions: number;
  dataAccess: number;
}

/** Result of applying an imported snapshot (`POST /recovery/recover`). */
export interface RecoveryApplyResult {
  appliedAt: string;
  appliedBy: string;
  source: string;
  commitSha: string;
  counts: RecoveryApplyCounts;
  warnings: string[];
  appHadState: boolean;
}

/** Headline KPIs for the data-access analytics window (`GET /data-access/analytics`). */
export interface DataAccessAnalyticsKpis {
  totalRuns: number;
  successRuns: number;
  failedRuns: number;
  runningRuns: number;
  /** Successes over finished runs, 0..1; null when nothing finished. */
  successRate: number | null;
  avgDurationSeconds: number | null;
  lastSuccessAt: string | null;
  lastFailureAt: string | null;
  distinctFingerprints: number;
  /** Times the desired state changed between consecutive successful runs of the same mode. */
  fingerprintChanges: number;
}

/** One UTC day and mode of sync activity. */
export interface DataAccessAnalyticsDay {
  day: string;
  mode: "dry_run" | "apply";
  runs: number;
  success: number;
  failed: number;
  grantsAdded: number;
  grantsRevoked: number;
  membersAdded: number;
  membersRemoved: number;
  avgDurationSeconds: number | null;
}

/** A recent failed sync. */
export interface DataAccessAnalyticsFailure {
  runId: string;
  mode: "dry_run" | "apply";
  startedAt: string;
  triggeredBy: string;
  error: string | null;
}

/** How often one desired-state fingerprint appeared across successful runs. */
export interface DataAccessAnalyticsFingerprint {
  fingerprint: string;
  runs: number;
  firstSeen: string;
  lastSeen: string;
}

/** Dashboard payload for data-access sync analytics (`GET /data-access/analytics`). */
export interface DataAccessAnalytics {
  days: number;
  generatedAt: string;
  kpis: DataAccessAnalyticsKpis;
  daily: DataAccessAnalyticsDay[];
  failures: DataAccessAnalyticsFailure[];
  fingerprints: DataAccessAnalyticsFingerprint[];
}
