/**
 * @file Subscriptions list: time-boxed access bundles, filtered by phase.
 *
 * Admins see every subscription; approvers see the ones they created. Rows open the detail page.
 */
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useSubscriptionOptions, useSubscriptions } from "@/api/queries";
import type { Subscription, SubscriptionPhase } from "@/api/types";
import { Alert, Badge, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import shared from "../identity/IdentityPage.module.css";
import styles from "./Subscriptions.module.css";
import { PHASE_LABELS, formatWhen, relativeTime } from "./subscriptionText";

type Filter = "all" | "pending" | "active" | "scheduled" | "ended";

const FILTERS: { id: Filter; label: string; phases: SubscriptionPhase[] }[] = [
  { id: "all", label: "All", phases: [] },
  { id: "pending", label: "Pending approval", phases: ["PENDING_APPROVAL"] },
  { id: "active", label: "Active", phases: ["ACTIVE", "EXPIRING"] },
  { id: "scheduled", label: "Scheduled", phases: ["SCHEDULED"] },
  { id: "ended", label: "Ended", phases: ["EXPIRED", "REVOKED", "DENIED"] },
];

/**
 * Subscriptions page.
 *
 * @returns The list with phase filters and a New subscription action.
 */
export function SubscriptionsPage() {
  const list = useSubscriptions();
  const options = useSubscriptionOptions();
  const [filter, setFilter] = useState<Filter>("all");
  const items = useMemo(() => list.data?.subscriptions ?? [], [list.data]);
  const counts = useMemo(() => {
    const out: Record<Filter, number> = { all: items.length, pending: 0, active: 0, scheduled: 0, ended: 0 };
    for (const f of FILTERS.slice(1)) out[f.id] = items.filter((s) => f.phases.includes(s.phase)).length;
    return out;
  }, [items]);
  const shown = useMemo(() => {
    const phases = FILTERS.find((f) => f.id === filter)?.phases ?? [];
    return phases.length ? items.filter((s) => phases.includes(s.phase)) : items;
  }, [items, filter]);

  return (
    <>
      <PageHeader
        title="Subscriptions"
        description="Time-boxed access to Unity Catalog objects and workspace folders. Each subscription owns one group; people are added when it starts and removed when it ends."
        actions={
          options.data?.canCreate ? (
            <Link to="/subscriptions/new" className={`${shared.button} ${shared.buttonPrimary}`}>
              New subscription
            </Link>
          ) : undefined
        }
      />
      {options.data?.requiresApproval && (
        <Alert tone="info">Subscriptions you create wait for an app admin to approve them.</Alert>
      )}
      <div className={styles.filters} role="tablist" aria-label="Filter subscriptions">
        {FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            role="tab"
            aria-selected={filter === f.id}
            className={`${styles.filter} ${filter === f.id ? styles.filterActive : ""}`}
            onClick={() => setFilter(f.id)}
          >
            {f.label}
            <span className={styles.count}>{counts[f.id]}</span>
          </button>
        ))}
      </div>
      {list.isPending && <Spinner label="Loading subscriptions" />}
      {list.error && <ErrorAlert error={list.error} title="Could not load subscriptions" />}
      {list.data && shown.length === 0 && (
        <p className={shared.empty}>
          {items.length === 0
            ? "No subscriptions yet. Create one to give a group time-boxed access."
            : "No subscriptions match this filter."}
        </p>
      )}
      {shown.length > 0 && <SubscriptionsTable items={shown} />}
    </>
  );
}

function SubscriptionsTable({ items }: { items: Subscription[] }) {
  const navigate = useNavigate();
  const columns: TableColumn<Subscription>[] = [
    {
      key: "name",
      label: "Subscription",
      value: (s) => s.displayName,
      filterText: (s) => `${s.displayName} ${s.name} ${s.groupName}`,
    },
    { key: "phase", label: "Status", value: (s) => PHASE_LABELS[s.phase].label },
    { key: "ends", label: "Ends", value: (s) => s.expiresAt },
    { key: "people", label: "People", value: (s) => s.subjects.length },
    { key: "resources", label: "Resources", value: (s) => s.resources.length },
    { key: "sync", label: "Last sync", value: (s) => s.lastSync?.syncedAt ?? "" },
  ];
  const view = useTableView(items, columns);
  return (
    <>
      <div className={shared.tableWrap}>
        <table className={shared.table} aria-label="Subscriptions">
          <DataTableHead view={view} />
          <tbody>
            {view.rows.map((s) => (
              <tr
                key={s.subscriptionId}
                className={shared.tableRowClickable}
                onClick={() => navigate(`/subscriptions/${s.subscriptionId}`)}
              >
                <td>
                  <Link to={`/subscriptions/${s.subscriptionId}`} className={styles.title}>
                    {s.displayName}
                  </Link>
                  <div className={styles.mono}>{s.groupName}</div>
                </td>
                <td>
                  <Badge tone={PHASE_LABELS[s.phase].tone}>{PHASE_LABELS[s.phase].label}</Badge>
                </td>
                <td>
                  <div>{formatWhen(s.expiresAt)}</div>
                  <div className={styles.sub}>{relativeTime(s.expiresAt)}</div>
                </td>
                <td>{s.subjects.length}</td>
                <td>{s.resources.length}</td>
                <td>
                  {s.lastSync ? (
                    <Badge tone={s.lastSync.ok ? "success" : "danger"}>
                      {s.lastSync.ok ? relativeTime(s.lastSync.syncedAt) : "errors"}
                    </Badge>
                  ) : (
                    <span className={shared.muted}>never</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={view.total}
        page={view.page}
        pageSize={view.pageSize}
        onPageChange={view.setPage}
        onPageSizeChange={view.setPageSize}
      />
    </>
  );
}
