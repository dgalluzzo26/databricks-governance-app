/**
 * @file Identity attributes page: self-service attribute viewing and access requests, and admin
 * management of category approvers, attribute assignments, and attribute definitions.
 *
 * Approvals live on `/approvals`. The active tab is stored in the `?tab=` search parameter.
 * Every signed-in user sees "My attributes", "Request access", and "My requests". "Approvers",
 * "Assignments", and "Definitions" are shown only to app admins. Legacy `?tab=approvals`
 * redirects to `/approvals`. This gating is a UI convenience only; the backend enforces
 * authorization on every endpoint.
 */
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router-dom";
import {
  useAccessStatus,
  useAssignAttribute,
  useAttributeAssignments,
  useAttributeDefinitions,
  useCancelAccessRequest,
  useCategoryApprovers,
  useCreateAccessRequest,
  useDataAccessSettings,
  useDeleteAttributeDefinition,
  useDeleteCategoryApprover,
  useGovernedTags,
  useMyAccessRequests,
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
  CategoryApprover,
  GovernedTagSummary,
  IdentityAttributeAssignment,
  PrincipalKind,
  ValueSource,
} from "@/api/types";
import { AccessStatusBadge } from "@/components/AccessStatusBadge";
import { accessStatusLabel } from "@/components/AccessStatusBadge";
import { PrincipalPicker } from "@/components/PrincipalPicker";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import styles from "./IdentityPage.module.css";

/**
 * Identifier of a top-level tab on the identity page, as stored in the `?tab=` search parameter.
 *
 * @remarks
 * `"approvers"`, `"assignments"`, and `"definitions"` require admin status.
 * See {@link USER_TABS} and {@link ADMIN_TABS}.
 */
type IdentityTab =
  | "mine"
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
/** Principal kinds that can be configured as category approvers (service principals are excluded). */
const APPROVER_TYPES: PrincipalKind[] = ["USER", "GROUP"];
/** Tabs available to every signed-in user. */
const USER_TABS: IdentityTab[] = ["mine", "request", "my-requests"];
/** Tabs available only to app admins. */
const ADMIN_TABS: IdentityTab[] = ["assignments", "definitions", "approvers"];

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
 * Tabs are filtered by role: everyone gets {@link USER_TABS}; admins also get {@link ADMIN_TABS}.
 * A disallowed or unknown `tab` value silently falls back to `"mine"`. Respects admin demo preview.
 */
export function IdentityPage() {
  const { data: user } = useEffectiveUser();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawTab = searchParams.get("tab");
  if (rawTab === "approvals") {
    return <Navigate to="/approvals" replace />;
  }
  const requested = (rawTab as IdentityTab | null) ?? "mine";
  const allowed = new Set<IdentityTab>([
    ...USER_TABS,
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
              aria-selected={tab === "assignments"}
              className={`${styles.tab}${tab === "assignments" ? ` ${styles.tabActive}` : ""}`}
              onClick={() => setTab("assignments")}
            >
              Assignments
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
      {tab === "request" && <RequestAccessPanel />}
      {tab === "my-requests" && <MyRequestsPanel />}
      {tab === "approvers" && user?.isAdmin && <CategoryApproversPanel />}
      {tab === "assignments" && user?.isAdmin && <AssignmentsPanel />}
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
          <select
            className={styles.select}
            value={attributeValue}
            onChange={(event) => setAttributeValue(event.target.value)}
            disabled={!values.length}
          >
            <option value="">Select value</option>
            {values.map((value) => (
              <option key={value} value={value}>
                {value}
              </option>
            ))}
          </select>
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
 * "Approvers" tab: configure who reviews sensitivity, data-access, and row-filter requests.
 *
 * Uses governed sensitivity tags, the data-access definition vocabulary, and
 * `useRowFilterDimensions` for pickers, plus `useCategoryApprovers` for the list.
 *
 * @remarks
 * Rendered only for app admins. Sensitivity and data-access use most-specific path routing.
 * Row-filter dimensions use exact designated approvers and fall back to app admins when none
 * are configured.
 */
function CategoryApproversPanel() {
  const { data, isPending, error } = useCategoryApprovers();
  const tags = useGovernedTags("sensitivity_category");
  const definitions = useAttributeDefinitions();
  const dataAccessSettings = useDataAccessSettings();
  const dimensions = useRowFilterDimensions();
  const upsert = useUpsertCategoryApprover();
  const remove = useDeleteCategoryApprover();
  const categoryTag = tags.data?.tags.find((tag) => tag.tagKey === "sensitivity_category");
  const accessTagKey =
    dataAccessSettings.data?.settings.accessTagKey?.trim().toLowerCase() || "data_access_control";
  const dataAccessDef = (definitions.data?.definitions ?? []).find((item) => {
    const key = item.key.toLowerCase();
    return (
      key === accessTagKey ||
      item.governedTagKeys.some((tag) => tag.toLowerCase() === accessTagKey)
    );
  });
  const dataAccessPaths = (dataAccessDef?.allowedValues ?? []).filter((value) => value && value !== "*");
  const [scope, setScope] = useState<"sensitivity" | "data_access" | "row_filter">("sensitivity");
  const [categoryPath, setCategoryPath] = useState("");
  const [dataAccessPath, setDataAccessPath] = useState("");
  const [dimensionKey, setDimensionKey] = useState("");
  const [principalName, setPrincipalName] = useState("");
  const [principalType, setPrincipalType] = useState<PrincipalKind>("GROUP");

  const pathReady =
    scope === "sensitivity"
      ? Boolean(categoryPath)
      : scope === "data_access"
        ? Boolean(dataAccessPath)
        : Boolean(dimensionKey);

  return (
    <div className={styles.stack}>
      <Card title="Add approver">
        <p className={styles.muted}>
          Sensitivity and data-access approvers are set on vocabulary paths (most specific path
          wins). Row-filter approvers are set per dimension; when none are configured, app admins
          review those requests.
        </p>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Scope</span>
            <select
              className={styles.select}
              value={scope}
              onChange={(event) => {
                setScope(event.target.value as "sensitivity" | "data_access" | "row_filter");
                setCategoryPath("");
                setDataAccessPath("");
                setDimensionKey("");
              }}
            >
              <option value="sensitivity">Sensitivity category</option>
              <option value="data_access">Data-access path</option>
              <option value="row_filter">Row-filter dimension</option>
            </select>
          </label>
          {scope === "sensitivity" ? (
            <label className={styles.field}>
              <span className={styles.label}>Category</span>
              <select
                className={styles.select}
                value={categoryPath}
                onChange={(event) => setCategoryPath(event.target.value)}
              >
                <option value="">Select category</option>
                {(categoryTag?.values ?? []).map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          ) : scope === "data_access" ? (
            <label className={styles.field}>
              <span className={styles.label}>Data-access path</span>
              <select
                className={styles.select}
                value={dataAccessPath}
                onChange={(event) => setDataAccessPath(event.target.value)}
                disabled={!dataAccessDef}
              >
                <option value="">
                  {dataAccessDef ? "Select path" : "Create a data-access definition first"}
                </option>
                {dataAccessPaths.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <label className={styles.field}>
              <span className={styles.label}>Dimension</span>
              <select
                className={styles.select}
                value={dimensionKey}
                onChange={(event) => setDimensionKey(event.target.value)}
              >
                <option value="">Select dimension</option>
                {(dimensions.data?.dimensions ?? []).map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.key}
                  </option>
                ))}
              </select>
            </label>
          )}
          <label className={styles.field}>
            <span className={styles.label}>Principal type</span>
            <select
              className={styles.select}
              value={principalType}
              onChange={(event) => setPrincipalType(event.target.value as PrincipalKind)}
            >
              {APPROVER_TYPES.map((type) => (
                <option key={type} value={type}>
                  {type}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Principal</span>
            <PrincipalPicker
              value={principalName}
              kind={principalType}
              placeholder="Search users or groups…"
              onChange={(name, nextKind) => {
                setPrincipalName(name);
                if (nextKind === "USER" || nextKind === "GROUP") setPrincipalType(nextKind);
              }}
            />
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!pathReady || !principalName.trim() || upsert.isPending}
            onClick={() => {
              const attributeKey =
                scope === "sensitivity"
                  ? "sensitivity"
                  : scope === "data_access"
                    ? (dataAccessDef?.key ?? "data_access")
                    : dimensionKey;
              const path =
                scope === "sensitivity"
                  ? categoryPath
                  : scope === "data_access"
                    ? dataAccessPath
                    : dimensionKey;
              void upsert
                .mutateAsync({
                  attributeKey,
                  categoryPath: path,
                  principalName: principalName.trim(),
                  principalType,
                })
                .then(() => {
                  setPrincipalName("");
                });
            }}
          >
            {upsert.isPending ? "Saving…" : "Add approver"}
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
    { key: "attribute", label: "Attribute", value: (item) => item.attributeKey },
    { key: "path", label: "Path / dimension", value: (item) => item.categoryPath },
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
                  <code>{item.attributeKey}</code>
                </td>
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
 * "Assignments" tab: directly assign attribute values to principals and browse or revoke existing
 * assignments.
 *
 * The "Assign attribute" form uses `useAttributeDefinitions` for the attribute picker; when the
 * selected definition has `allowedValues` the value is a select, otherwise free text. Submitting
 * calls `useAssignAttribute` (justification and expiry are optional and sent as `null` when
 * blank), then clears the value, justification, and expiry while keeping principal and attribute.
 *
 * The "Assignments" table uses `useAttributeAssignments`, filtered by principal name, principal
 * type, attribute key, and an "Include inactive" toggle. Active rows can be revoked via
 * `useRevokeAttribute`.
 *
 * @remarks
 * Rendered only for app admins. Direct assignments bypass the request/approval workflow.
 */
function AssignmentsPanel() {
  const definitions = useAttributeDefinitions();
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
  const assign = useAssignAttribute();
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

  const [formPrincipal, setFormPrincipal] = useState("");
  const [formType, setFormType] = useState<PrincipalKind>("USER");
  const [formKey, setFormKey] = useState("");
  const [formValue, setFormValue] = useState("");
  const [formJustification, setFormJustification] = useState("");
  const [formExpires, setFormExpires] = useState("");

  const selectedDefinition = definitions.data?.definitions.find((item) => item.key === formKey);
  const valueOptions = selectedDefinition?.allowedValues ?? [];

  /** Creates the assignment from the form and resets the value, justification, and expiry on success. */
  const onAssign = async () => {
    await assign.mutateAsync({
      principalName: formPrincipal.trim(),
      principalType: formType,
      attributeKey: formKey,
      attributeValue: formValue.trim(),
      justification: formJustification.trim() || null,
      expiresAt: formExpires ? new Date(formExpires).toISOString() : null,
    });
    setFormValue("");
    setFormJustification("");
    setFormExpires("");
  };

  return (
    <div className={styles.stack}>
      <Card title="Assign attribute">
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Principal</span>
            <PrincipalPicker
              value={formPrincipal}
              kind={formType === "SERVICE_PRINCIPAL" ? undefined : formType}
              placeholder="Search users or groups…"
              onChange={(name, nextKind) => {
                setFormPrincipal(name);
                if (nextKind) setFormType(nextKind);
              }}
            />
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Principal type</span>
            <select
              className={styles.select}
              value={formType}
              onChange={(event) => setFormType(event.target.value as PrincipalKind)}
            >
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
              value={formKey}
              onChange={(event) => {
                setFormKey(event.target.value);
                setFormValue("");
              }}
            >
              <option value="">Select attribute</option>
              {(definitions.data?.definitions ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.key}
                </option>
              ))}
            </select>
          </label>
          <label className={styles.field}>
            <span className={styles.label}>Value</span>
            {valueOptions.length ? (
              <select className={styles.select} value={formValue} onChange={(event) => setFormValue(event.target.value)}>
                <option value="">Select value</option>
                {valueOptions.map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            ) : (
              <input
                className={styles.input}
                value={formValue}
                onChange={(event) => setFormValue(event.target.value)}
                placeholder="finance or finance.budget"
              />
            )}
          </label>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Justification</span>
            <input
              className={styles.input}
              value={formJustification}
              onChange={(event) => setFormJustification(event.target.value)}
              placeholder="Optional"
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
            disabled={!formPrincipal.trim() || !formKey || !formValue.trim() || assign.isPending}
            onClick={() => void onAssign()}
          >
            {assign.isPending ? "Assigning…" : "Assign"}
          </button>
          {assign.error && <ErrorAlert error={assign.error} title="Assign failed" />}
        </div>
        {(definitions.data?.definitions.length ?? 0) === 0 && (
          <Alert tone="warning" title="No attribute definitions yet">
            Create a definition on the Definitions tab before assigning values.
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
              {(definitions.data?.definitions ?? []).map((item) => (
                <option key={item.key} value={item.key}>
                  {item.key}
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
const SENSITIVITY_TAGS = ["sensitivity_category", "sensitivity"];
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
  const access = accessTagKey.toLowerCase();
  if (
    item.key.toLowerCase() === access ||
    item.governedTagKeys.some((tag) => tag.toLowerCase() === access)
  ) {
    return "data_access";
  }
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
    dataAccessSettings.data?.settings.accessTagKey?.trim() || "data_access_control";

  const [view, setView] = useState<DefinitionsView>("existing");
  const [purpose, setPurpose] = useState<DefinitionPurpose>("sensitivity");
  const [key, setKey] = useState(SENSITIVITY_KEY);
  const [description, setDescription] = useState("");
  const [tagSearch, setTagSearch] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([...SENSITIVITY_TAGS]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const tags = useGovernedTags(tagSearch.trim() || undefined);

  const sensitivityDef = data?.definitions.find((item) => item.key.toLowerCase() === SENSITIVITY_KEY);
  const dataAccessDef = data?.definitions.find(
    (item) =>
      item.key.toLowerCase() === accessTagKey.toLowerCase() ||
      item.governedTagKeys.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase()),
  );
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
      setSelectedTags([...SENSITIVITY_TAGS]);
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
    setSelectedTags([...SENSITIVITY_TAGS]);
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
      purpose === "data_access" &&
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
    setSelectedTags(
      nextPurpose === "data_access" &&
        !definition.governedTagKeys.some((tag) => tag.toLowerCase() === accessTagKey.toLowerCase())
        ? [accessTagKey, ...definition.governedTagKeys]
        : definition.governedTagKeys,
    );
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
                  : " (sensitivity_category + sensitivity recommended)"}
              </span>
              <input
                className={styles.input}
                value={tagSearch}
                onChange={(event) => setTagSearch(event.target.value)}
                placeholder="Search governed tags by name, description, or value"
              />
            </label>

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

