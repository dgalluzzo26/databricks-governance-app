/**
 * @file Identity attributes page: self-service attribute viewing and access requests, and admin
 * management of category approvers, attribute assignments, and attribute definitions.
 *
 * Approvals live on `/approvals`. The active tab is stored in the `?tab=` search parameter.
 * Every signed-in user sees "My attributes", "My access", "Request access", and "My requests".
 * "Assignments" is shown to app admins and path approvers (approvers only grant paths they can
 * decide). "Approvers" and "Definitions" are shown only to app admins. Legacy `?tab=approvals`
 * redirects to `/approvals`. This gating is a UI convenience only; the backend enforces
 * authorization on every endpoint.
 */
import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import {
  useAccessStatus,
  useAttributeAssignments,
  useAttributeDefinitions,
  useBulkAssignAttributes,
  useCancelAccessRequest,
  useCategoryApprovers,
  useCreateAccessRequest,
  useDataAccessSettings,
  useDeleteAttributeDefinition,
  useDeleteCategoryApprover,
  useGovernedTags,
  useGrantableAttributes,
  useMyAccess,
  useMyAccessRequests,
  useMyAccessResources,
  useMyAccessStatus,
  useMyAttributes,
  useRevokeAttribute,
  useRowFilterDimensions,
  useUpsertAttributeDefinition,
  useUpsertCategoryApprover,
} from "@/api/queries";
import { useEffectiveUser } from "@/app/viewAs";
import type {
  AccessRequest,
  AttributeDefinition,
  AttributeExplorerKind,
  BulkAssignResult,
  CategoryApprover,
  GovernedTagSummary,
  IdentityAttributeAssignment,
  MyAccessAttribute,
  MyAccessSubscription,
  PrincipalKind,
  UnlockedTable,
  ValueSource,
} from "@/api/types";
import { AccessStatusBadge } from "@/components/AccessStatusBadge";
import { accessStatusLabel } from "@/components/AccessStatusBadge";
import { PrincipalPicker } from "@/components/PrincipalPicker";
import { Alert, Badge, Card, ErrorAlert, PageHeader, SearchableSelect, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import styles from "./IdentityPage.module.css";
import { isDataAccessDefinition } from "./definitionRules";
import { ObjectTable } from "@/pages/subscriptions/SubscriptionWizardPage";
import { PHASE_LABELS, describePrivileges, describeResource, groupGrants } from "@/pages/subscriptions/subscriptionText";

/**
 * Identifier of a top-level tab on the identity page, as stored in the `?tab=` search parameter.
 *
 * @remarks
 * `"assignments"` requires admin or approver status. `"approvers"` and `"definitions"` require
 * admin status. See {@link USER_TABS}, {@link ASSIGNER_TABS}, and {@link ADMIN_TABS}.
 */
type IdentityTab =
  | "mine"
  | "my-access"
  | "request"
  | "my-requests"
  | "approvers"
  | "assignments"
  | "definitions";

/** Maximum number of allowed-value chips rendered per definition row before collapsing into "+N more". */
const MAX_VALUE_CHIPS = 20;
/** Human-readable labels for each attribute definition value source, shown in the definitions table. */
const VALUE_SOURCE_LABELS: Record<ValueSource, string> = {
  governed_tags: "Governed tags",
  free_form: "Free-form list",
  sql: "SQL seed query",
};

/** Principal kinds that can receive attribute assignments and be used as assignment filters. */
const PRINCIPAL_TYPES: PrincipalKind[] = ["USER", "GROUP", "SERVICE_PRINCIPAL"];
/** Tabs available to every signed-in user. */
const USER_TABS: IdentityTab[] = ["mine", "my-access", "request", "my-requests"];
/** Tabs available to app admins and path approvers. */
const ASSIGNER_TABS: IdentityTab[] = ["assignments"];
/** Tabs available only to app admins. */
const ADMIN_TABS: IdentityTab[] = ["definitions", "approvers"];

/** One selected principal chip in the bulk assign form. */
type SelectedPrincipal = { name: string; type: PrincipalKind };
/** One attribute key/value row in the bulk assign form. */
type AttributeRow = { key: string; value: string; id: string };

/**
 * Formats an ISO timestamp for display using the browser locale.
 *
 * @param value - ISO-8601 timestamp, or `null`/`undefined` when absent.
 * @returns An em dash for missing values, the raw input if it cannot be parsed, otherwise a
 * locale-formatted date and time.
 */
function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

/**
 * Identity attributes page with a role-gated tab bar and one panel per tab.
 *
 * Reads the effective user via `useEffectiveUser` and the active tab from the `?tab=` search
 * parameter (default `"mine"`). Legacy `?tab=approvals` redirects to `/approvals`.
 *
 * @remarks
 * Tabs are filtered by role: everyone gets {@link USER_TABS}; admins and approvers get
 * {@link ASSIGNER_TABS}; admins also get {@link ADMIN_TABS}. A disallowed or unknown `tab`
 * value silently falls back to `"mine"`. Respects admin demo preview.
 */
export function IdentityPage() {
  const { data: user } = useEffectiveUser();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  if (rawTab === "approvals") {
    return <Navigate to="/approvals" replace />;
  }
  const canAssign = Boolean(user?.isAdmin || user?.isApprover);
  const requested = (rawTab as IdentityTab | null) ?? "mine";
  const allowed = new Set<IdentityTab>([
    ...USER_TABS,
    ...(canAssign ? ASSIGNER_TABS : []),
    ...(user?.isAdmin ? ADMIN_TABS : []),
  ]);
  const tab: IdentityTab = allowed.has(requested) ? requested : "mine";

  /** Switches tabs by updating `?tab=` (replacing the history entry) while preserving other search params. */
  const setTab = (next: IdentityTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Identity attributes"
        description={
          user?.isAdmin
            ? "Manage attribute definitions, assignments, category approvers, and access requests."
            : user?.isApprover
              ? "View your attributes, request access, and grant attributes for paths you approve."
              : "View your attributes and request access. Approvals are under Approvals in the nav."
        }
      />

      <div className={styles.tabs} role="tablist" aria-label="Identity views">
        <button
          type="button"
          role="tab"
          aria-selected={tab === "mine"}
          className={`${styles.tab}${tab === "mine" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("mine")}
        >
          My attributes
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "my-access"}
          className={`${styles.tab}${tab === "my-access" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("my-access")}
        >
          My access
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "request"}
          className={`${styles.tab}${tab === "request" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("request")}
        >
          Request access
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "my-requests"}
          className={`${styles.tab}${tab === "my-requests" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("my-requests")}
        >
          My requests
        </button>
        {canAssign && (
          <button
            type="button"
            role="tab"
            aria-selected={tab === "assignments"}
            className={`${styles.tab}${tab === "assignments" ? ` ${styles.tabActive}` : ""}`}
            onClick={() => setTab("assignments")}
          >
            Assignments
          </button>
        )}
        {user?.isAdmin && (
          <>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "approvers"}
              className={`${styles.tab}${tab === "approvers" ? ` ${styles.tabActive}` : ""}`}
              onClick={() => setTab("approvers")}
            >
              Approvers
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "definitions"}
              className={`${styles.tab}${tab === "definitions" ? ` ${styles.tabActive}` : ""}`}
              onClick={() => setTab("definitions")}
            >
              Definitions
            </button>
          </>
        )}
      </div>

      {tab === "mine" && <MyAttributesPanel />}
      {tab === "my-access" && <MyAccessPanel />}
      {tab === "request" && <RequestAccessPanel />}
      {tab === "my-requests" && <MyRequestsPanel />}
      {tab === "approvers" && user?.isAdmin && <CategoryApproversPanel />}
      {tab === "assignments" && canAssign && <AssignmentsPanel />}
      {tab === "definitions" && user?.isAdmin && <DefinitionsPanel />}
    </>
  );
}

/**
 * "My attributes" tab: read-only table of the current user's effective attribute assignments.
 *
 * Uses `useMyAttributes`. Each row shows the attribute key and value, the principal it was
 * granted through (the user directly or one of their groups), and its expiry.
 *
 * @remarks
 * Visible to every signed-in user. No mutations.
 */
function MyAttributesPanel() {
  const { data, isPending, error } = useMyAttributes();

  return (
    <Card title="Effective attributes" actions={data ? <Badge>{data.total}</Badge> : null}>
      {isPending && <Spinner label="Loading attributes" />}
      {error && <ErrorAlert error={error} title="Could not load your attributes" />}
      {data && data.assignments.length === 0 && (
        <div className={styles.empty}>No active attributes are assigned to you or your groups.</div>
      )}
      {data && data.assignments.length > 0 && (
        <MyAttributesTable assignments={data.assignments} />
      )}
    </Card>
  );
}

/**
 * "My access" tab: groups the user belongs to, attributes by Via (direct / group), and
 * expandable rows that load affected tables or columns on demand.
 */
function MyAccessPanel() {
  const { data, isPending, error } = useMyAccess();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (isPending) return <Spinner label="Loading your access" />;
  if (error) return <ErrorAlert error={error} title="Could not load your access" />;
  if (!data) return null;

  const direct = data.attributes.filter((item) => item.principalType === "USER");
  const byGroup = new Map<string, MyAccessAttribute[]>();
  for (const item of data.attributes) {
    if (item.principalType !== "GROUP") continue;
    const list = byGroup.get(item.principalName) ?? [];
    list.push(item);
    byGroup.set(item.principalName, list);
  }

  return (
    <div className={styles.stack}>
      <Card title="Summary">
        <div className={styles.buttonRow} style={{ flexWrap: "wrap", gap: "var(--space-3)" }}>
          <Badge>{data.groupCount} groups</Badge>
          <Badge>{data.attributeCount} attributes</Badge>
          <Badge tone="info">{data.directAttributeCount} direct</Badge>
          <Badge tone="info">{data.inheritedAttributeCount} via groups</Badge>
          <Badge tone="success">{data.dataAccessReadyCount} data access ready</Badge>
          <Badge tone="info">{data.subscriptionCount} subscriptions</Badge>
        </div>
      </Card>

      <MyAccessSubscriptionsCard subscriptions={data.subscriptions} />

      <Card title="My groups" actions={<Badge>{data.groups.length}</Badge>}>
        {data.groups.length === 0 ? (
          <div className={styles.empty}>You are not in any workspace groups.</div>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="My groups">
              <thead>
                <tr>
                  <th>Group</th>
                  <th>Attributes from this group</th>
                </tr>
              </thead>
              <tbody>
                {data.groups.map((group) => (
                  <tr key={group.name}>
                    <td>
                      <code>{group.name}</code>
                    </td>
                    <td>
                      <Badge>{group.attributeCount}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title="Effective attributes by source" actions={<Badge>{data.attributeCount}</Badge>}>
        {data.attributeCount === 0 ? (
          <div className={styles.empty}>No active attributes are assigned to you or your groups.</div>
        ) : (
          <div className={styles.stack}>
            {direct.length > 0 && (
              <MyAccessAttributeSection
                title="Direct"
                rows={direct}
                expandedId={expandedId}
                onToggle={setExpandedId}
              />
            )}
            {[...byGroup.entries()].map(([groupName, rows]) => (
              <MyAccessAttributeSection
                key={groupName}
                title={`Via ${groupName}`}
                rows={rows}
                expandedId={expandedId}
                onToggle={setExpandedId}
              />
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/**
 * Subscriptions that include the caller, directly or through a group. Each row can expand to the
 * grants its group held at the last sync and what the subscription is meant to reach.
 */
function MyAccessSubscriptionsCard({ subscriptions }: { subscriptions: MyAccessSubscription[] }) {
  const [openId, setOpenId] = useState<string | null>(null);
  return (
    <Card title="My subscriptions" actions={<Badge>{subscriptions.length}</Badge>}>
      {subscriptions.length === 0 ? (
        <div className={styles.empty}>No subscriptions include you, directly or through your groups.</div>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="My subscriptions">
            <thead>
              <tr>
                <th>Subscription</th>
                <th>Via</th>
                <th>Status</th>
                <th>Ends</th>
                <th>Objects</th>
                <th>Access</th>
              </tr>
            </thead>
            <tbody>
              {subscriptions.map((sub) => {
                const open = openId === sub.subscriptionId;
                const phase = PHASE_LABELS[sub.phase];
                return (
                  <Fragment key={sub.subscriptionId}>
                    <tr>
                      <td>
                        <strong>{sub.displayName}</strong>
                        <br />
                        <code>{sub.groupName}</code>
                      </td>
                      <td>{sub.via === "Direct" ? "Direct" : `Via ${sub.via}`}</td>
                      <td>
                        <Badge tone={phase.tone}>{phase.label}</Badge>
                      </td>
                      <td className={styles.muted}>{formatWhen(sub.expiresAt)}</td>
                      <td>{sub.matchedObjects}</td>
                      <td>
                        <button
                          type="button"
                          className={styles.button}
                          onClick={() => setOpenId(open ? null : sub.subscriptionId)}
                          aria-expanded={open}
                        >
                          {open ? "Hide" : "Show"} access
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr>
                        <td colSpan={6}>
                          <div className={styles.stack}>
                            <p className={styles.muted}>
                              Reaches: {sub.resources.map(describeResource).join("; ")}. Permissions:{" "}
                              {sub.resources.map(describePrivileges).join("; ")}.
                            </p>
                            {sub.grants.length > 0 ? (
                              <>
                                <p className={styles.muted}>
                                  As of{" "}
                                  {sub.lastSyncedAt ? formatWhen(sub.lastSyncedAt) : "the last sync"}
                                  {sub.syncOk === false && " (the last sync reported errors)"}:
                                </p>
                                <ObjectTable objects={groupGrants(sub.grants).slice(0, 200)} />
                              </>
                            ) : (
                              <p className={styles.muted}>
                                {sub.phase === "ACTIVE" || sub.phase === "EXPIRING"
                                  ? "No grants recorded yet. The next sync sets them."
                                  : "This subscription is not in effect, so it holds no access."}
                              </p>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

/**
 * One Via section (Direct or a group) of expandable attribute rows.
 */
function MyAccessAttributeSection({
  title,
  rows,
  expandedId,
  onToggle,
}: {
  title: string;
  rows: MyAccessAttribute[];
  expandedId: string | null;
  onToggle: (id: string | null) => void;
}) {
  return (
    <div className={styles.stack}>
      <h3 className={styles.label}>{title}</h3>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label={title}>
          <thead>
            <tr>
              <th>Attribute</th>
              <th>Value</th>
              <th>Kind</th>
              <th>Expires</th>
              <th>Data access</th>
              <th>Resources</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((item) => {
              const open = expandedId === item.assignmentId;
              return (
                <MyAccessAttributeRow
                  key={item.assignmentId}
                  item={item}
                  open={open}
                  onToggle={() => onToggle(open ? null : item.assignmentId)}
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/**
 * One attribute row that can expand to load affected tables/columns.
 */
function MyAccessAttributeRow({
  item,
  open,
  onToggle,
}: {
  item: MyAccessAttribute;
  open: boolean;
  onToggle: () => void;
}) {
  const resources = useMyAccessResources(
    open ? item.attributeKey : null,
    open ? item.attributeValue : null,
  );
  const canExpand = item.kind === "data_access" || item.kind === "sensitivity" || item.kind === "row_filter";

  return (
    <>
      <tr>
        <td>
          <code>{item.attributeKey}</code>
        </td>
        <td>
          <Badge tone="info">{item.attributeValue}</Badge>
        </td>
        <td>
          <Badge>{item.kind.replaceAll("_", " ")}</Badge>
        </td>
        <td className={styles.muted}>{formatWhen(item.expiresAt)}</td>
        <td>
          <AccessStatusBadge status={item.accessStatus ?? undefined} />
        </td>
        <td>
          {canExpand ? (
            <button type="button" className={styles.button} onClick={onToggle} aria-expanded={open}>
              {open ? "Hide" : "Show"} resources
            </button>
          ) : (
            <span className={styles.muted}>—</span>
          )}
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6}>
            {resources.isPending && <Spinner label="Loading resources" />}
            {resources.error && (
              <ErrorAlert error={resources.error} title="Could not load resources" />
            )}
            {resources.data && (
              <MyAccessResourcesTable kind={resources.data.kind} tables={resources.data.tables} />
            )}
          </td>
        </tr>
      )}
    </>
  );
}

/**
 * Inline table of resources for an expanded My access attribute.
 *
 * @remarks
 * Paginated and filterable: a data-access grant can unlock hundreds of tables, so the list is
 * shown a page at a time with per-column filters and sorting.
 */
function MyAccessResourcesTable({
  kind,
  tables,
}: {
  kind: AttributeExplorerKind;
  tables: UnlockedTable[];
}) {
  const showColumn = kind === "sensitivity" || kind === "row_filter";
  const title =
    kind === "sensitivity"
      ? "Columns affected"
      : kind === "row_filter"
        ? "Tables filtered"
        : "Tables unlocked";
  const empty =
    kind === "sensitivity"
      ? "No columns tagged with this sensitivity path (or a descendant)."
      : kind === "row_filter"
        ? "No table columns are tagged with this row-filter dimension."
        : "No reviewed tables tagged with this path (or a descendant).";
  const tagLabel =
    kind === "sensitivity" ? "Sensitivity path" : kind === "row_filter" ? "Dimension" : "Tag value";

  const columns = useMemo<TableColumn<UnlockedTable>[]>(
    () => [
      { key: "table", label: "Table", value: (item) => item.fullName },
      ...(showColumn
        ? [{ key: "column", label: "Column", value: (item: UnlockedTable) => item.columnName ?? "" }]
        : []),
      { key: "tag", label: tagLabel, value: (item) => item.tagValue },
      { key: "match", label: "Match", value: (item) => item.match },
      ...(kind === "data_access"
        ? [{ key: "group", label: "Group", value: (item: UnlockedTable) => item.groupName ?? "" }]
        : []),
    ],
    [kind, showColumn, tagLabel],
  );
  const pager = useTableView(tables, columns, { defaultPageSize: 10 });

  if (tables.length === 0) {
    return <div className={styles.empty}>{empty}</div>;
  }

  return (
    <div className={styles.stack}>
      <div className={styles.label}>
        {title} <Badge>{tables.length}</Badge>
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label={title}>
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={`${item.fullName}:${item.columnName ?? ""}:${item.tagValue}`}>
                <td>
                  <Link to={`/access-explorer?table=${encodeURIComponent(item.fullName)}`}>
                    <code>{item.fullName}</code>
                  </Link>
                </td>
                {showColumn && (
                  <td>
                    <code>{item.columnName ?? "—"}</code>
                  </td>
                )}
                <td>
                  <code>{item.tagValue}</code>
                </td>
                <td>{item.match}</td>
                {kind === "data_access" && (
                  <td>
                    <code>{item.groupName ?? "—"}</code>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </div>
  );
}

/**
 * Paginated table of the signed-in user's effective attribute assignments.
 */
function MyAttributesTable({ assignments }: { assignments: IdentityAttributeAssignment[] }) {
  const access = useMyAccessStatus();
  const statusById = new Map((access.data?.statuses ?? []).map((s) => [s.assignmentId, s]));
  const columns: TableColumn<IdentityAttributeAssignment>[] = [
    { key: "attribute", label: "Attribute", value: (item) => item.attributeKey },
    { key: "value", label: "Value", value: (item) => item.attributeValue },
    {
      key: "via",
      label: "Via",
      value: (item) => item.principalName,
      filterText: (item) => `${item.principalType} ${item.principalName}`,
    },
    { key: "expires", label: "Expires", value: (item) => item.expiresAt, filterText: (item) => formatWhen(item.expiresAt) },
    {
      key: "access",
      label: "Data access",
      value: (item) => {
        const status = statusById.get(item.assignmentId);
        return status ? accessStatusLabel(status).label : null;
      },
    },
  ];
  const pager = useTableView(assignments, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={item.assignmentId}>
                <td>
                  <code>{item.attributeKey}</code>
                </td>
                <td>
                  <Badge tone="info">{item.attributeValue}</Badge>
                </td>
                <td>
                  <Badge>{item.principalType}</Badge> <code>{item.principalName}</code>
                </td>
                <td className={styles.muted}>{formatWhen(item.expiresAt)}</td>
                <td>
                  <AccessStatusBadge status={statusById.get(item.assignmentId)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </>
  );
}

/**
 * "Request access" tab: form for submitting an access request for an attribute value.
 *
 * Uses `useAttributeDefinitions` and `useRowFilterDimensions` to build the list of requestable
 * attributes: the `sensitivity` definition (column masking) plus any definition whose key is a
 * row-filter dimension. Values are restricted to the selected definition's `allowedValues`.
 * The `?key=` and `?value=` search params prefill the form (key defaults to `sensitivity`),
 * which lets other pages such as Access Explorer deep-link here.
 *
 * Submitting calls `useCreateAccessRequest` with the key, value, trimmed justification, and the
 * requested expiry (required), then clears the value, justification, and expiry fields.
 *
 * @remarks
 * Visible to every signed-in user. Sensitivity requests are routed to the matching category
 * approvers when configured, otherwise to app admins; row-filter requests always go to app
 * admins. Routing is decided by the backend.
 */
function RequestAccessPanel() {
  const [searchParams] = useSearchParams();
  const definitions = useAttributeDefinitions();
  const dimensions = useRowFilterDimensions();
  const create = useCreateAccessRequest();
  const prefillsKey = searchParams.get("key") ?? "sensitivity";
  const prefillsValue = searchParams.get("value") ?? "";
  const [attributeKey, setAttributeKey] = useState(prefillsKey);
  const [attributeValue, setAttributeValue] = useState(prefillsValue);
  const [justification, setJustification] = useState("");
  const [expires, setExpires] = useState("");

  const dimKeys = useMemo(
    () => new Set((dimensions.data?.dimensions ?? []).map((item) => item.key)),
    [dimensions.data],
  );
  const requestable = useMemo(() => {
    const defs = definitions.data?.definitions ?? [];
    return defs.filter((item) => item.key === "sensitivity" || dimKeys.has(item.key));
  }, [definitions.data, dimKeys]);
  const selected = requestable.find((item) => item.key === attributeKey) ?? null;
  const values = selected?.allowedValues ?? [];
  const isRowFilter = attributeKey !== "sensitivity";

  // Re-apply prefills when the deep-link search params change while the panel is mounted.
  useEffect(() => {
    if (prefillsKey) setAttributeKey(prefillsKey);
    if (prefillsValue) setAttributeValue(prefillsValue);
  }, [prefillsKey, prefillsValue]);

  // Drop a selected (or prefilled) value that is not allowed for the current attribute.
  useEffect(() => {
    if (attributeValue && values.length && !values.includes(attributeValue)) {
      setAttributeValue("");
    }
  }, [attributeKey, values, attributeValue]);

  /** Submits the access request and resets the value, justification, and expiry on success. */
  const onSubmit = async () => {
    await create.mutateAsync({
      attributeKey,
      attributeValue,
      justification: justification.trim(),
      requestedExpiresAt: new Date(expires).toISOString(),
    });
    setAttributeValue("");
    setJustification("");
    setExpires("");
  };

  return (
    <Card title="Request access">
      <p className={styles.muted}>
        Request a sensitivity path (category approvers when configured) or a row-filter dimension value
        such as <code>state=*</code> (app admins). Prefer Access Explorer to discover the right
        attributes for a table.
      </p>
      {!requestable.length && !definitions.isPending && !dimensions.isPending && (
        <Alert tone="warning" title="No requestable attributes">
          An admin must create the sensitivity definition and/or row-filter dimensions before requests
          can be submitted.
        </Alert>
      )}
      <div className={styles.formGrid}>
        <label className={styles.field}>
          <span className={styles.label}>Attribute</span>
          <select
            className={styles.select}
            value={attributeKey}
            onChange={(event) => {
              setAttributeKey(event.target.value);
              setAttributeValue("");
            }}
            disabled={!requestable.length}
          >
            {requestable.map((item) => (
              <option key={item.key} value={item.key}>
                {item.key}
                {item.key === "sensitivity" ? " (mask)" : " (row filter)"}
              </option>
            ))}
          </select>
        </label>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>{isRowFilter ? "Dimension value" : "Sensitivity path"}</span>
          <SearchableSelect
            aria-label={isRowFilter ? "Dimension value" : "Sensitivity path"}
            value={attributeValue}
            onChange={setAttributeValue}
            options={values}
            placeholder="Select value"
            disabled={!values.length}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>Requested expiry</span>
          <input
            className={styles.input}
            type="datetime-local"
            value={expires}
            onChange={(event) => setExpires(event.target.value)}
          />
        </label>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Justification</span>
          <input
            className={styles.input}
            value={justification}
            onChange={(event) => setJustification(event.target.value)}
            placeholder="Why do you need this access?"
          />
        </label>
      </div>
      {isRowFilter && (
        <div className={styles.muted} style={{ marginTop: "var(--space-2)" }}>
          Row-filter requests are reviewed by app admins. Use <code>*</code> for all rows, or a
          concrete value to match only those rows.
        </div>
      )}
      <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          disabled={!attributeKey || !attributeValue || !justification.trim() || !expires || create.isPending}
          onClick={() => void onSubmit()}
        >
          {create.isPending ? "Submitting…" : "Submit request"}
        </button>
        {create.error && <ErrorAlert error={create.error} title="Request failed" />}
      </div>
    </Card>
  );
}

/**
 * "My requests" tab: table of access requests submitted by the current user.
 *
 * Uses `useMyAccessRequests`. Each row shows the requested attribute and value, who the request
 * was routed to (a resolved sensitivity category, category approvers, or app admins), its
 * status, requested expiry, and submission time. Pending requests can be cancelled via
 * `useCancelAccessRequest`.
 *
 * @remarks
 * Visible to every signed-in user.
 */
function MyRequestsPanel() {
  const { data, isPending, error } = useMyAccessRequests();
  const cancel = useCancelAccessRequest();

  return (
    <Card title="My requests" actions={data ? <Badge>{data.total}</Badge> : null}>
      {isPending && <Spinner label="Loading requests" />}
      {error && <ErrorAlert error={error} title="Could not load requests" />}
      {data && data.requests.length === 0 && <div className={styles.empty}>No access requests yet.</div>}
      {data && data.requests.length > 0 && (
        <MyRequestsTable
          requests={data.requests}
          cancelling={cancel.isPending}
          onCancel={(requestId) => void cancel.mutateAsync(requestId)}
        />
      )}
    </Card>
  );
}

/**
 * Paginated table of the signed-in user's access requests with a cancel action for pending rows.
 */
function MyRequestsTable({
  requests,
  cancelling,
  onCancel,
}: {
  requests: AccessRequest[];
  cancelling: boolean;
  onCancel: (requestId: string) => void;
}) {
  const columns: TableColumn<AccessRequest>[] = [
    { key: "attribute", label: "Attribute", value: (item) => item.attributeKey },
    { key: "value", label: "Value", value: (item) => item.attributeValue },
    {
      key: "routed",
      label: "Routed to",
      value: (item) =>
        item.routedTo === "category_approvers"
          ? item.resolvedCategory
            ? `category ${item.resolvedCategory}`
            : "category approvers"
          : "app admins",
    },
    { key: "status", label: "Status", value: (item) => item.status },
    {
      key: "requestedExpiry",
      label: "Requested expiry",
      value: (item) => item.requestedExpiresAt,
      filterText: (item) => formatWhen(item.requestedExpiresAt),
    },
    { key: "submitted", label: "Submitted", value: (item) => item.createdAt, filterText: (item) => formatWhen(item.createdAt) },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(requests, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="My access requests">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={item.requestId}>
                <td>
                  <code>{item.attributeKey}</code>
                </td>
                <td>
                  <code>{item.attributeValue}</code>
                </td>
                <td className={styles.muted}>
                  {item.routedTo === "category_approvers"
                    ? item.resolvedCategory
                      ? `category ${item.resolvedCategory}`
                      : "category approvers"
                    : "app admins"}
                </td>
                <td>
                  <Badge tone={item.status === "APPROVED" ? "success" : item.status === "DENIED" ? "danger" : "neutral"}>
                    {item.status}
                  </Badge>
                </td>
                <td className={styles.muted}>{formatWhen(item.requestedExpiresAt)}</td>
                <td className={styles.muted}>{formatWhen(item.createdAt)}</td>
                <td>
                  {item.status === "PENDING" && (
                    <button
                      type="button"
                      className={styles.button}
                      disabled={cancelling}
                      aria-busy={cancelling}
                      onClick={() => onCancel(item.requestId)}
                    >
                      {cancelling ? "Cancelling…" : "Cancel"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </>
  );
}

/**
 * "Approvers" tab: register users and/or groups on a data-access path.
 *
 * That registration covers data-access, sensitivity, and row-filter requests under the path
 * (most specific wins). Falls back to app admins when nothing matches.
 */
function CategoryApproversPanel() {
  const { data, isPending, error } = useCategoryApprovers();
  const definitions = useAttributeDefinitions();
  const dataAccessSettings = useDataAccessSettings();
  const upsert = useUpsertCategoryApprover();
  const remove = useDeleteCategoryApprover();
  const accessTagKey =
    dataAccessSettings.data?.settings.accessTagKey?.trim().toLowerCase() || "access_path";
  const dataAccessDef = (definitions.data?.definitions ?? []).find((item) =>
    isDataAccessDefinition(item, accessTagKey),
  );
  const dataAccessPaths = (dataAccessDef?.allowedValues ?? []).filter((value) => value && value !== "*");
  const [categoryPath, setCategoryPath] = useState("");
  const [pickerValue, setPickerValue] = useState("");
  const [principals, setPrincipals] = useState<{ name: string; type: PrincipalKind }[]>([]);

  const addPrincipal = (name: string, kind?: PrincipalKind) => {
    const trimmed = name.trim();
    if (!trimmed || !kind) return;
    setPrincipals((current) => {
      if (current.some((item) => item.name.toLowerCase() === trimmed.toLowerCase() && item.type === kind)) {
        return current;
      }
      return [...current, { name: trimmed, type: kind }];
    });
    setPickerValue("");
  };

  return (
    <div className={styles.stack}>
      <Card title="Add approver">
        <p className={styles.muted}>
          Approvers are set on an access path (most specific wins). Add any mix of users and groups
          for that path; they decide data-access, sensitivity, and row-filter requests under it.
          When nothing matches, app admins review the request.
        </p>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Access path</span>
            <SearchableSelect
              aria-label="Access path"
              value={categoryPath}
              onChange={setCategoryPath}
              options={dataAccessPaths}
              placeholder={dataAccessDef ? "Select path" : "Create a data-access definition first"}
              disabled={!dataAccessDef}
            />
          </label>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Users and groups</span>
            <PrincipalPicker
              value={pickerValue}
              placeholder="Search users or groups to add…"
              aria-label="Add approver principal"
              onChange={(name, nextKind) => {
                setPickerValue(name);
                if (nextKind === "USER" || nextKind === "GROUP") addPrincipal(name, nextKind);
              }}
            />
            {principals.length > 0 && (
              <div className={styles.chipRow} aria-label="Selected approvers">
                {principals.map((item) => (
                  <span key={`${item.type}:${item.name}`} className={styles.chip}>
                    <Badge>{item.type}</Badge>
                    <code>{item.name}</code>
                    <button
                      type="button"
                      className={styles.chipRemove}
                      aria-label={`Remove ${item.name}`}
                      onClick={() =>
                        setPrincipals((current) =>
                          current.filter((p) => !(p.name === item.name && p.type === item.type)),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!categoryPath || principals.length === 0 || upsert.isPending}
            onClick={() => {
              void upsert
                .mutateAsync({
                  categoryPath,
                  principals: principals.map((item) => ({
                    principalName: item.name,
                    principalType: item.type,
                  })),
                })
                .then(() => {
                  setPrincipals([]);
                  setPickerValue("");
                });
            }}
          >
            {upsert.isPending ? "Saving…" : principals.length > 1 ? "Add approvers" : "Add approver"}
          </button>
          {upsert.error && <ErrorAlert error={upsert.error} title="Could not save approver" />}
        </div>
      </Card>

      <Card title="Configured approvers" actions={data ? <Badge>{data.total}</Badge> : null}>
        {isPending && <Spinner label="Loading approvers" />}
        {error && <ErrorAlert error={error} title="Could not load approvers" />}
        {data && data.approvers.length === 0 && (
          <div className={styles.empty}>No approvers configured yet.</div>
        )}
        {data && data.approvers.length > 0 && (
          <ApproversTable
            approvers={data.approvers}
            removing={remove.isPending}
            onRemove={(approverId) => void remove.mutateAsync(approverId)}
          />
        )}
      </Card>
    </div>
  );
}

/**
 * Paginated table of configured request approvers with a remove action.
 */
function ApproversTable({
  approvers,
  removing,
  onRemove,
}: {
  approvers: CategoryApprover[];
  removing: boolean;
  onRemove: (approverId: string) => void;
}) {
  const columns: TableColumn<CategoryApprover>[] = [
    { key: "path", label: "Access path", value: (item) => item.categoryPath },
    { key: "principal", label: "Principal", value: (item) => item.principalName },
    { key: "type", label: "Type", value: (item) => item.principalType },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(approvers, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Approvers">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={item.approverId}>
                <td>
                  <code>{item.categoryPath}</code>
                </td>
                <td>
                  <code>{item.principalName}</code>
                </td>
                <td>
                  <Badge>{item.principalType}</Badge>
                </td>
                <td>
                  <button
                    type="button"
                    className={styles.button}
                    disabled={removing}
                    aria-busy={removing}
                    onClick={() => onRemove(item.approverId)}
                  >
                    {removing ? "Removing…" : "Remove"}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </>
  );
}

/**
 * "Assignments" tab: bulk-grant attribute values to principals and browse or revoke existing
 * assignments.
 *
 * The form picks many users/groups and one or more attribute+value rows, with one shared
 * justification and expiry. Submitting calls `useBulkAssignAttributes` (each grant is still
 * created and audited individually). Approvers only see attribute keys from
 * `useGrantableAttributes`; the server re-checks path authority per value.
 *
 * The "Assignments" table uses `useAttributeAssignments`, filtered by principal name, principal
 * type, attribute key, and an "Include inactive" toggle. Active rows can be revoked via
 * `useRevokeAttribute`. Approvers only see assignments for paths they can decide.
 *
 * @remarks
 * Rendered for app admins and path approvers. Direct assignments bypass the request inbox.
 */
function AssignmentsPanel() {
  const { data: user } = useEffectiveUser();
  const definitions = useAttributeDefinitions();
  const grantable = useGrantableAttributes();
  const [principalName, setPrincipalName] = useState("");
  // The box filters as you type: the query waits briefly after the last keystroke.
  const principalSearch = useDebouncedValue(principalName.trim(), 300);
  const [principalType, setPrincipalType] = useState<PrincipalKind | "">("");
  const [attributeKey, setAttributeKey] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);

  const params = useMemo(
    () => ({
      principalSearch: principalSearch || undefined,
      principalType: principalType || undefined,
      attributeKey: attributeKey || undefined,
      includeInactive,
    }),
    [principalSearch, principalType, attributeKey, includeInactive],
  );
  const { data, isPending, error } = useAttributeAssignments(params);
  const bulkAssign = useBulkAssignAttributes();
  const revoke = useRevokeAttribute();
  const assignments = data?.assignments ?? [];
  const columns: TableColumn<IdentityAttributeAssignment>[] = [
    {
      key: "principal",
      label: "Principal",
      value: (item) => item.principalName,
      filterText: (item) => `${item.principalType} ${item.principalName}`,
    },
    { key: "attribute", label: "Attribute", value: (item) => item.attributeKey },
    { key: "value", label: "Value", value: (item) => item.attributeValue },
    { key: "status", label: "Status", value: (item) => (item.active ? "Active" : "Inactive") },
    { key: "access", label: "Data access", sortable: false, filterable: false },
    {
      key: "granted",
      label: "Granted",
      value: (item) => item.createdAt,
      filterText: (item) => `${item.grantedBy ?? ""} ${formatWhen(item.createdAt)}`,
    },
    { key: "expires", label: "Expires", value: (item) => item.expiresAt, filterText: (item) => formatWhen(item.expiresAt) },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(assignments, columns, {
    resetKey: `${principalSearch}|${principalType}|${attributeKey}|${includeInactive}`,
  });
  const access = useAccessStatus(pager.rows.map((item) => item.assignmentId));
  const statusById = new Map((access.data?.statuses ?? []).map((s) => [s.assignmentId, s]));

  const [pickerValue, setPickerValue] = useState("");
  const [principals, setPrincipals] = useState<SelectedPrincipal[]>([]);
  const [attrRows, setAttrRows] = useState<AttributeRow[]>([{ id: "row-1", key: "", value: "" }]);
  const [formJustification, setFormJustification] = useState("");
  const [formExpires, setFormExpires] = useState("");
  const [lastResult, setLastResult] = useState<BulkAssignResult | null>(null);

  const grantableKeys = useMemo(() => {
    if (user?.isAdmin) {
      return (definitions.data?.definitions ?? []).map((item) => item.key);
    }
    return (grantable.data?.attributes ?? []).map((item) => item.key);
  }, [user?.isAdmin, definitions.data?.definitions, grantable.data?.attributes]);

  const definitionByKey = useMemo(() => {
    const map = new Map<string, AttributeDefinition>();
    for (const item of definitions.data?.definitions ?? []) map.set(item.key, item);
    return map;
  }, [definitions.data?.definitions]);

  const validAttrRows = attrRows.filter((row) => row.key.trim() && row.value.trim());
  const grantCount = principals.length * validAttrRows.length;
  const canSubmit = principals.length > 0 && validAttrRows.length > 0 && !bulkAssign.isPending;

  const addPrincipal = (name: string, kind?: PrincipalKind) => {
    const trimmed = name.trim();
    if (!trimmed || !kind) return;
    setPrincipals((current) => {
      if (current.some((item) => item.name.toLowerCase() === trimmed.toLowerCase() && item.type === kind)) {
        return current;
      }
      return [...current, { name: trimmed, type: kind }];
    });
    setPickerValue("");
  };

  const onAssign = async () => {
    const result = await bulkAssign.mutateAsync({
      principals: principals.map((item) => ({
        principalName: item.name,
        principalType: item.type,
      })),
      attributes: validAttrRows.map((row) => ({
        attributeKey: row.key.trim(),
        attributeValue: row.value.trim(),
      })),
      justification: formJustification.trim() || null,
      expiresAt: formExpires ? new Date(formExpires).toISOString() : null,
    });
    setLastResult(result);
    setAttrRows([{ id: `row-${Date.now()}`, key: "", value: "" }]);
    setFormJustification("");
    setFormExpires("");
  };

  return (
    <div className={styles.stack}>
      <Card title="Assign attributes">
        <div className={styles.formGrid}>
          <label className={styles.fieldGrow} style={{ gridColumn: "1 / -1" }}>
            <span className={styles.label}>Principals</span>
            <PrincipalPicker
              value={pickerValue}
              placeholder="Search users or groups to add…"
              aria-label="Add principal"
              onChange={(name, nextKind) => {
                setPickerValue(name);
                if (nextKind) addPrincipal(name, nextKind);
              }}
            />
            {principals.length > 0 && (
              <div className={styles.chipRow} aria-label="Selected principals">
                {principals.map((item) => (
                  <span key={`${item.type}:${item.name}`} className={styles.chip}>
                    <Badge>{item.type}</Badge>
                    <code>{item.name}</code>
                    <button
                      type="button"
                      className={styles.chipRemove}
                      aria-label={`Remove ${item.name}`}
                      onClick={() =>
                        setPrincipals((current) =>
                          current.filter((p) => !(p.name === item.name && p.type === item.type)),
                        )
                      }
                    >
                      ×
                    </button>
                  </span>
                ))}
              </div>
            )}
          </label>

          {attrRows.map((row) => {
            const valueOptions = definitionByKey.get(row.key)?.allowedValues ?? [];
            return (
              <div key={row.id} className={styles.attrRow}>
                <label className={styles.field}>
                  <span className={styles.label}>Attribute</span>
                  <select
                    className={styles.select}
                    value={row.key}
                    onChange={(event) => {
                      const key = event.target.value;
                      setAttrRows((current) =>
                        current.map((item) => (item.id === row.id ? { ...item, key, value: "" } : item)),
                      );
                    }}
                  >
                    <option value="">Select attribute</option>
                    {grantableKeys.map((key) => (
                      <option key={key} value={key}>
                        {key}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.field}>
                  <span className={styles.label}>Value</span>
                  {valueOptions.length ? (
                    <SearchableSelect
                      aria-label="Value"
                      value={row.value}
                      onChange={(value) => {
                        setAttrRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, value } : item)),
                        );
                      }}
                      options={valueOptions}
                      placeholder="Select value"
                    />
                  ) : (
                    <input
                      className={styles.input}
                      value={row.value}
                      onChange={(event) => {
                        const value = event.target.value;
                        setAttrRows((current) =>
                          current.map((item) => (item.id === row.id ? { ...item, value } : item)),
                        );
                      }}
                      placeholder="finance or finance.budget"
                      aria-label="Value"
                    />
                  )}
                </label>
                {attrRows.length > 1 && (
                  <button
                    type="button"
                    className={styles.button}
                    aria-label="Remove attribute row"
                    onClick={() => setAttrRows((current) => current.filter((item) => item.id !== row.id))}
                  >
                    Remove
                  </button>
                )}
              </div>
            );
          })}

          <div className={styles.buttonRow} style={{ gridColumn: "1 / -1" }}>
            <button
              type="button"
              className={styles.button}
              onClick={() =>
                setAttrRows((current) => [...current, { id: `row-${Date.now()}`, key: "", value: "" }])
              }
            >
              Add attribute
            </button>
          </div>

          <label className={styles.fieldGrow}>
            <span className={styles.label}>Justification</span>
            <input
              className={styles.input}
              value={formJustification}
              onChange={(event) => setFormJustification(event.target.value)}
              placeholder="Optional — applied to every grant"
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Expires</span>
            <input
              className={styles.input}
              type="datetime-local"
              value={formExpires}
              onChange={(event) => setFormExpires(event.target.value)}
            />
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!canSubmit}
            onClick={() => void onAssign()}
          >
            {bulkAssign.isPending ? "Assigning…" : "Assign"}
          </button>
          {grantCount > 0 && (
            <span className={styles.muted}>
              {principals.length} principal{principals.length === 1 ? "" : "s"} × {validAttrRows.length}{" "}
              value{validAttrRows.length === 1 ? "" : "s"} = {grantCount} assignment
              {grantCount === 1 ? "" : "s"}
            </span>
          )}
          {bulkAssign.error && <ErrorAlert error={bulkAssign.error} title="Assign failed" />}
        </div>
        {lastResult && (
          <Alert
            tone={lastResult.failed.length ? "warning" : "success"}
            title="Bulk assign finished"
          >
            {lastResult.succeeded.length} created or renewed, {lastResult.skipped.length} already held
            {lastResult.failed.length ? `, ${lastResult.failed.length} failed` : ""}.
            {lastResult.failed.length > 0 && (
              <ul className={styles.resultList}>
                {lastResult.failed.slice(0, 8).map((item) => (
                  <li key={`${item.principalName}:${item.attributeKey}:${item.attributeValue}`}>
                    <code>
                      {item.principalName} → {item.attributeKey}={item.attributeValue}
                    </code>
                    : {item.reason}
                  </li>
                ))}
                {lastResult.failed.length > 8 && <li>…and {lastResult.failed.length - 8} more</li>}
              </ul>
            )}
          </Alert>
        )}
        {grantableKeys.length === 0 && (
          <Alert tone="warning" title="No grantable attributes">
            {user?.isAdmin
              ? "Create a definition on the Definitions tab before assigning values."
              : "You are not listed as an approver for any attribute path yet."}
          </Alert>
        )}
      </Card>

      <Card title="Assignments" actions={data ? <Badge>{data.total}</Badge> : null}>
        <div className={styles.toolbar}>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Principal</span>
            <input
              className={styles.input}
              value={principalName}
              onChange={(event) => setPrincipalName(event.target.value)}
              placeholder="Type part of a name or email"
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Type</span>
            <select
              className={styles.select}
              value={principalType}
              onChange={(event) => setPrincipalType(event.target.value as PrincipalKind | "")}
            >
              <option value="">All types</option>
              {PRINCIPAL_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type.replaceAll("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Attribute</span>
            <select
              className={styles.select}
              value={attributeKey}
              onChange={(event) => setAttributeKey(event.target.value)}
            >
              <option value="">All attributes</option>
              {grantableKeys.map((key) => (
                <option key={key} value={key}>
                  {key}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.checkboxRow}>
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(event) => setIncludeInactive(event.target.checked)}
            />
            Include inactive
          </label>
        </div>

        {isPending && <Spinner label="Loading assignments" />}
        {error && <ErrorAlert error={error} title="Could not load assignments" />}
        {data && assignments.length === 0 && (
          <div className={styles.empty}>No assignments match the current filters.</div>
        )}
        {data && assignments.length > 0 && (
          <>
            <div className={styles.tableWrap}>
              <table className={styles.table} aria-label="Assignments">
                <DataTableHead view={pager} />
                <tbody>
                  {pager.rows.map((item) => (
                    <tr key={item.assignmentId}>
                      <td>
                        <div>
                          <Badge>{item.principalType}</Badge>
                        </div>
                        <code>{item.principalName}</code>
                      </td>
                      <td>
                        <code>{item.attributeKey}</code>
                      </td>
                      <td>
                        <Badge tone="info">{item.attributeValue}</Badge>
                      </td>
                      <td>
                        <Badge tone={item.active ? "success" : "neutral"}>
                          {item.active ? "Active" : "Inactive"}
                        </Badge>
                      </td>
                      <td>
                        <AccessStatusBadge status={statusById.get(item.assignmentId)} />
                      </td>
                      <td className={styles.muted}>
                        {item.grantedBy ?? "—"}
                        <div>{formatWhen(item.createdAt)}</div>
                      </td>
                      <td className={styles.muted}>{formatWhen(item.expiresAt)}</td>
                      <td>
                        {item.active && (
                          <button
                            type="button"
                            className={`${styles.button} ${styles.buttonDanger}`}
                            disabled={revoke.isPending}
                            aria-busy={revoke.isPending}
                            onClick={() => void revoke.mutateAsync(item.assignmentId)}
                          >
                            {revoke.isPending ? "Revoking…" : "Revoke"}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <TablePager
              total={pager.total}
              page={pager.page}
              pageSize={pager.pageSize}
              onPageChange={pager.setPage}
              onPageSizeChange={pager.setPageSize}
            />
          </>
        )}
        {revoke.error && <ErrorAlert error={revoke.error} title="Revoke failed" />}
      </Card>
    </div>
  );
}

/** Sub-view of the definitions tab: the list of existing definitions, or the create/edit form. */
type DefinitionsView = "existing" | "create";

type DefinitionPurpose = "sensitivity" | "data_access";

const SENSITIVITY_KEY = "sensitivity";
const DEFAULT_DATA_ACCESS_KEY = "data_access";

/**
 * Paginated table of attribute definitions with edit and delete actions.
 */
function DefinitionsTable({
  definitions,
  accessTagKey,
  deleting,
  onEdit,
  onDelete,
}: {
  definitions: AttributeDefinition[];
  accessTagKey: string;
  deleting: boolean;
  onEdit: (key: string) => void;
  onDelete: (key: string) => void;
}) {
  const columns: TableColumn<AttributeDefinition>[] = [
    {
      key: "key",
      label: "Key",
      value: (item) => item.key,
      filterText: (item) => `${item.key} ${item.description ?? ""}`,
    },
    {
      key: "purpose",
      label: "Purpose",
      value: (item) => definitionPurpose(item, accessTagKey).replaceAll("_", " "),
    },
    {
      key: "valueSource",
      label: "Value source",
      value: (item) =>
        item.valueSource !== "governed_tags" ? VALUE_SOURCE_LABELS[item.valueSource] : item.governedTagKeys.join(" "),
    },
    {
      key: "allowedValues",
      label: "Allowed values",
      value: (item) => item.allowedValues.length,
      filterText: (item) => item.allowedValues.join(" "),
    },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(definitions, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Attribute definitions">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => {
              const purposeLabel = definitionPurpose(item, accessTagKey);
              return (
                <tr key={item.key}>
                  <td>
                    <code>{item.key}</code>
                    {item.description && <div className={styles.muted}>{item.description}</div>}
                  </td>
                  <td>
                    <Badge
                      tone={
                        purposeLabel === "sensitivity" || purposeLabel === "data_access"
                          ? "info"
                          : "neutral"
                      }
                    >
                      {purposeLabel.replaceAll("_", " ")}
                    </Badge>
                  </td>
                  <td>
                    <div className={styles.chipRow}>
                      {item.valueSource !== "governed_tags" ? (
                        <Badge>{VALUE_SOURCE_LABELS[item.valueSource]}</Badge>
                      ) : item.governedTagKeys.length ? (
                        item.governedTagKeys.map((tagKey) => <Badge key={tagKey}>{tagKey}</Badge>)
                      ) : (
                        <span className={styles.muted}>Not linked</span>
                      )}
                    </div>
                  </td>
                  <td>
                    {item.allowedValues.length ? (
                      <div className={styles.chipRow}>
                        {item.allowedValues.slice(0, MAX_VALUE_CHIPS).map((value) => (
                          <Badge key={value}>{value}</Badge>
                        ))}
                        {item.allowedValues.length > MAX_VALUE_CHIPS && (
                          <span className={styles.muted}>
                            +{item.allowedValues.length - MAX_VALUE_CHIPS} more
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className={styles.muted}>Any value</span>
                    )}
                  </td>
                  <td>
                    <div className={styles.buttonRow}>
                      {item.valueSource === "governed_tags" ? (
                        <button
                          type="button"
                          className={styles.button}
                          onClick={() => onEdit(item.key)}
                        >
                          Edit
                        </button>
                      ) : (
                        <Link className={styles.button} to="/row-filters">
                          Edit in Row filters
                        </Link>
                      )}
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonDanger}`}
                        disabled={deleting}
                        aria-busy={deleting}
                        onClick={() => onDelete(item.key)}
                      >
                        {deleting ? "Deleting…" : "Delete"}
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </>
  );
}

/**
 * Classify a definition for list badges: sensitivity, data access, row filter, or other.
 */
function definitionPurpose(
  item: AttributeDefinition,
  accessTagKey: string,
): "sensitivity" | "data_access" | "row_filter" | "other" {
  if (item.key.toLowerCase() === SENSITIVITY_KEY) return "sensitivity";
  if (isDataAccessDefinition(item, accessTagKey)) return "data_access";
  if (item.valueSource !== "governed_tags") return "row_filter";
  return "other";
}

/**
 * Admin panel for the two required governed-tag definitions: sensitivity and data access.
 *
 * Row-filter dimensions are created on the Row filters page (unlimited). This form only allows
 * one sensitivity and one data-access definition.
 *
 * @remarks
 * Rendered only for app admins. The key is locked while editing an existing definition.
 */
function DefinitionsPanel() {
  const { data, isPending, error } = useAttributeDefinitions();
  const dataAccessSettings = useDataAccessSettings();
  const upsert = useUpsertAttributeDefinition();
  const remove = useDeleteAttributeDefinition();

  const accessTagKey =
    dataAccessSettings.data?.settings.accessTagKey?.trim() || "access_path";

  const [view, setView] = useState<DefinitionsView>("existing");
  const [purpose, setPurpose] = useState<DefinitionPurpose>("sensitivity");
  const [key, setKey] = useState(SENSITIVITY_KEY);
  const [description, setDescription] = useState("");
  const [tagSearch, setTagSearch] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([SENSITIVITY_KEY]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const tags = useGovernedTags(tagSearch.trim() || undefined);

  const sensitivityDef = data?.definitions.find((item) => item.key.toLowerCase() === SENSITIVITY_KEY);
  const dataAccessDef = data?.definitions.find((item) => isDataAccessDefinition(item, accessTagKey));
  const purposeAlreadyExists =
    !editingKey && (purpose === "sensitivity" ? Boolean(sensitivityDef) : Boolean(dataAccessDef));

  /** Adds or removes a governed tag key from the selection. */
  const toggleTag = (tagKey: string) => {
    if (
      purpose === "data_access" &&
      tagKey.toLowerCase() === accessTagKey.toLowerCase() &&
      selectedTags.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase())
    ) {
      return;
    }
    if (purpose === "sensitivity") {
      setSelectedTags([tagKey]);
      return;
    }
    setSelectedTags((current) =>
      current.includes(tagKey) ? current.filter((item) => item !== tagKey) : [...current, tagKey],
    );
  };

  /**
   * De-duplicated union of values across the selected tags, in selection order. Only tags present
   * in the current search results contribute, so this is a preview rather than the saved values.
   */
  const selectedValues = useMemo(() => {
    const byKey = new Map((tags.data?.tags ?? []).map((tag) => [tag.tagKey, tag]));
    const values: string[] = [];
    for (const tagKey of selectedTags) {
      for (const value of byKey.get(tagKey)?.values ?? []) {
        if (!values.includes(value)) values.push(value);
      }
    }
    return values;
  }, [selectedTags, tags.data]);

  /** Apply purpose defaults for key and tags. */
  const applyPurposeDefaults = (next: DefinitionPurpose) => {
    setPurpose(next);
    if (next === "sensitivity") {
      setKey(SENSITIVITY_KEY);
      setSelectedTags([SENSITIVITY_KEY]);
    } else {
      setKey(DEFAULT_DATA_ACCESS_KEY);
      setSelectedTags([accessTagKey]);
    }
  };

  /** Clears the create/edit form and exits edit mode. */
  const resetForm = () => {
    setPurpose("sensitivity");
    setKey(SENSITIVITY_KEY);
    setDescription("");
    setSelectedTags([SENSITIVITY_KEY]);
    setTagSearch("");
    setEditingKey(null);
  };

  /** Opens a blank create form, discarding any in-progress edit. */
  const openCreate = () => {
    resetForm();
    setView("create");
  };

  /** Returns to the definitions list, discarding any unsaved form state. */
  const openExisting = () => {
    resetForm();
    setView("existing");
  };

  /** Creates or updates the governed-tag-backed definition, then returns to the list on success. */
  const onSave = async () => {
    const resolvedKey =
      purpose === "sensitivity" ? SENSITIVITY_KEY : key.trim() || DEFAULT_DATA_ACCESS_KEY;
    const tagsForSave =
      purpose === "sensitivity"
        ? selectedTags.filter((tag) => tag.toLowerCase() !== accessTagKey.toLowerCase()).slice(0, 1)
        : purpose === "data_access" &&
            !selectedTags.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase())
          ? [accessTagKey, ...selectedTags]
          : selectedTags;
    await upsert.mutateAsync({
      key: resolvedKey,
      description: description.trim() || null,
      valueSource: "governed_tags",
      governedTagKeys: tagsForSave,
    });
    resetForm();
    setView("existing");
  };

  /**
   * Loads an existing definition into the form and switches to edit mode.
   *
   * @param definitionKey - Key of the definition to edit; ignored if it is not in the loaded list.
   */
  const startEdit = (definitionKey: string) => {
    const definition = data?.definitions.find((item) => item.key === definitionKey);
    if (!definition) return;
    const nextPurpose = definitionPurpose(definition, accessTagKey);
    setEditingKey(definition.key);
    setPurpose(nextPurpose === "data_access" ? "data_access" : "sensitivity");
    setKey(definition.key);
    setDescription(definition.description ?? "");
    if (nextPurpose === "sensitivity") {
      const sensitivityTags = definition.governedTagKeys.filter(
        (tag) => tag.toLowerCase() !== accessTagKey.toLowerCase(),
      );
      setSelectedTags(sensitivityTags.length ? [sensitivityTags[0] ?? SENSITIVITY_KEY] : [SENSITIVITY_KEY]);
    } else {
      setSelectedTags(
        !definition.governedTagKeys.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase())
          ? [accessTagKey, ...definition.governedTagKeys]
          : definition.governedTagKeys,
      );
    }
    setView("create");
  };

  const visibleTags = tags.data?.tags ?? [];
  const tagColumns: TableColumn<GovernedTagSummary>[] = [
    { key: "select", label: "" },
    { key: "tag", label: "Tag", value: (tag) => tag.tagKey },
    { key: "description", label: "Description", value: (tag) => tag.description ?? "" },
    {
      key: "values",
      label: "Values",
      value: (tag) => tag.valueCount,
      filterText: (tag) => tag.values.join(" "),
    },
  ];
  const tagView = useTableView(visibleTags, tagColumns, { paginate: false });
  const canSave =
    !purposeAlreadyExists &&
    selectedTags.length > 0 &&
    (purpose !== "sensitivity" || Boolean(dataAccessDef)) &&
    (purpose !== "data_access" ||
      selectedTags.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase())) &&
    (purpose === "sensitivity" || Boolean(key.trim()));

  return (
    <div className={styles.stack}>
      <div className={styles.tabs} role="tablist" aria-label="Definition views">
        <button
          type="button"
          role="tab"
          aria-selected={view === "existing"}
          className={`${styles.tab}${view === "existing" ? ` ${styles.tabActive}` : ""}`}
          onClick={openExisting}
        >
          Existing definitions
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={view === "create"}
          className={`${styles.tab}${view === "create" ? ` ${styles.tabActive}` : ""}`}
          onClick={openCreate}
        >
          {editingKey ? `Edit ${editingKey}` : "Create new definition"}
        </button>
      </div>

      {view === "existing" && (
        <Card title="Existing definitions" actions={data ? <Badge>{data.total}</Badge> : null}>
          <p className={styles.muted}>
            Configure one sensitivity definition and one data-access definition here. Create as many
            row-filter dimensions as you need under <Link to="/row-filters">Row filters</Link>.
          </p>
          {isPending && <Spinner label="Loading definitions" />}
          {error && <ErrorAlert error={error} title="Could not load definitions" />}
          {data && data.definitions.length === 0 && (
            <div className={styles.empty}>
              No attribute definitions yet.{" "}
              <button type="button" className={styles.button} onClick={openCreate}>
                Create new definition
              </button>
            </div>
          )}
          {data && data.definitions.length > 0 && (
            <DefinitionsTable
              definitions={data.definitions}
              accessTagKey={accessTagKey}
              deleting={remove.isPending}
              onEdit={startEdit}
              onDelete={(definitionKey) => void remove.mutateAsync(definitionKey)}
            />
          )}
          {remove.error && <ErrorAlert error={remove.error} title="Delete failed" />}
        </Card>
      )}

      {view === "create" && (
        <Card title={editingKey ? `Edit ${editingKey}` : "Create definition from governed tags"}>
          <p className={styles.muted}>
            Choose a purpose. Sensitivity and data access allow one definition each. Row-filter
            dimensions are managed under <Link to="/row-filters">Row filters</Link>.
          </p>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span className={styles.label}>Purpose</span>
              <select
                className={styles.select}
                aria-label="Definition purpose"
                value={purpose}
                disabled={Boolean(editingKey)}
                onChange={(event) => applyPurposeDefaults(event.target.value as DefinitionPurpose)}
              >
                <option value="sensitivity">Sensitivity</option>
                <option value="data_access">Data access</option>
              </select>
            </label>
            <label className={styles.field}>
              <span className={styles.label}>Attribute key</span>
              <input
                className={styles.input}
                value={key}
                disabled={Boolean(editingKey) || purpose === "sensitivity"}
                onChange={(event) => setKey(event.target.value)}
                placeholder={purpose === "sensitivity" ? SENSITIVITY_KEY : "e.g. data_access"}
                aria-label="Attribute key"
              />
            </label>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>Description</span>
              <input
                className={styles.input}
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="Optional; defaults to the tag description"
              />
            </label>
          </div>

          {purpose === "sensitivity" && !dataAccessDef && (
            <Alert title="Data-access definition required" tone="warning">
              Create the data-access definition first. Sensitivity inherits its access tag so the two
              vocabularies stay aligned.
            </Alert>
          )}

          {purposeAlreadyExists && (
            <Alert title="Already configured" tone="warning">
              A {purpose === "sensitivity" ? "sensitivity" : "data-access"} definition already exists
              as{" "}
              <code>
                {purpose === "sensitivity" ? sensitivityDef?.key : dataAccessDef?.key}
              </code>
              .{" "}
              <button
                type="button"
                className={styles.button}
                onClick={() =>
                  startEdit(
                    (purpose === "sensitivity" ? sensitivityDef?.key : dataAccessDef?.key) ?? "",
                  )
                }
              >
                Edit instead
              </button>
            </Alert>
          )}

          <div className={styles.stack} style={{ marginTop: "var(--space-3)" }}>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>
                Governed tags
                {purpose === "data_access"
                  ? ` (must include ${accessTagKey})`
                  : " (pick the sensitivity tag; the data-access tag is added automatically)"}
              </span>
              <input
                className={styles.input}
                value={tagSearch}
                onChange={(event) => setTagSearch(event.target.value)}
                placeholder="Search governed tags by name, description, or value"
              />
            </label>

            {purpose === "sensitivity" && dataAccessDef && (
              <div className={styles.muted}>
                Inherits access tag <code>{accessTagKey}</code> from{" "}
                <code>{dataAccessDef.key}</code>.
              </div>
            )}

            {selectedTags.length > 0 && (
              <div className={styles.chipRow}>
                {selectedTags.map((tagKey) => (
                  <button
                    key={tagKey}
                    type="button"
                    className={styles.button}
                    onClick={() => toggleTag(tagKey)}
                    aria-label={`Remove tag ${tagKey}`}
                    disabled={
                      purpose === "data_access" &&
                      tagKey.toLowerCase() === accessTagKey.toLowerCase()
                    }
                  >
                    {tagKey} ×
                  </button>
                ))}
              </div>
            )}

            {tags.isPending && <Spinner label="Loading governed tags" />}
            {tags.error && <ErrorAlert error={tags.error} title="Could not load governed tags" />}
            {tags.data && visibleTags.length === 0 && (
              <div className={styles.muted}>
                No governed tags match. Create tag policies in Unity Catalog first.
              </div>
            )}
            {visibleTags.length > 0 && (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <DataTableHead view={tagView} />
                  <tbody>
                    {tagView.rows.map((tag) => (
                      <tr key={tag.tagKey}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ${tag.tagKey}`}
                            checked={selectedTags.includes(tag.tagKey)}
                            onChange={() => toggleTag(tag.tagKey)}
                            disabled={purposeAlreadyExists}
                          />
                        </td>
                        <td>
                          <code>{tag.tagKey}</code>
                        </td>
                        <td className={styles.muted}>{tag.description || "—"}</td>
                        <td>
                          <div className={styles.chipRow}>
                            {tag.values.slice(0, 6).map((value) => (
                              <Badge key={value}>{value}</Badge>
                            ))}
                            {tag.valueCount > 6 && (
                              <span className={styles.muted}>+{tag.valueCount - 6} more</span>
                            )}
                            {tag.valueCount === 0 && <span className={styles.muted}>Free-form</span>}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {tags.data?.truncated && (
              <div className={styles.muted}>Showing the first 100 matches; refine the search to narrow.</div>
            )}

            {selectedTags.length > 0 && (
              <div>
                <span className={styles.label}>
                  Allowed values resolved from selected tags ({selectedValues.length})
                </span>
                <div className={styles.chipRow} style={{ marginTop: "var(--space-1)" }}>
                  {selectedValues.length ? (
                    selectedValues.map((value) => (
                      <Badge key={value} tone="info">
                        {value}
                      </Badge>
                    ))
                  ) : (
                    <span className={styles.muted}>Free-form (no values on the selected tags)</span>
                  )}
                </div>
              </div>
            )}
          </div>

          <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
            <button
              type="button"
              className={`${styles.button} ${styles.buttonPrimary}`}
              disabled={!canSave || upsert.isPending}
              onClick={() => void onSave()}
            >
              {upsert.isPending ? "Saving…" : editingKey ? "Save changes" : "Create definition"}
            </button>
            {editingKey && (
              <button type="button" className={styles.button} onClick={openExisting}>
                Cancel
              </button>
            )}
            {upsert.error && <ErrorAlert error={upsert.error} title="Save failed" />}
          </div>
        </Card>
      )}
    </div>
  );
}

