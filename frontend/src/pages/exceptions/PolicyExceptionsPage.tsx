/**
 * @file Admin page for managing global ABAC policy exceptions (EXCEPT-list principals).
 *
 * Lists, adds, and removes principals exempt from app-managed column masks and row filters, and
 * can re-apply the exception list onto mask/row-filter policy packs.
 *
 * @remarks Admin-only UI. Non-admins see a warning; list/mutations still rely on backend auth.
 */
import { useState } from "react";
import {
  useApplyPolicyExceptions,
  useDeletePolicyException,
  usePolicyExceptions,
  useUpsertPolicyException,
} from "@/api/queries";
import { useEffectiveUser } from "@/app/viewAs";
import type { PolicyException, PrincipalKind } from "@/api/types";
import { PrincipalPicker } from "@/components/PrincipalPicker";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import styles from "../identity/IdentityPage.module.css";

/** Principal kinds selectable when adding a configured policy exception. */
const PRINCIPAL_TYPES: PrincipalKind[] = ["USER", "GROUP", "SERVICE_PRINCIPAL"];

/**
 * Table of configured and system policy exceptions with client-side pagination.
 */
function ExceptionsTable({
  exceptions,
  removing,
  onRemove,
}: {
  /** All exceptions returned by the API. */
  exceptions: PolicyException[];
  /** True while a remove mutation is in flight. */
  removing: boolean;
  /** Called with the exception id when the user clicks Remove. */
  onRemove: (exceptionId: string) => void;
}) {
  const columns: TableColumn<(typeof exceptions)[number]>[] = [
    {
      key: "principal",
      label: "Principal",
      value: (item) => item.principalName,
      filterText: (item) => `${item.principalType} ${item.principalName}`,
    },
    { key: "type", label: "Type", value: (item) => item.principalType },
    { key: "note", label: "Note", value: (item) => item.note ?? "" },
    { key: "source", label: "Source", value: (item) => (item.system ? "system" : "configured") },
    { key: "actions", label: "" },
  ];
  const pager = useTableView(exceptions, columns);

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table} aria-label="Policy exceptions">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((item) => (
              <tr key={item.exceptionId}>
                <td>
                  <code>{item.principalName}</code>
                </td>
                <td>
                  <Badge>{item.principalType}</Badge>
                </td>
                <td className={styles.muted}>{item.note ?? "—"}</td>
                <td>
                  <Badge tone={item.system ? "info" : "neutral"}>
                    {item.system ? "system" : "configured"}
                  </Badge>
                </td>
                <td>
                  {!item.system && (
                    <button
                      type="button"
                      className={styles.button}
                      disabled={removing}
                      onClick={() => onRemove(item.exceptionId)}
                    >
                      Remove
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
 * Admin UI to add/remove policy exceptions and re-apply them to ABAC packs.
 *
 * Uses `useEffectiveUser` for role gating, `usePolicyExceptions` (enabled only for admins),
 * `useUpsertPolicyException`, `useDeletePolicyException`, and `useApplyPolicyExceptions`.
 * Saving an exception regenerates mask/row-filter packs server-side; "Re-apply" forces a sync.
 *
 * @remarks Non-admin users get a read-only warning shell; the backend enforces authorization.
 * Respects admin demo preview.
 */
export function PolicyExceptionsPage() {
  const { data: user } = useEffectiveUser();
  const list = usePolicyExceptions(Boolean(user?.isAdmin));
  const upsert = useUpsertPolicyException();
  const remove = useDeletePolicyException();
  const apply = useApplyPolicyExceptions();
  const [principalName, setPrincipalName] = useState("");
  const [principalType, setPrincipalType] = useState<PrincipalKind>("GROUP");
  const [note, setNote] = useState("");

  if (user && !user.isAdmin) {
    return (
      <>
        <PageHeader
          title="Policy exceptions"
          description="Principals exempt from app-managed column masks and row filters."
        />
        <Alert tone="warning" title="Admins only">
          Only app admins can manage global ABAC exceptions.
        </Alert>
      </>
    );
  }

  /** Upserts the form principal as a configured exception and clears name/note fields. */
  const onAdd = async () => {
    await upsert.mutateAsync({
      principalName: principalName.trim(),
      principalType,
      note: note.trim() || null,
    });
    setPrincipalName("");
    setNote("");
  };

  return (
    <>
      <PageHeader
        title="Policy exceptions"
        description="Users, groups, and service principals listed here are added to the EXCEPT clause on app-managed mask and row-filter policies so they see clear values and all rows."
        actions={
          <button
            type="button"
            className={styles.button}
            disabled={apply.isPending}
            onClick={() => void apply.mutateAsync()}
          >
            {apply.isPending ? "Applying…" : "Re-apply to policies"}
          </button>
        }
      />

      <Card title="Add exception">
        <p className={styles.muted}>
          Use this for Data Service principals, break-glass groups, or other identities that need
          full access without sensitivity or row-filter attributes. App and admin service principals
          are always included as built-in system exceptions. Saving regenerates the mask and
          row-filter packs.
        </p>
        <div className={styles.formGrid}>
          <label className={styles.field}>
            <span className={styles.label}>Principal type</span>
            <select
              className={styles.select}
              value={principalType}
              onChange={(event) => setPrincipalType(event.target.value as PrincipalKind)}
            >
              {PRINCIPAL_TYPES.map((type) => (
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
              kind={principalType === "SERVICE_PRINCIPAL" ? undefined : principalType}
              placeholder="Search users or groups…"
              onChange={(name, nextKind) => {
                setPrincipalName(name);
                if (nextKind) setPrincipalType(nextKind);
              }}
            />
          </label>
          <label className={styles.fieldGrow}>
            <span className={styles.label}>Note</span>
            <input
              className={styles.input}
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="Why this principal needs full access"
            />
          </label>
        </div>
        <div className={styles.buttonRow} style={{ marginTop: "var(--space-3)" }}>
          <button
            type="button"
            className={`${styles.button} ${styles.buttonPrimary}`}
            disabled={!principalName.trim() || upsert.isPending}
            onClick={() => void onAdd()}
          >
            {upsert.isPending ? "Saving…" : "Add exception"}
          </button>
          {upsert.error && <ErrorAlert error={upsert.error} title="Could not save exception" />}
          {apply.error && <ErrorAlert error={apply.error} title="Could not apply exceptions" />}
          {apply.data && (
            <Alert tone="success" title="Policies updated">
              EXCEPT list now has {apply.data.exceptPrincipals.length} principal(s). Mask pack:{" "}
              {apply.data.maskPackSynced ? "synced" : "skipped"}; row-filter pack:{" "}
              {apply.data.rowFilterPackSynced ? "synced" : "skipped"}.
            </Alert>
          )}
        </div>
      </Card>

      <Card title="Current exceptions" actions={list.data ? <Badge>{list.data.total}</Badge> : null}>
        {list.isPending && <Spinner label="Loading exceptions" />}
        {list.error && <ErrorAlert error={list.error} title="Could not load exceptions" />}
        {list.data && list.data.exceptions.length === 0 && (
          <div className={styles.empty}>No exceptions configured yet.</div>
        )}
        {list.data && list.data.exceptions.length > 0 && (
          <ExceptionsTable
            exceptions={list.data.exceptions}
            removing={remove.isPending}
            onRemove={(id) => void remove.mutateAsync(id)}
          />
        )}
        {remove.error && <ErrorAlert error={remove.error} title="Could not remove exception" />}
      </Card>
    </>
  );
}
