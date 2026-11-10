import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  useApproveAccessRequest,
  useApproveRenewal,
  useApprovalsInbox,
  useAssignAttribute,
  useAttributeAssignments,
  useAttributeDefinitions,
  useCancelAccessRequest,
  useCategoryApprovers,
  useCreateAccessRequest,
  useCurrentUser,
  useDeleteAttributeDefinition,
  useDeleteCategoryApprover,
  useDenyAccessRequest,
  useDismissRenewal,
  useGovernedTags,
  useMyAccessRequests,
  useMyAttributes,
  useRevokeAttribute,
  useUpsertAttributeDefinition,
  useUpsertCategoryApprover,
} from "@/api/queries";
import type { ApprovalInboxItem, PrincipalKind, ValueSource } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import styles from "./IdentityPage.module.css";

type IdentityTab =
  | "mine"
  | "request"
  | "my-requests"
  | "approvals"
  | "approvers"
  | "assignments"
  | "definitions";

const MAX_VALUE_CHIPS = 20;
const VALUE_SOURCE_LABELS: Record<ValueSource, string> = {
  governed_tags: "Governed tags",
  free_form: "Free-form list",
  sql: "SQL seed query",
};

const PRINCIPAL_TYPES: PrincipalKind[] = ["USER", "GROUP", "SERVICE_PRINCIPAL"];
const APPROVER_TYPES: PrincipalKind[] = ["USER", "GROUP"];
const USER_TABS: IdentityTab[] = ["mine", "request", "my-requests", "approvals"];
const ADMIN_TABS: IdentityTab[] = ["assignments", "definitions", "approvers"];

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function toLocalInput(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function IdentityPage() {
  const { data: user } = useCurrentUser();
  const [searchParams, setSearchParams] = useSearchParams();
  const requested = (searchParams.get("tab") as IdentityTab | null) ?? "mine";
  const allowed = new Set<IdentityTab>([
    ...USER_TABS,
    ...(user?.isAdmin ? ADMIN_TABS : []),
  ]);
  const tab: IdentityTab = allowed.has(requested) ? requested : "mine";

  const setTab = (next: IdentityTab) => {
    const params = new URLSearchParams(searchParams);
    params.set("tab", next);
    setSearchParams(params, { replace: true });
  };

  return (
    <>
      <PageHeader
        title="Identity attributes"
        description="App-managed attributes on users, groups, and service principals until Unity Catalog supports native identity attributes."
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
        <button
          type="button"
          role="tab"
          aria-selected={tab === "approvals"}
          className={`${styles.tab}${tab === "approvals" ? ` ${styles.tabActive}` : ""}`}
          onClick={() => setTab("approvals")}
        >
          Approvals
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
              Category approvers
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
      {tab === "approvals" && <ApprovalsPanel />}
      {tab === "approvers" && user?.isAdmin && <CategoryApproversPanel />}
      {tab === "assignments" && user?.isAdmin && <AssignmentsPanel />}
      {tab === "definitions" && user?.isAdmin && <DefinitionsPanel />}
    </>
  );
}

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
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Attribute</th>
                <th>Value</th>
                <th>Via</th>
                <th>Expires</th>
              </tr>
            </thead>
            <tbody>
              {data.assignments.map((item) => (
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
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function RequestAccessPanel() {
  const definitions = useAttributeDefinitions();
  const create = useCreateAccessRequest();
  const sensitivity = definitions.data?.definitions.find((item) => item.key === "sensitivity");
  const values = sensitivity?.allowedValues ?? [];
  const [attributeValue, setAttributeValue] = useState("");
  const [justification, setJustification] = useState("");
  const [expires, setExpires] = useState("");

  const onSubmit = async () => {
    await create.mutateAsync({
      attributeValue,
      justification: justification.trim(),
      requestedExpiresAt: new Date(expires).toISOString(),
    });
    setAttributeValue("");
    setJustification("");
    setExpires("");
  };

  return (
    <Card title="Request sensitivity access">
      {!sensitivity && !definitions.isPending && (
        <Alert tone="warning" title="Sensitivity definition missing">
          An admin must create the sensitivity attribute definition before requests can be submitted.
        </Alert>
      )}
      <div className={styles.formGrid}>
        <label className={styles.fieldGrow}>
          <span className={styles.label}>Sensitivity path</span>
          <select
            className={styles.select}
            value={attributeValue}
            onChange={(event) => setAttributeValue(event.target.value)}
            disabled={!values.length}
          >
            <option value="">Select path</option>
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
      <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
        <button
          type="button"
          className={`${styles.button} ${styles.buttonPrimary}`}
          disabled={!attributeValue || !justification.trim() || !expires || create.isPending}
          onClick={() => void onSubmit()}
        >
          {create.isPending ? "Submitting…" : "Submit request"}
        </button>
        {create.error && <ErrorAlert error={create.error} title="Request failed" />}
      </div>
    </Card>
  );
}

function MyRequestsPanel() {
  const { data, isPending, error } = useMyAccessRequests();
  const cancel = useCancelAccessRequest();

  return (
    <Card title="My requests" actions={data ? <Badge>{data.total}</Badge> : null}>
      {isPending && <Spinner label="Loading requests" />}
      {error && <ErrorAlert error={error} title="Could not load requests" />}
      {data && data.requests.length === 0 && <div className={styles.empty}>No access requests yet.</div>}
      {data && data.requests.length > 0 && (
        <div className={styles.tableWrap}>
          <table className={styles.table} aria-label="My access requests">
            <thead>
              <tr>
                <th>Path</th>
                <th>Status</th>
                <th>Requested expiry</th>
                <th>Submitted</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.requests.map((item) => (
                <tr key={item.requestId}>
                  <td>
                    <code>{item.attributeValue}</code>
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
                        disabled={cancel.isPending}
                        onClick={() => void cancel.mutateAsync(item.requestId)}
                      >
                        Cancel
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function ApprovalsPanel() {
  const { data, isPending, error } = useApprovalsInbox();
  const approveRequest = useApproveAccessRequest();
  const denyRequest = useDenyAccessRequest();
  const approveRenewal = useApproveRenewal();
  const dismissRenewal = useDismissRenewal();
  const [draftExpiry, setDraftExpiry] = useState<Record<string, string>>({});
  const [draftNote, setDraftNote] = useState<Record<string, string>>({});

  const keyFor = (item: ApprovalInboxItem) =>
    item.kind === "REQUEST" ? `req:${item.request?.requestId}` : `ren:${item.assignment?.assignmentId}`;

  return (
    <Card title="Approvals inbox" actions={data ? <Badge>{data.total}</Badge> : null}>
      <p className={styles.muted}>
        Pending sensitivity requests plus assignments expiring within 30 days. Most-specific category
        approvers decide; app admins can break glass.
      </p>
      {isPending && <Spinner label="Loading approvals" />}
      {error && <ErrorAlert error={error} title="Could not load approvals" />}
      {data && data.items.length === 0 && <div className={styles.empty}>Nothing waiting for your approval.</div>}
      {data && data.items.length > 0 && (
        <div className={styles.stack}>
          {data.items.map((item) => {
            const key = keyFor(item);
            const expiry = draftExpiry[key] ?? toLocalInput(item.expiresAt);
            const note = draftNote[key] ?? "";
            return (
              <div key={key} className={styles.approvalCard}>
                <div className={styles.toolbar}>
                  <Badge tone={item.kind === "RENEWAL" ? "warning" : "info"}>{item.kind}</Badge>
                  <code>{item.attributeValue}</code>
                  <span className={styles.muted}>{item.principalName}</span>
                  {item.resolvedCategory && <Badge>via {item.resolvedCategory}</Badge>}
                </div>
                {item.kind === "REQUEST" && item.request && (
                  <div className={styles.muted}>Justification: {item.request.justification}</div>
                )}
                <div className={styles.formGrid}>
                  <label className={styles.field}>
                    <span className={styles.label}>Approve until</span>
                    <input
                      className={styles.input}
                      type="datetime-local"
                      value={expiry}
                      onChange={(event) =>
                        setDraftExpiry((current) => ({ ...current, [key]: event.target.value }))
                      }
                    />
                  </label>
                  <label className={styles.fieldGrow}>
                    <span className={styles.label}>Note</span>
                    <input
                      className={styles.input}
                      value={note}
                      onChange={(event) =>
                        setDraftNote((current) => ({ ...current, [key]: event.target.value }))
                      }
                      placeholder="Optional"
                    />
                  </label>
                </div>
                <div className={styles.buttonRow}>
                  {item.kind === "REQUEST" && item.request && (
                    <>
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonPrimary}`}
                        disabled={!expiry || approveRequest.isPending}
                        onClick={() =>
                          void approveRequest.mutateAsync({
                            requestId: item.request!.requestId,
                            body: {
                              expiresAt: new Date(expiry).toISOString(),
                              note: note.trim() || null,
                            },
                          })
                        }
                      >
                        Approve
                      </button>
                      <button
                        type="button"
                        className={styles.button}
                        disabled={denyRequest.isPending}
                        onClick={() =>
                          void denyRequest.mutateAsync({
                            requestId: item.request!.requestId,
                            body: { note: note.trim() || null },
                          })
                        }
                      >
                        Deny
                      </button>
                    </>
                  )}
                  {item.kind === "RENEWAL" && item.assignment && (
                    <>
                      <button
                        type="button"
                        className={`${styles.button} ${styles.buttonPrimary}`}
                        disabled={!expiry || approveRenewal.isPending}
                        onClick={() =>
                          void approveRenewal.mutateAsync({
                            assignmentId: item.assignment!.assignmentId,
                            body: {
                              expiresAt: new Date(expiry).toISOString(),
                              note: note.trim() || null,
                            },
                          })
                        }
                      >
                        Renew
                      </button>
                      <button
                        type="button"
                        className={styles.button}
                        disabled={dismissRenewal.isPending}
                        onClick={() => void dismissRenewal.mutateAsync(item.assignment!.assignmentId)}
                      >
                        Dismiss
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}

function CategoryApproversPanel() {
  const { data, isPending, error } = useCategoryApprovers();
  const tags = useGovernedTags("sensitivity_category");
  const upsert = useUpsertCategoryApprover();
  const remove = useDeleteCategoryApprover();
  const categoryTag = tags.data?.tags.find((tag) => tag.tagKey === "sensitivity_category");
  const [categoryPath, setCategoryPath] = useState("");
  const [principalName, setPrincipalName] = useState("");
  const [principalType, setPrincipalType] = useState<PrincipalKind>("GROUP");

  return (
    <div className={styles.stack}>
      <Card title="Add category approver">
        <p className={styles.muted}>
          Approvers are set on sensitivity_category paths. The most specific path wins — an approver
          on finance.budget handles finance.budget… requests; finance approvers cover children only
          when no deeper category is configured.
        </p>
        <div className={styles.formGrid}>
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
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Principal</span>
            <input
              className={styles.input}
              value={principalName}
              onChange={(event) => setPrincipalName(event.target.value)}
              placeholder="user@example.com or group name"
            />
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!categoryPath || !principalName.trim() || upsert.isPending}
            onClick={() =>
              void upsert.mutateAsync({
                categoryPath,
                principalName: principalName.trim(),
                principalType,
              }).then(() => {
                setPrincipalName("");
              })
            }
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
          <div className={styles.empty}>No category approvers configured yet.</div>
        )}
        {data && data.approvers.length > 0 && (
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-label="Category approvers">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Principal</th>
                  <th>Type</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.approvers.map((item) => (
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
                        disabled={remove.isPending}
                        onClick={() => void remove.mutateAsync(item.approverId)}
                      >
                        Remove
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}

function AssignmentsPanel() {
  const definitions = useAttributeDefinitions();
  const [principalName, setPrincipalName] = useState("");
  const [principalType, setPrincipalType] = useState<PrincipalKind | "">("");
  const [attributeKey, setAttributeKey] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);

  const params = useMemo(
    () => ({
      principalName: principalName.trim() || undefined,
      principalType: principalType || undefined,
      attributeKey: attributeKey || undefined,
      includeInactive,
    }),
    [principalName, principalType, attributeKey, includeInactive],
  );
  const { data, isPending, error } = useAttributeAssignments(params);
  const assign = useAssignAttribute();
  const revoke = useRevokeAttribute();

  const [formPrincipal, setFormPrincipal] = useState("");
  const [formType, setFormType] = useState<PrincipalKind>("USER");
  const [formKey, setFormKey] = useState("");
  const [formValue, setFormValue] = useState("");
  const [formJustification, setFormJustification] = useState("");
  const [formExpires, setFormExpires] = useState("");

  const selectedDefinition = definitions.data?.definitions.find((item) => item.key === formKey);
  const valueOptions = selectedDefinition?.allowedValues ?? [];

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
            <input
              className={styles.input}
              value={formPrincipal}
              onChange={(event) => setFormPrincipal(event.target.value)}
              placeholder="user@example.com or group name"
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
              placeholder="Filter by principal"
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
        {data && data.assignments.length === 0 && (
          <div className={styles.empty}>No assignments match the current filters.</div>
        )}
        {data && data.assignments.length > 0 && (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Principal</th>
                  <th>Attribute</th>
                  <th>Value</th>
                  <th>Status</th>
                  <th>Granted</th>
                  <th>Expires</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.assignments.map((item) => (
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
                          onClick={() => void revoke.mutateAsync(item.assignmentId)}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {revoke.error && <ErrorAlert error={revoke.error} title="Revoke failed" />}
      </Card>
    </div>
  );
}

type DefinitionsView = "existing" | "create";

function DefinitionsPanel() {
  const { data, isPending, error } = useAttributeDefinitions();
  const upsert = useUpsertAttributeDefinition();
  const remove = useDeleteAttributeDefinition();

  const [view, setView] = useState<DefinitionsView>("existing");
  const [key, setKey] = useState("");
  const [description, setDescription] = useState("");
  const [tagSearch, setTagSearch] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const tags = useGovernedTags(tagSearch.trim() || undefined);

  const toggleTag = (tagKey: string) => {
    setSelectedTags((current) =>
      current.includes(tagKey) ? current.filter((item) => item !== tagKey) : [...current, tagKey],
    );
  };

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

  const resetForm = () => {
    setKey("");
    setDescription("");
    setSelectedTags([]);
    setTagSearch("");
    setEditingKey(null);
  };

  const openCreate = () => {
    resetForm();
    setView("create");
  };

  const openExisting = () => {
    resetForm();
    setView("existing");
  };

  const onSave = async () => {
    await upsert.mutateAsync({
      key: key.trim() || null,
      description: description.trim() || null,
      valueSource: "governed_tags",
      governedTagKeys: selectedTags,
    });
    resetForm();
    setView("existing");
  };

  const startEdit = (definitionKey: string) => {
    const definition = data?.definitions.find((item) => item.key === definitionKey);
    if (!definition) return;
    setEditingKey(definition.key);
    setKey(definition.key);
    setDescription(definition.description ?? "");
    setSelectedTags(definition.governedTagKeys);
    setView("create");
  };

  const visibleTags = tags.data?.tags ?? [];

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
            <div className={styles.tableWrap}>
              <table className={styles.table} aria-label="Attribute definitions">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Value source</th>
                    <th>Allowed values</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {data.definitions.map((item) => (
                    <tr key={item.key}>
                      <td>
                        <code>{item.key}</code>
                        {item.description && <div className={styles.muted}>{item.description}</div>}
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
                            <button type="button" className={styles.button} onClick={() => startEdit(item.key)}>
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
                            disabled={remove.isPending}
                            onClick={() => void remove.mutateAsync(item.key)}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {remove.error && <ErrorAlert error={remove.error} title="Delete failed" />}
        </Card>
      )}

      {view === "create" && (
        <Card title={editingKey ? `Edit ${editingKey}` : "Create definition from governed tags"}>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span className={styles.label}>Attribute key</span>
              <input
                className={styles.input}
                value={key}
                disabled={Boolean(editingKey)}
                onChange={(event) => setKey(event.target.value)}
                placeholder={selectedTags[0] ? `Defaults to ${selectedTags[0]}` : "e.g. data_access"}
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

          <div className={styles.stack} style={{ marginTop: "var(--space-3)" }}>
            <label className={styles.fieldGrow}>
              <span className={styles.label}>Governed tags (allowed values come from these)</span>
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
                  >
                    {tagKey} ×
                  </button>
                ))}
              </div>
            )}

            {tags.isPending && <Spinner label="Loading governed tags" />}
            {tags.error && <ErrorAlert error={tags.error} title="Could not load governed tags" />}
            {tags.data && visibleTags.length === 0 && (
              <div className={styles.muted}>No governed tags match. Create tag policies in Unity Catalog first.</div>
            )}
            {visibleTags.length > 0 && (
              <div className={styles.tableWrap}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th />
                      <th>Tag</th>
                      <th>Description</th>
                      <th>Values</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleTags.map((tag) => (
                      <tr key={tag.tagKey}>
                        <td>
                          <input
                            type="checkbox"
                            aria-label={`Select ${tag.tagKey}`}
                            checked={selectedTags.includes(tag.tagKey)}
                            onChange={() => toggleTag(tag.tagKey)}
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
                            {tag.valueCount > 6 && <span className={styles.muted}>+{tag.valueCount - 6} more</span>}
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
              disabled={selectedTags.length === 0 || upsert.isPending}
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
