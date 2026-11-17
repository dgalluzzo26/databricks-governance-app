/**
 * @file Access reports section for Data Access Analytics (Phase 3).
 *
 * Shows who holds data-access grants on governed tables, which holders used that access
 * in the window, holders who never read, and readers who hold no assigned grant.
 */
import { useMemo, useState } from "react";
import {
  useAccessNoAccess,
  useAccessTableDetail,
  useAccessTables,
  useAccessUnused,
} from "@/api/queries";
import type {
  AccessGrant,
  AccessHolder,
  AccessNoAccessItem,
  AccessTableSummary,
  AccessUnusedItem,
  UsageUserTableSummary,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./DataAccessAnalyticsPage.module.css";

const WINDOW_OPTIONS = [7, 14, 30, 90] as const;

function formatWhen(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function formatGrants(grants: AccessGrant[]): string {
  if (grants.length === 0) return "—";
  return grants
    .map((g) => `${g.viaType.toLowerCase()} ${g.viaPrincipal} → ${g.heldValue}`)
    .join("; ");
}

function Kpi({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string | number;
  hint?: string;
  tone?: "danger" | "warning";
}) {
  return (
    <div className={styles.kpi}>
      <span className={styles.kpiLabel}>{label}</span>
      <span
        className={`${styles.kpiValue}${
          tone === "danger" || tone === "warning" ? ` ${styles.kpiDanger}` : ""
        }`}
      >
        {value}
      </span>
      {hint ? <span className={styles.kpiHint}>{hint}</span> : null}
    </div>
  );
}

/**
 * Access vs usage reports: governed tables, holders, unused access, and reads without access.
 */
export function DataAccessAccessSection() {
  const [days, setDays] = useState<number>(30);
  const [search, setSearch] = useState("");
  const [selectedTable, setSelectedTable] = useState<string | null>(null);
  const debouncedSearch = useDebouncedValue(search, 250);

  const tablesQuery = useAccessTables(days, debouncedSearch || undefined, 200);
  const unusedQuery = useAccessUnused(days, 200);
  const noAccessQuery = useAccessNoAccess(days, 200);

  const tables = tablesQuery.data?.tables ?? [];
  const unusedTotal = unusedQuery.data?.total ?? 0;
  const noAccessTotal = noAccessQuery.data?.total ?? 0;
  const holders = tables.reduce((sum, t) => sum + t.holders, 0);
  const unusedHolders = tables.reduce((sum, t) => sum + t.unusedHolders, 0);
  const readersWithoutAccess = tables.reduce((sum, t) => sum + t.readersWithoutAccess, 0);

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="access-window">
            Window
          </label>
          <select
            id="access-window"
            className={identityStyles.select}
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            {WINDOW_OPTIONS.map((option) => (
              <option key={option} value={option}>
                Last {option} days
              </option>
            ))}
          </select>
        </div>

        <a
          href={`/api/usage-analytics/access/export?kind=tables&days=${days}`}
          className={identityStyles.button}
          download={`access_tables_${days}d.csv`}
        >
          Export tables CSV
        </a>
        <a
          href={`/api/usage-analytics/access/export?kind=holders&days=${days}`}
          className={identityStyles.button}
          download={`access_holders_${days}d.csv`}
        >
          Export holders CSV
        </a>
        <a
          href={`/api/usage-analytics/access/export?kind=unused&days=${days}`}
          className={identityStyles.button}
          download={`access_unused_${days}d.csv`}
        >
          Export unused CSV
        </a>
        <a
          href={`/api/usage-analytics/access/export?kind=no-access&days=${days}`}
          className={identityStyles.button}
          download={`access_no-access_${days}d.csv`}
        >
          Export no-access CSV
        </a>

        {tablesQuery.data?.asOf ? (
          <span className={styles.freshness}>Access snapshot as of {tablesQuery.data.asOf}</span>
        ) : null}
      </div>

      <Alert tone="info" title="How to read these reports">
        Holders come from active <code>data_access</code> assignments (direct or via group), matched
        to each table&apos;s <code>data_access_control</code> tag with the same hierarchical rule as
        grants. Unused access is a holder with zero reads in the window. Reads without assigned
        access are readers who hold no matching grant (often owners, admins, or grants made outside
        the app)—reported, not judged. Column-level sensitivity holders are not included here.
      </Alert>

      {(tablesQuery.isPending || unusedQuery.isPending || noAccessQuery.isPending) && (
        <Spinner label="Loading access reports" />
      )}
      {tablesQuery.isError && (
        <ErrorAlert error={tablesQuery.error} title="Could not load access tables" />
      )}

      {tablesQuery.data && (
        <div className={styles.kpiGrid}>
          <Kpi label="Governed tables" value={tablesQuery.data.total} hint="With a data_access_control tag" />
          <Kpi label="Holders (listed)" value={holders} hint="Across tables in this page" />
          <Kpi
            label="Unused holders"
            value={unusedHolders}
            tone={unusedHolders > 0 ? "warning" : undefined}
            hint={`${unusedTotal} pairs in unused report`}
          />
          <Kpi
            label="Readers without access"
            value={readersWithoutAccess}
            tone={readersWithoutAccess > 0 ? "warning" : undefined}
            hint={`${noAccessTotal} pairs in no-access report`}
          />
        </div>
      )}

      {tablesQuery.data && tablesQuery.data.total === 0 ? (
        <Alert tone="info" title="No access snapshot yet">
          Run the usage rollup (Refresh usage on the Data usage tab, or wait for the daily job) to
          build the governed-table and holder snapshots.
        </Alert>
      ) : null}

      <Card
        title="Governed tables"
        actions={
          <input
            className={identityStyles.input}
            placeholder="Search tables…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search governed tables"
          />
        }
      >
        {tablesQuery.data ? (
          <AccessTablesTable tables={tables} onSelect={setSelectedTable} selected={selectedTable} />
        ) : null}
      </Card>

      {selectedTable && (
        <AccessTableDetailCard
          tableFullName={selectedTable}
          days={days}
          onClose={() => setSelectedTable(null)}
        />
      )}

      <Card title="Unused access" actions={<Badge>{unusedTotal}</Badge>}>
        {unusedQuery.isError && (
          <ErrorAlert error={unusedQuery.error} title="Could not load unused access" />
        )}
        {unusedQuery.data ? <UnusedAccessTable items={unusedQuery.data.items} /> : null}
      </Card>

      <Card title="Reads without assigned access" actions={<Badge>{noAccessTotal}</Badge>}>
        {noAccessQuery.isError && (
          <ErrorAlert error={noAccessQuery.error} title="Could not load reads without access" />
        )}
        {noAccessQuery.data ? <NoAccessTable items={noAccessQuery.data.items} /> : null}
      </Card>
    </div>
  );
}

function AccessTablesTable({
  tables,
  onSelect,
  selected,
}: {
  tables: AccessTableSummary[];
  onSelect: (table: string) => void;
  selected: string | null;
}) {
  const columns: TableColumn<AccessTableSummary>[] = useMemo(
    () => [
      { key: "table", label: "Table", value: (t) => t.tableFullName },
      { key: "tag", label: "Access tag", value: (t) => t.tagValue },
      { key: "holders", label: "Holders", value: (t) => t.holders },
      { key: "used", label: "Used", value: (t) => t.holdersWithReads },
      { key: "unused", label: "Unused", value: (t) => t.unusedHolders },
      { key: "gaps", label: "No access", value: (t) => t.readersWithoutAccess },
      { key: "reads", label: "Reads", value: (t) => t.reads },
      {
        key: "last",
        label: "Last read",
        value: (t) => t.lastReadAt,
        filterText: (t) => formatWhen(t.lastReadAt),
      },
    ],
    [],
  );
  const pager = useTableView(tables, columns, { defaultPageSize: 10 });

  if (tables.length === 0) {
    return <p className={identityStyles.muted}>No governed tables match this search.</p>;
  }

  return (
    <>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Governed tables">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((t) => (
              <tr
                key={t.tableFullName}
                style={
                  selected === t.tableFullName
                    ? { background: "var(--color-surface-hover)" }
                    : undefined
                }
              >
                <td>
                  <button
                    type="button"
                    className={identityStyles.button}
                    style={{
                      padding: "2px 6px",
                      fontSize: "var(--font-size-xs)",
                      fontFamily: "monospace",
                    }}
                    onClick={() => onSelect(t.tableFullName)}
                  >
                    {t.tableFullName}
                  </button>
                </td>
                <td>
                  <code>{t.tagValue}</code>
                </td>
                <td>{t.holders}</td>
                <td>{t.holdersWithReads}</td>
                <td>{t.unusedHolders}</td>
                <td>{t.readersWithoutAccess}</td>
                <td>{t.reads}</td>
                <td className={identityStyles.muted}>{formatWhen(t.lastReadAt)}</td>
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

function AccessTableDetailCard({
  tableFullName,
  days,
  onClose,
}: {
  tableFullName: string;
  days: number;
  onClose: () => void;
}) {
  const detail = useAccessTableDetail(tableFullName, days);

  return (
    <Card
      title={`Access detail: ${tableFullName}`}
      actions={
        <button type="button" className={identityStyles.button} onClick={onClose} aria-label="Close access detail">
          Close
        </button>
      }
    >
      {detail.isPending && <Spinner label="Loading holders" />}
      {detail.isError && <ErrorAlert error={detail.error} title="Could not load table access detail" />}
      {detail.data && (
        <div className={styles.tableStack}>
          <p className={identityStyles.muted}>
            Tag value <code>{detail.data.tagValue || "—"}</code>
            {detail.data.asOf ? ` · snapshot ${detail.data.asOf}` : ""}
          </p>
          <h3 className={identityStyles.label}>
            Holders <Badge>{detail.data.holders.length}</Badge>
          </h3>
          <HoldersTable holders={detail.data.holders} />
          <h3 className={identityStyles.label}>
            Readers without assigned access <Badge>{detail.data.readersWithoutAccess.length}</Badge>
          </h3>
          <ReadersTable readers={detail.data.readersWithoutAccess} />
        </div>
      )}
    </Card>
  );
}

function HoldersTable({ holders }: { holders: AccessHolder[] }) {
  const columns: TableColumn<AccessHolder>[] = useMemo(
    () => [
      { key: "user", label: "User", value: (h) => h.userName },
      { key: "grants", label: "Grants", value: (h) => formatGrants(h.grants) },
      { key: "reads", label: "Reads", value: (h) => h.reads },
      {
        key: "status",
        label: "Status",
        value: (h) => (h.used ? "used" : "unused"),
      },
      {
        key: "last",
        label: "Last read",
        value: (h) => h.lastReadAt,
        filterText: (h) => formatWhen(h.lastReadAt),
      },
    ],
    [],
  );
  const pager = useTableView(holders, columns, { defaultPageSize: 10 });

  if (holders.length === 0) {
    return <p className={identityStyles.muted}>No holders for this table.</p>;
  }

  return (
    <>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Table holders">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((h) => (
              <tr key={h.userName}>
                <td>
                  <code>{h.userName}</code>
                </td>
                <td className={identityStyles.muted}>{formatGrants(h.grants)}</td>
                <td>{h.reads}</td>
                <td>
                  <Badge tone={h.used ? "info" : "warning"}>{h.used ? "used" : "unused"}</Badge>
                </td>
                <td className={identityStyles.muted}>{formatWhen(h.lastReadAt)}</td>
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

function ReadersTable({ readers }: { readers: UsageUserTableSummary[] }) {
  const columns: TableColumn<UsageUserTableSummary>[] = useMemo(
    () => [
      { key: "user", label: "User", value: (r) => r.userName },
      { key: "reads", label: "Reads", value: (r) => r.reads },
      {
        key: "last",
        label: "Last read",
        value: (r) => r.lastReadAt,
        filterText: (r) => formatWhen(r.lastReadAt),
      },
    ],
    [],
  );
  const pager = useTableView(readers, columns, { defaultPageSize: 10 });

  if (readers.length === 0) {
    return <p className={identityStyles.muted}>Every reader in the window holds a matching grant.</p>;
  }

  return (
    <>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Readers without access">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((r) => (
              <tr key={r.userName}>
                <td>
                  <code>{r.userName}</code>
                </td>
                <td>{r.reads}</td>
                <td className={identityStyles.muted}>{formatWhen(r.lastReadAt)}</td>
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

function UnusedAccessTable({ items }: { items: AccessUnusedItem[] }) {
  const columns: TableColumn<AccessUnusedItem>[] = useMemo(
    () => [
      { key: "table", label: "Table", value: (i) => i.tableFullName },
      { key: "tag", label: "Access tag", value: (i) => i.tagValue },
      { key: "user", label: "User", value: (i) => i.userName },
      { key: "grants", label: "Grants", value: (i) => formatGrants(i.grants) },
    ],
    [],
  );
  const pager = useTableView(items, columns, { defaultPageSize: 10 });

  if (items.length === 0) {
    return <p className={identityStyles.muted}>Every holder read their table in this window.</p>;
  }

  return (
    <>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Unused access">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((i) => (
              <tr key={`${i.tableFullName}:${i.userName}`}>
                <td>
                  <code>{i.tableFullName}</code>
                </td>
                <td>
                  <code>{i.tagValue}</code>
                </td>
                <td>
                  <code>{i.userName}</code>
                </td>
                <td className={identityStyles.muted}>{formatGrants(i.grants)}</td>
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

function NoAccessTable({ items }: { items: AccessNoAccessItem[] }) {
  const columns: TableColumn<AccessNoAccessItem>[] = useMemo(
    () => [
      { key: "table", label: "Table", value: (i) => i.tableFullName },
      { key: "user", label: "User", value: (i) => i.userName },
      { key: "reads", label: "Reads", value: (i) => i.reads },
      {
        key: "last",
        label: "Last read",
        value: (i) => i.lastReadAt,
        filterText: (i) => formatWhen(i.lastReadAt),
      },
    ],
    [],
  );
  const pager = useTableView(items, columns, { defaultPageSize: 10 });

  if (items.length === 0) {
    return <p className={identityStyles.muted}>No readers without an assigned grant in this window.</p>;
  }

  return (
    <>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Reads without assigned access">
          <DataTableHead view={pager} />
          <tbody>
            {pager.rows.map((i) => (
              <tr key={`${i.tableFullName}:${i.userName}`}>
                <td>
                  <code>{i.tableFullName}</code>
                </td>
                <td>
                  <code>{i.userName}</code>
                </td>
                <td>{i.reads}</td>
                <td className={identityStyles.muted}>{formatWhen(i.lastReadAt)}</td>
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
