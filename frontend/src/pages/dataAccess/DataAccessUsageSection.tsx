/**
 * @file Usage analytics section for Data Access Analytics.
 *
 * Shows table and user reads, sensitive column access (clear vs masked),
 * daily trend charts, table drilldown, and an on-demand refresh trigger.
 */
import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  useRefreshUsageRollup,
  useUsageOverview,
  useUsageTableDetail,
  useUsageTables,
  useUsageUsers,
} from "@/api/queries";
import type {
  UsageColumnSummary,
  UsageDailyPoint,
  UsageRecentReadEvent,
  UsageSensitiveSummary,
  UsageTableSummary,
  UsageUserSummary,
  UsageUserTableSummary,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./DataAccessAnalyticsPage.module.css";

const WINDOW_OPTIONS = [7, 14, 30, 90] as const;

const CHART_COLORS = {
  reads: "#2272b4",
  users: "#2f9e6b",
  clear: "#dc2626",
  masked: "#0f766e",
};

function formatWhen(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
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
          tone === "danger"
            ? ` ${styles.kpiDanger}`
            : tone === "warning"
              ? ` ${styles.kpiDanger}`
              : ""
        }`}
      >
        {value}
      </span>
      {hint ? <span className={styles.kpiHint}>{hint}</span> : null}
    </div>
  );
}

function DailyActivityChart({ rows }: { rows: UsageDailyPoint[] }) {
  const chartData = useMemo(
    () =>
      rows.map((r) => ({
        ...r,
        label: r.date.slice(5),
      })),
    [rows],
  );

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis stroke="var(--color-text-muted)" />
        <Tooltip />
        <Legend />
        <Line
          type="monotone"
          dataKey="reads"
          name="Table reads"
          stroke={CHART_COLORS.reads}
          strokeWidth={2}
          dot={false}
        />
        <Line
          type="monotone"
          dataKey="distinctUsers"
          name="Active users"
          stroke={CHART_COLORS.users}
          strokeWidth={2}
          dot={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

function SensitiveModeChart({ rows }: { rows: UsageDailyPoint[] }) {
  const chartData = useMemo(
    () =>
      rows.map((r) => ({
        ...r,
        label: r.date.slice(5),
      })),
    [rows],
  );

  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis allowDecimals={false} stroke="var(--color-text-muted)" />
        <Tooltip />
        <Legend />
        <Bar dataKey="clearReads" name="Clear reads" stackId="mode" fill={CHART_COLORS.clear} />
        <Bar dataKey="maskedReads" name="Masked reads" stackId="mode" fill={CHART_COLORS.masked} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function DataAccessUsageSection() {
  const [days, setDays] = useState<number>(30);
  const [selectedTable, setSelectedTable] = useState<string | null>(null);

  const overviewQuery = useUsageOverview(days);
  const refreshMutation = useRefreshUsageRollup();
  const data = overviewQuery.data;

  const handleRefresh = () => {
    refreshMutation.mutate();
  };

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="usage-window">
            Window
          </label>
          <select
            id="usage-window"
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

        <button
          type="button"
          className={`${identityStyles.button} ${identityStyles.buttonPrimary}`}
          onClick={handleRefresh}
          disabled={refreshMutation.isPending}
          aria-label="Refresh usage"
        >
          {refreshMutation.isPending ? "Starting rollup…" : "Refresh usage"}
        </button>

        <a
          href={`/api/usage-analytics/export?kind=tables&days=${days}`}
          className={identityStyles.button}
          download={`usage_tables_${days}d.csv`}
        >
          Export tables CSV
        </a>
        <a
          href={`/api/usage-analytics/export?kind=sensitive&days=${days}`}
          className={identityStyles.button}
          download={`usage_sensitive_${days}d.csv`}
        >
          Export sensitive CSV
        </a>

        {data?.lastRun ? (
          <span className={styles.freshness}>
            Rollup: {data.lastRun.status} · Data as of{" "}
            {data.lastRun.dataAsOf ? formatWhen(data.lastRun.dataAsOf) : "N/A"}
          </span>
        ) : null}
      </div>

      {refreshMutation.isSuccess && (
        <Alert tone="success" title="Rollup started">
          {refreshMutation.data?.message}
        </Alert>
      )}
      {refreshMutation.isError && (
        <ErrorAlert error={refreshMutation.error} title="Could not trigger rollup" />
      )}

      {overviewQuery.isPending && <Spinner label="Loading usage analytics…" />}
      {overviewQuery.isError && (
        <ErrorAlert error={overviewQuery.error} title="Could not load usage analytics" />
      )}

      {data && !data.available && (
        <Alert
          tone="warning"
          title={data.errorCode === "warehouse_unavailable" ? "SQL warehouse is not ready" : "Usage analytics unavailable"}
        >
          {data.detail ?? "Could not query usage summary tables."}
          {(data.errorCode === "warehouse_unavailable" || data.errorCode === "timeout") && (
            <> This page retries on the next load; it is not cached while the warehouse is down.</>
          )}
        </Alert>
      )}

      {data && data.available && (
        <>
          <div className={styles.kpiGrid}>
            <Kpi
              label="Table reads"
              value={data.kpis.totalTableReads.toLocaleString()}
              hint="In enrolled catalogs"
            />
            <Kpi
              label="Active users"
              value={data.kpis.activeUsers.toLocaleString()}
              hint="Unique readers"
            />
            <Kpi
              label="Tables accessed"
              value={data.kpis.tablesAccessed.toLocaleString()}
              hint="Unique tables read"
            />
            <Kpi
              label="Sensitive column reads"
              value={data.kpis.sensitiveColumnReads.toLocaleString()}
              hint={`${data.kpis.clearReads} clear · ${data.kpis.maskedReads} masked`}
            />
            <Kpi
              label="Clear reads"
              value={data.kpis.clearReads.toLocaleString()}
              tone={data.kpis.clearReads > 0 ? "warning" : undefined}
              hint="Allowed by sensitivity attributes"
            />
            <Kpi
              label="Masked reads"
              value={data.kpis.maskedReads.toLocaleString()}
              hint="Mask UDFs applied"
            />
          </div>

          <div className={styles.chartGrid}>
            <Card title="Daily table reads and users">
              {data.dailyTrends.length > 0 ? (
                <DailyActivityChart rows={data.dailyTrends} />
              ) : (
                <p className={identityStyles.muted}>No activity recorded in this window.</p>
              )}
            </Card>
            <Card title="Sensitive column reads: clear vs masked">
              {data.dailyTrends.length > 0 ? (
                <SensitiveModeChart rows={data.dailyTrends} />
              ) : (
                <p className={identityStyles.muted}>No sensitive column reads in this window.</p>
              )}
            </Card>
          </div>

          {selectedTable ? (
            <TableDetailCard
              tableFullName={selectedTable}
              days={days}
              onClose={() => setSelectedTable(null)}
            />
          ) : null}

          <div className={styles.tableStack}>
            <Card
              title="Sensitive column usage"
              actions={<Badge>{data.sensitiveBreakdown.length}</Badge>}
            >
              <SensitiveBreakdownTable items={data.sensitiveBreakdown} />
            </Card>

            <Card title="Top tables accessed">
              <p className={styles.caption}>
                Click any table name to inspect who read it and which columns were accessed.
              </p>
              <TopTablesTable
                days={days}
                selectedTable={selectedTable}
                onSelectTable={setSelectedTable}
              />
            </Card>

            <Card title="Top users">
              <TopUsersTable days={days} />
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

function SensitiveBreakdownTable({ items }: { items: UsageSensitiveSummary[] }) {
  const columns: TableColumn<UsageSensitiveSummary>[] = [
    {
      key: "sensitivityPath",
      label: "Sensitivity path",
      value: (s) => s.sensitivityPath,
    },
    { key: "reads", label: "Total reads", value: (s) => s.reads },
    { key: "clearReads", label: "Clear reads", value: (s) => s.clearReads },
    { key: "maskedReads", label: "Masked reads", value: (s) => s.maskedReads },
    { key: "exemptReads", label: "Exempt reads", value: (s) => s.exemptReads },
    { key: "distinctUsers", label: "Users", value: (s) => s.distinctUsers },
    { key: "distinctTables", label: "Tables", value: (s) => s.distinctTables },
  ];

  const view = useTableView(items, columns, { defaultPageSize: 10 });

  if (items.length === 0) {
    return <p className={identityStyles.muted}>No sensitive column reads in this window.</p>;
  }

  return (
    <div className={identityStyles.tableWrap}>
      <table className={identityStyles.table} aria-label="Sensitive column usage">
        <DataTableHead view={view} />
        <tbody>
          {view.rows.map((row) => (
            <tr key={row.sensitivityPath}>
              <td>
                <code>{row.sensitivityPath}</code>
              </td>
              <td>{row.reads.toLocaleString()}</td>
              <td>
                {row.clearReads > 0 ? (
                  <Badge tone="warning">{row.clearReads.toLocaleString()}</Badge>
                ) : (
                  "0"
                )}
              </td>
              <td>{row.maskedReads.toLocaleString()}</td>
              <td>{row.exemptReads.toLocaleString()}</td>
              <td>{row.distinctUsers}</td>
              <td>{row.distinctTables}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager
        total={view.total}
        page={view.page}
        pageSize={view.pageSize}
        onPageChange={view.setPage}
        onPageSizeChange={view.setPageSize}
      />
    </div>
  );
}

function TopTablesTable({
  days,
  selectedTable,
  onSelectTable,
}: {
  days: number;
  selectedTable: string | null;
  onSelectTable: (name: string) => void;
}) {
  const [search, setSearch] = useState("");
  const query = useUsageTables(days, search.trim() || undefined, 100);
  const items = query.data?.tables ?? [];

  const columns: TableColumn<UsageTableSummary>[] = [
    { key: "tableFullName", label: "Table", value: (t) => t.tableFullName },
    { key: "catalogName", label: "Catalog", value: (t) => t.catalogName },
    { key: "reads", label: "Reads", value: (t) => t.reads },
    { key: "distinctUsers", label: "Users", value: (t) => t.distinctUsers },
    {
      key: "lastReadAt",
      label: "Last read",
      value: (t) => t.lastReadAt,
      filterText: (t) => formatWhen(t.lastReadAt),
    },
  ];

  const view = useTableView(items, columns, { defaultPageSize: 10 });

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.field} style={{ maxWidth: 300 }}>
        <input
          type="search"
          className={identityStyles.input}
          placeholder="Filter tables…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter tables"
        />
      </div>

      {query.isPending && <Spinner label="Loading tables…" />}
      {query.isError && <ErrorAlert error={query.error} title="Could not load tables" />}

      {items.length === 0 && !query.isPending ? (
        <p className={identityStyles.muted}>No tables found matching criteria.</p>
      ) : null}

      {items.length > 0 ? (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="Enrolled table usage">
            <DataTableHead view={view} />
            <tbody>
              {view.rows.map((row) => (
                <tr
                  key={row.tableFullName}
                  style={
                    selectedTable === row.tableFullName
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
                      onClick={() => onSelectTable(row.tableFullName)}
                    >
                      {row.tableFullName}
                    </button>
                  </td>
                  <td>{row.catalogName}</td>
                  <td>{row.reads.toLocaleString()}</td>
                  <td>{row.distinctUsers}</td>
                  <td>{formatWhen(row.lastReadAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <TablePager
            total={view.total}
            page={view.page}
            pageSize={view.pageSize}
            onPageChange={view.setPage}
            onPageSizeChange={view.setPageSize}
          />
        </div>
      ) : null}
    </div>
  );
}

function TopUsersTable({ days }: { days: number }) {
  const [search, setSearch] = useState("");
  const query = useUsageUsers(days, search.trim() || undefined, 100);
  const items = query.data?.users ?? [];

  const columns: TableColumn<UsageUserSummary>[] = [
    { key: "userName", label: "User", value: (u) => u.userName },
    { key: "reads", label: "Reads", value: (u) => u.reads },
    { key: "distinctTables", label: "Tables accessed", value: (u) => u.distinctTables },
    {
      key: "lastReadAt",
      label: "Last read",
      value: (u) => u.lastReadAt,
      filterText: (u) => formatWhen(u.lastReadAt),
    },
  ];

  const view = useTableView(items, columns, { defaultPageSize: 10 });

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.field} style={{ maxWidth: 300 }}>
        <input
          type="search"
          className={identityStyles.input}
          placeholder="Filter users…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Filter users"
        />
      </div>

      {query.isPending && <Spinner label="Loading users…" />}
      {query.isError && <ErrorAlert error={query.error} title="Could not load users" />}

      {items.length === 0 && !query.isPending ? (
        <p className={identityStyles.muted}>No users found matching criteria.</p>
      ) : null}

      {items.length > 0 ? (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="User usage ranking">
            <DataTableHead view={view} />
            <tbody>
              {view.rows.map((row) => (
                <tr key={row.userName}>
                  <td>
                    <code>{row.userName}</code>
                  </td>
                  <td>{row.reads.toLocaleString()}</td>
                  <td>{row.distinctTables}</td>
                  <td>{formatWhen(row.lastReadAt)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <TablePager
            total={view.total}
            page={view.page}
            pageSize={view.pageSize}
            onPageChange={view.setPage}
            onPageSizeChange={view.setPageSize}
          />
        </div>
      ) : null}
    </div>
  );
}

function TableDetailCard({
  tableFullName,
  days,
  onClose,
}: {
  tableFullName: string;
  days: number;
  onClose: () => void;
}) {
  const query = useUsageTableDetail(tableFullName, days);
  const data = query.data;

  return (
    <Card
      title={`Table detail: ${tableFullName}`}
      actions={
        <button
          type="button"
          className={identityStyles.button}
          onClick={onClose}
          aria-label="Close table detail"
        >
          Close
        </button>
      }
    >
      {query.isPending && <Spinner label="Loading table detail…" />}
      {query.isError && (
        <ErrorAlert error={query.error} title="Could not load table detail" />
      )}

      {data ? (
        <div className={styles.tableStack}>
          <div className={styles.kpiGrid}>
            <Kpi label="Total reads" value={data.reads.toLocaleString()} />
            <Kpi label="Distinct users" value={data.distinctUsers.toLocaleString()} />
            <Kpi label="Last read" value={formatWhen(data.lastReadAt)} />
          </div>

          <div>
            <h4 style={{ margin: "var(--space-3) 0 var(--space-2)" }}>Columns accessed</h4>
            {data.columns.length === 0 ? (
              <p className={identityStyles.muted}>No column reads recorded.</p>
            ) : (
              <div className={identityStyles.tableWrap}>
                <table className={identityStyles.table} aria-label="Columns accessed">
                  <thead>
                    <tr>
                      <th>Column</th>
                      <th>Sensitivity</th>
                      <th>Access mode</th>
                      <th>Reads</th>
                      <th>Users</th>
                      <th>Last read</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.columns.map((c: UsageColumnSummary) => (
                      <tr key={c.columnName}>
                        <td>
                          <code>{c.columnName}</code>
                        </td>
                        <td>{c.sensitivityPath ? <code>{c.sensitivityPath}</code> : "—"}</td>
                        <td>
                          <Badge
                            tone={
                              c.accessMode === "clear"
                                ? "warning"
                                : c.accessMode === "masked"
                                  ? "info"
                                  : undefined
                            }
                          >
                            {c.accessMode}
                          </Badge>
                        </td>
                        <td>{c.reads.toLocaleString()}</td>
                        <td>{c.distinctUsers}</td>
                        <td>{formatWhen(c.lastReadAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <h4 style={{ margin: "var(--space-3) 0 var(--space-2)" }}>Users who accessed this table</h4>
            {data.users.length === 0 ? (
              <p className={identityStyles.muted}>No user activity recorded.</p>
            ) : (
              <div className={identityStyles.tableWrap}>
                <table className={identityStyles.table} aria-label="Table readers">
                  <thead>
                    <tr>
                      <th>User</th>
                      <th>Reads</th>
                      <th>Last read</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.users.map((u: UsageUserTableSummary) => (
                      <tr key={u.userName}>
                        <td>
                          <code>{u.userName}</code>
                        </td>
                        <td>{u.reads.toLocaleString()}</td>
                        <td>{formatWhen(u.lastReadAt)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div>
            <h4 style={{ margin: "var(--space-3) 0 var(--space-2)" }}>Recent read statements</h4>
            {data.recentReads.length === 0 ? (
              <p className={identityStyles.muted}>No recent read events.</p>
            ) : (
              <div className={identityStyles.tableWrap}>
                <table className={identityStyles.table} aria-label="Recent statements">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>User</th>
                      <th>Entity type</th>
                      <th>Direct</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.recentReads.map((e: UsageRecentReadEvent, i: number) => (
                      <tr key={`${e.eventTime}-${i}`}>
                        <td>{formatWhen(e.eventTime)}</td>
                        <td>
                          <code>{e.userName}</code>
                        </td>
                        <td>{e.entityType ?? "—"}</td>
                        <td>{e.directAccess ? "Yes" : "Indirect"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      ) : null}
    </Card>
  );
}
