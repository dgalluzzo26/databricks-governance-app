/**
 * @file "Outstanding work" tab on the data-access page: what the sync still has to do, and which
 * tables changed tags since the last sync.
 *
 * Counts and lists come from the last sync run (the full list, not the 25-row plan samples).
 * Approvals and revokes update membership immediately, so their rows disappear without a sync;
 * group creation, nesting, grants, and expiries wait for the next sync. Pages are fetched from
 * the server (1-based); {@link TablePager} works in 0-based indexes.
 */
import { useState } from "react";
import {
  useDataAccessPending,
  useDataAccessPendingItems,
  useDataAccessTagChanges,
} from "@/api/queries";
import type { GapItem, GapKind, PendingCounts, TagChangeKind } from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, Spinner, TablePager } from "@/components/ui";
import type { TablePageSize } from "@/components/ui";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./DataAccessAnalyticsPage.module.css";

type KindFilter = GapKind | "expired" | "all";

/** Labels for each outstanding item kind, in the order the sync applies them. */
export const GAP_KIND_LABELS: Record<GapKind | "expired", string> = {
  group_create: "Groups to create",
  group_nest: "Nesting to add",
  member_add: "Members to add",
  member_remove: "Members to remove",
  grant_add: "Grants to add",
  grant_revoke: "Grants to revoke",
  expired: "Expired since sync",
};

const KIND_COUNT: Record<GapKind | "expired", keyof PendingCounts> = {
  group_create: "groupCreate",
  group_nest: "groupNest",
  member_add: "memberAdd",
  member_remove: "memberRemove",
  grant_add: "grantAdd",
  grant_revoke: "grantRevoke",
  expired: "expiredSinceSync",
};

const TAG_CHANGE_LABELS: Record<TagChangeKind, string> = {
  newly_eligible: "Newly eligible",
  no_longer_eligible: "No longer eligible",
  value_changed: "Value changed",
};

/** Formats an ISO timestamp for display, or an em dash when absent. */
function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

/** One-line description of what an outstanding item changes. */
export function describeGapItem(item: GapItem): string {
  switch (item.kind) {
    case "group_create":
      return `Create ${item.groupName ?? "group"}`;
    case "group_nest":
      return `Nest ${item.principalName ?? "parent"} into ${item.groupName ?? "group"}`;
    case "member_add":
      return `Add ${item.principalName ?? "principal"} to ${item.groupName ?? "group"}`;
    case "member_remove":
      return `Remove ${item.principalName ?? "principal"} from ${item.groupName ?? "group"}`;
    case "grant_add":
      return `GRANT ${item.privilege ?? ""} on ${item.securableType ?? ""} ${item.fullName ?? ""} to ${item.groupName ?? ""}`;
    case "grant_revoke":
      return `REVOKE ${item.privilege ?? ""} on ${item.securableType ?? ""} ${item.fullName ?? ""} from ${item.groupName ?? ""}`;
  }
}

/** Shared pager wiring: converts the server's 1-based page to TablePager's 0-based index. */
function usePaging() {
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(25);
  return { pageIndex, pageSize, page: pageIndex + 1, setPageIndex, setPageSize };
}

/**
 * Outstanding sync work: counts by kind, a filterable paged list, and tag changes.
 */
export function OutstandingWorkPanel() {
  const summary = useDataAccessPending();
  const [kind, setKind] = useState<KindFilter>("all");
  const meta = summary.data?.meta ?? null;
  const counts = summary.data?.counts;

  return (
    <div className={identityStyles.stack}>
      {summary.isPending && <Spinner label="Loading outstanding work" />}
      {summary.error && <ErrorAlert error={summary.error} title="Could not load outstanding work" />}
      {summary.data && meta === null && (
        <Alert tone="info" title="No sync has run yet">
          Run a dry-run or apply to compute what is outstanding. Approvals already add and remove
          members immediately.
        </Alert>
      )}
      {summary.data && meta && counts && (
        <Card
          title="Outstanding work"
          actions={<Badge tone={summary.data.total ? "warning" : "success"}>{summary.data.total}</Badge>}
        >
          <p className={identityStyles.muted}>
            From the {meta.mode === "apply" ? "live apply" : "dry run"} at {when(meta.computedAt)} ·{" "}
            {meta.tablesTracked} tagged table{meta.tablesTracked === 1 ? "" : "s"} tracked.{" "}
            {meta.mode === "apply"
              ? "Only changes that failed to apply remain, plus approvals that could not be applied."
              : "A dry run lists everything the next apply will change."}{" "}
            Approvals and revokes change membership immediately; groups, nesting, grants, and
            expiries wait for the next sync.
          </p>
          <div className={styles.kpiGrid} aria-label="Outstanding counts">
            {(Object.keys(GAP_KIND_LABELS) as Array<GapKind | "expired">).map((key) => {
              const value = counts[KIND_COUNT[key]];
              const active = kind === key;
              return (
                <button
                  key={key}
                  type="button"
                  aria-pressed={active}
                  className={`${styles.kpi} ${styles.kpiButton}${active ? ` ${styles.kpiActive}` : ""}`}
                  onClick={() => setKind(active ? "all" : key)}
                >
                  <span className={styles.kpiLabel}>{GAP_KIND_LABELS[key]}</span>
                  <span className={styles.kpiValue}>{value}</span>
                </button>
              );
            })}
            <div className={styles.kpi}>
              <span className={styles.kpiLabel}>Failed on last apply</span>
              <span className={`${styles.kpiValue}${counts.failed ? ` ${styles.kpiDanger}` : ""}`}>
                {counts.failed}
              </span>
            </div>
          </div>
          <OutstandingItemsTable kind={kind} onKindChange={setKind} />
        </Card>
      )}
      {summary.data && meta && (
        <Card title="Tag changes">
          {meta.tagBaseline ? (
            <p className={identityStyles.muted}>
              The last sync took the first tag snapshot; changes appear from the next sync on.
            </p>
          ) : (
            <p className={identityStyles.muted}>
              Last sync: {summary.data.tagChanges.newlyEligible} newly eligible ·{" "}
              {summary.data.tagChanges.noLongerEligible} no longer eligible ·{" "}
              {summary.data.tagChanges.valueChanged} value changed.
            </p>
          )}
          <TagChangesTable />
        </Card>
      )}
    </div>
  );
}

/** Filterable, server-paged list of outstanding items. */
function OutstandingItemsTable({
  kind,
  onKindChange,
}: {
  kind: KindFilter;
  onKindChange: (kind: KindFilter) => void;
}) {
  const [failedOnly, setFailedOnly] = useState(false);
  const [search, setSearch] = useState("");
  const paging = usePaging();
  const query = useDataAccessPendingItems({
    kind: kind === "all" ? undefined : kind,
    status: failedOnly && kind !== "expired" ? "failed" : undefined,
    q: search.trim() || undefined,
    page: paging.page,
    pageSize: paging.pageSize,
  });
  const data = query.data;
  const items = data?.items ?? [];

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="pending-kind">
            Change
          </label>
          <select
            id="pending-kind"
            className={identityStyles.select}
            value={kind}
            onChange={(event) => {
              onKindChange(event.target.value as KindFilter);
              paging.setPageIndex(0);
            }}
          >
            <option value="all">All changes</option>
            {(Object.keys(GAP_KIND_LABELS) as Array<GapKind | "expired">).map((key) => (
              <option key={key} value={key}>
                {GAP_KIND_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
        <div className={identityStyles.fieldGrow}>
          <label className={identityStyles.label} htmlFor="pending-search">
            Search
          </label>
          <input
            id="pending-search"
            className={identityStyles.input}
            value={search}
            placeholder="Group, principal, table, or value"
            onChange={(event) => {
              setSearch(event.target.value);
              paging.setPageIndex(0);
            }}
          />
        </div>
        <label className={identityStyles.checkboxRow}>
          <input
            type="checkbox"
            checked={failedOnly}
            disabled={kind === "expired"}
            onChange={(event) => {
              setFailedOnly(event.target.checked);
              paging.setPageIndex(0);
            }}
          />
          Failed only
        </label>
      </div>

      {query.isError && <ErrorAlert error={query.error} title="Could not load outstanding items" />}
      {query.isPending && !data && <Spinner label="Loading outstanding items" />}
      {data && items.length === 0 && !query.isFetching && (
        <p className={identityStyles.muted}>Nothing outstanding matches these filters.</p>
      )}
      {items.length > 0 && (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="Outstanding items">
            <thead>
              <tr>
                <th>Kind</th>
                <th>Change</th>
                <th>Path</th>
                <th>Status</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.itemKey}>
                  <td>{GAP_KIND_LABELS[item.kind]}</td>
                  <td>
                    <code>{describeGapItem(item)}</code>
                  </td>
                  <td>{item.value ? <Badge tone="info">{item.value}</Badge> : "—"}</td>
                  <td>
                    <Badge tone={item.status === "failed" ? "danger" : "warning"}>{item.status}</Badge>
                  </td>
                  <td className={item.error ? styles.errorCell : undefined}>
                    {item.error ? `${item.error} · ${item.reason}` : item.reason}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.total > 0 && (
        <TablePager
          total={data.total}
          page={paging.pageIndex}
          pageSize={paging.pageSize}
          onPageChange={paging.setPageIndex}
          onPageSizeChange={(size) => {
            paging.setPageSize(size);
            paging.setPageIndex(0);
          }}
        />
      )}
    </div>
  );
}

/** Server-paged list of tables whose data-access tagging changed. */
function TagChangesTable() {
  const [change, setChange] = useState<TagChangeKind | "all">("all");
  const [scope, setScope] = useState<"latest" | "all">("latest");
  const paging = usePaging();
  const query = useDataAccessTagChanges({
    change: change === "all" ? undefined : change,
    scope,
    page: paging.page,
    pageSize: paging.pageSize,
  });
  const data = query.data;
  const items = data?.items ?? [];

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="tag-change-kind">
            Tag change
          </label>
          <select
            id="tag-change-kind"
            className={identityStyles.select}
            value={change}
            onChange={(event) => {
              setChange(event.target.value as TagChangeKind | "all");
              paging.setPageIndex(0);
            }}
          >
            <option value="all">All tag changes</option>
            {(Object.keys(TAG_CHANGE_LABELS) as TagChangeKind[]).map((key) => (
              <option key={key} value={key}>
                {TAG_CHANGE_LABELS[key]}
              </option>
            ))}
          </select>
        </div>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="tag-change-scope">
            Window
          </label>
          <select
            id="tag-change-scope"
            className={identityStyles.select}
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as "latest" | "all");
              paging.setPageIndex(0);
            }}
          >
            <option value="latest">Last sync</option>
            <option value="all">Last 30 days</option>
          </select>
        </div>
      </div>

      {query.isError && <ErrorAlert error={query.error} title="Could not load tag changes" />}
      {query.isPending && !data && <Spinner label="Loading tag changes" />}
      {data && items.length === 0 && !query.isFetching && (
        <p className={identityStyles.muted}>No tag changes match these filters.</p>
      )}
      {items.length > 0 && (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="Tag changes">
            <thead>
              <tr>
                <th>Table</th>
                <th>Change</th>
                <th>Before</th>
                <th>After</th>
                <th>Detected</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${item.runId}-${item.fullName}`}>
                  <td>
                    <code>{item.fullName}</code>
                  </td>
                  <td>{TAG_CHANGE_LABELS[item.change]}</td>
                  <td>{item.oldValue ?? "—"}</td>
                  <td>{item.newValue ?? "—"}</td>
                  <td>{when(item.detectedAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && data.total > 0 && (
        <TablePager
          total={data.total}
          page={paging.pageIndex}
          pageSize={paging.pageSize}
          onPageChange={paging.setPageIndex}
          onPageSizeChange={(size) => {
            paging.setPageSize(size);
            paging.setPageIndex(0);
          }}
        />
      )}
    </div>
  );
}
