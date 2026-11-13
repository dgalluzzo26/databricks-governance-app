/**
 * @file Admin health monitor: request latency, slow-request attribution, scaling recommendations,
 * and current app, Lakebase, and SQL warehouse sizing.
 *
 * Data comes from `/api/performance/*`. Every API request is timed by the backend and stored in
 * Lakebase, so the page reflects real traffic in the chosen window.
 */
import { useMemo, useState } from "react";
import { useServerTableView } from "@/hooks/useServerTableView";
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
  usePerformanceAppCost,
  usePerformanceCapacity,
  usePerformanceSlowRequests,
  usePerformanceSummary,
} from "@/api/queries";
import type {
  PerformanceActivity,
  PerformanceCapacitySetting,
  PerformanceHour,
  PerformanceKpis,
  PerformanceRecommendation,
  PerformanceSeverity,
  PerformanceSummary,
} from "@/api/types";
import { Alert, Badge, Card, ErrorAlert, PageHeader, Spinner, TablePager } from "@/components/ui";
import type { TablePageSize } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./PerformancePage.module.css";
import { useTableView, type TableColumn } from "@/hooks/useTableView";
import { DataTableHead } from "@/components/ui/DataTableHead";

const WINDOW_OPTIONS = [
  { hours: 1, label: "Last hour" },
  { hours: 6, label: "Last 6 hours" },
  { hours: 24, label: "Last 24 hours" },
  { hours: 168, label: "Last 7 days" },
  { hours: 336, label: "Last 14 days" },
] as const;

const COLORS = {
  warehouse: "#2272b4",
  lakebase: "#0f766e",
  app: "#9a3412",
  p95: "#2272b4",
  concurrency: "#b45309",
};

const SEVERITY_TONE: Record<PerformanceSeverity, BadgeTone> = {
  critical: "danger",
  warning: "warning",
  info: "info",
};

const TARGET_LABEL: Record<string, string> = {
  app: "App compute",
  lakebase: "Lakebase",
  warehouse: "SQL warehouse",
  none: "General",
};

/** Formats milliseconds as `850 ms` or `12.4 s`, or an em dash when unknown. */
export function formatMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;
}

/** Formats a 0..1 share or rate as a whole percentage, or an em dash when unknown. */
export function formatPercent(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)}%`;
}

/** One hourly bucket ready for the charts, with a short time label. */
export interface HourRow {
  label: string;
  hour: string;
  avgWarehouseMs: number;
  avgLakebaseMs: number;
  avgAppMs: number;
  p95Ms: number;
  peakConcurrency: number;
  slowCount: number;
}

/**
 * Converts the hourly roll-up into chart rows, oldest first.
 *
 * @param hourly - Buckets from the summary payload.
 * @returns One row per hour with a local `HH:mm` label.
 */
export function buildHourRows(hourly: PerformanceHour[]): HourRow[] {
  return hourly.map((bucket) => ({
    label: new Date(bucket.hour).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
    hour: bucket.hour,
    avgWarehouseMs: bucket.avgWarehouseMs,
    avgLakebaseMs: bucket.avgLakebaseMs,
    avgAppMs: bucket.avgAppMs,
    p95Ms: bucket.p95Ms,
    peakConcurrency: bucket.peakConcurrency,
    slowCount: bucket.slowCount,
  }));
}

/** Headline tile. */
function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "danger" }) {
  return (
    <div className={styles.kpi}>
      <span className={styles.kpiLabel}>{label}</span>
      <span className={`${styles.kpiValue}${tone === "danger" ? ` ${styles.kpiDanger}` : ""}`}>{value}</span>
      {hint ? <span className={styles.kpiHint}>{hint}</span> : null}
    </div>
  );
}

/** KPI tiles for the window. */
function KpiGrid({ kpis }: { kpis: PerformanceKpis }) {
  return (
    <div className={styles.kpiGrid}>
      <Kpi label="Requests" value={String(kpis.totalRequests)} hint={`p95 ${formatMs(kpis.p95Ms)}`} />
      <Kpi
        label="Slower than expected"
        value={String(kpis.slowRequests)}
        tone={kpis.slowRequests > 0 ? "danger" : undefined}
        hint={`${formatPercent(kpis.slowRate)} of requests`}
      />
      <Kpi
        label="Server errors"
        value={String(kpis.errorRequests)}
        tone={kpis.errorRequests > 0 ? "danger" : undefined}
        hint="HTTP 5xx"
      />
      <Kpi label="Peak concurrency" value={String(kpis.peakConcurrency)} hint="Requests in flight at once" />
      <Kpi label="Slow time on warehouse" value={formatPercent(kpis.warehouseShare)} hint="Share of slow-request time" />
      <Kpi label="Slow time on Lakebase" value={formatPercent(kpis.lakebaseShare)} hint="Share of slow-request time" />
      <Kpi label="Slow time in app" value={formatPercent(kpis.appShare)} hint="Python and REST API calls" />
    </div>
  );
}

/** Ranked recommendations with severity and the resource they point at. */
function Recommendations({ items }: { items: PerformanceRecommendation[] }) {
  if (items.length === 0) return <p className={identityStyles.muted}>No recommendations.</p>;
  return (
    <ul className={styles.recList} aria-label="Scaling recommendations">
      {items.map((rec, index) => (
        <li key={`${rec.title}-${index}`} className={styles.recItem}>
          <div className={styles.recHead}>
            <Badge tone={SEVERITY_TONE[rec.severity]}>{rec.severity}</Badge>
            <span className={styles.recTarget}>{TARGET_LABEL[rec.target] ?? rec.target}</span>
            <strong>{rec.title}</strong>
          </div>
          <p className={styles.recDetail}>{rec.detail}</p>
        </li>
      ))}
    </ul>
  );
}

/** Stacked bars: average warehouse, Lakebase, and app time per hour. */
function TimeSplitChart({ rows }: { rows: HourRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis stroke="var(--color-text-muted)" unit=" ms" />
        <Tooltip />
        <Legend />
        <Bar dataKey="avgWarehouseMs" name="Warehouse" stackId="time" fill={COLORS.warehouse} />
        <Bar dataKey="avgLakebaseMs" name="Lakebase" stackId="time" fill={COLORS.lakebase} />
        <Bar dataKey="avgAppMs" name="App" stackId="time" fill={COLORS.app} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Lines: p95 total latency and peak concurrency per hour. */
function LatencyChart({ rows }: { rows: HourRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis yAxisId="ms" stroke="var(--color-text-muted)" unit=" ms" />
        <YAxis yAxisId="count" orientation="right" stroke="var(--color-text-muted)" allowDecimals={false} />
        <Tooltip />
        <Legend />
        <Line
          yAxisId="ms"
          type="monotone"
          dataKey="p95Ms"
          name="p95 latency (ms)"
          stroke={COLORS.p95}
          strokeWidth={2}
          connectNulls
        />
        <Line
          yAxisId="count"
          type="monotone"
          dataKey="peakConcurrency"
          name="Peak concurrency"
          stroke={COLORS.concurrency}
          strokeWidth={2}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Per-activity latency table, slowest p95 first. */
function ActivityTable({ rows }: { rows: PerformanceActivity[] }) {
  const columns: TableColumn<PerformanceActivity>[] = [
    {
      key: "activity",
      label: "Activity",
      value: (row) => row.activity,
      filterText: (row) => `${row.activity} ${row.method} ${row.route}`,
    },
    { key: "expected", label: "Expected", value: (row) => row.expectedMs },
    { key: "requests", label: "Requests", value: (row) => row.count },
    { key: "slow", label: "Slow", value: (row) => row.slowCount },
    { key: "p50", label: "p50", value: (row) => row.p50Ms },
    { key: "p95", label: "p95", value: (row) => row.p95Ms },
    { key: "max", label: "Max", value: (row) => row.maxMs },
    { key: "avgWarehouse", label: "Avg warehouse", value: (row) => row.avgWarehouseMs },
    { key: "avgLakebase", label: "Avg Lakebase", value: (row) => row.avgLakebaseMs },
    { key: "avgApp", label: "Avg app", value: (row) => row.avgAppMs },
  ];
  const view = useTableView(rows, columns, { paginate: false });
  if (rows.length === 0) return <p className={identityStyles.muted}>No requests in this window.</p>;
  return (
    <div className={identityStyles.tableWrap}>
      <table className={identityStyles.table} aria-label="Latency by activity">
        <DataTableHead view={view} />
        <tbody>
          {view.rows.map((row) => (
            <tr key={`${row.method} ${row.route}`}>
              <td>
                {row.activity}
                <div className={styles.route}>
                  {row.method} {row.route}
                </div>
              </td>
              <td>{formatMs(row.expectedMs)}</td>
              <td>{row.count}</td>
              <td className={row.slowCount > 0 ? styles.slowCell : undefined}>{row.slowCount}</td>
              <td>{formatMs(row.p50Ms)}</td>
              <td className={row.p95Ms > row.expectedMs ? styles.slowCell : undefined}>{formatMs(row.p95Ms)}</td>
              <td>{formatMs(row.maxMs)}</td>
              <td>{formatMs(row.avgWarehouseMs)}</td>
              <td>{formatMs(row.avgLakebaseMs)}</td>
              <td>{formatMs(row.avgAppMs)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Current sizing of the app, Lakebase endpoint, and SQL warehouse. */
function CapacityList({ settings }: { settings: PerformanceCapacitySetting[] }) {
  return (
    <dl className={styles.capacity} aria-label="Current sizing">
      {settings.map((setting) => (
        <div key={setting.target} className={styles.capacityRow}>
          <dt>
            {TARGET_LABEL[setting.target] ?? setting.target}
            <div className={styles.route}>{setting.name}</div>
          </dt>
          <dd>
            {setting.available ? (
              <>
                <span>{setting.size ?? "Size not reported"}</span>
                {setting.autoscaling ? <span className={styles.muted}> · {setting.autoscaling}</span> : null}
                {setting.detail ? <div className={styles.muted}>{setting.detail}</div> : null}
              </>
            ) : (
              <span className={styles.unavailable}>Unavailable: {setting.detail ?? "unknown error"}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** Slow-request columns. Keys match the backend `SLOW_REQUEST_READERS` whitelist. */
const SLOW_REQUEST_COLUMNS: TableColumn<unknown>[] = [
  { key: "when", label: "When", sortable: true, filterable: true },
  { key: "activity", label: "Activity", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "total", label: "Total", sortable: true, filterable: true },
  { key: "expected", label: "Expected", sortable: true, filterable: true },
  { key: "warehouse", label: "Warehouse", sortable: true, filterable: true },
  { key: "lakebase", label: "Lakebase", sortable: true, filterable: true },
  { key: "app", label: "App", sortable: true, filterable: true },
  { key: "user", label: "User", sortable: true, filterable: true },
];

/** Paginated slow requests, newest first. */
function SlowRequestsTable({ hours }: { hours: number }) {
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(25);
  const view = useServerTableView(SLOW_REQUEST_COLUMNS, { onChange: () => setPageIndex(0) });
  const query = usePerformanceSlowRequests({ hours, page: pageIndex + 1, pageSize, ...view.query });
  const data = query.data;

  if (query.isPending && !data) return <Spinner label="Loading slow requests" />;
  if (query.isError) return <ErrorAlert error={query.error} title="Could not load slow requests" />;
  if (!data || data.total === 0) return <p className={identityStyles.muted}>Nothing slower than expected in this window.</p>;

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.tableWrap}>
        <table className={identityStyles.table} aria-label="Slow requests">
          <DataTableHead view={view} />
          <tbody>
            {data.items.map((item, index) => (
              <tr key={`${item.recordedAt}-${index}`}>
                <td>{new Date(item.recordedAt).toLocaleString()}</td>
                <td>
                  {item.activity}
                  <div className={styles.route}>
                    {item.method} {item.route}
                  </div>
                </td>
                <td>{item.statusCode}</td>
                <td className={styles.slowCell}>{formatMs(item.durationMs)}</td>
                <td>{formatMs(item.expectedMs)}</td>
                <td>{formatMs(item.warehouseMs)}</td>
                <td>{formatMs(item.lakebaseMs)}</td>
                <td>{formatMs(item.appMs)}</td>
                <td>{item.userName ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <TablePager
        total={data.total}
        page={pageIndex}
        pageSize={pageSize}
        onPageChange={setPageIndex}
        onPageSizeChange={(size) => {
          setPageSize(size);
          setPageIndex(0);
        }}
      />
    </div>
  );
}

const USD = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Formats a list-price USD amount for display.
 *
 * @param value - Amount in USD, or null when it cannot be computed.
 */
export function formatUsd(value: number | null | undefined): string {
  return value == null ? "—" : USD.format(value);
}

const APP_COST_WINDOWS = [7, 14, 30] as const;

/**
 * App cost: Databricks Apps compute (today, last 7 days, per active day), average warehouse and
 * job cost per sync, and a daily chart. Costs are list prices from billing system tables.
 */
function AppCostCard() {
  const [days, setDays] = useState<number>(30);
  const query = usePerformanceAppCost(days);
  const report = query.data;
  const chartRows = useMemo(
    () =>
      (report?.daily ?? []).map((day) => ({
        label: day.usageDate.slice(5),
        app: day.appUsd,
        warehouse: day.warehouseUsd,
        job: day.jobUsd,
      })),
    [report],
  );

  return (
    <Card title="App cost">
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="app-cost-window">
            Cost period
          </label>
          <select
            id="app-cost-window"
            className={identityStyles.select}
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            {APP_COST_WINDOWS.map((option) => (
              <option key={option} value={option}>
                Last {option} days
              </option>
            ))}
          </select>
        </div>
      </div>
      {query.isPending ? <Spinner label="Reading billing" /> : null}
      {query.isError ? <ErrorAlert error={query.error} title="Could not read app cost" /> : null}
      {report && !report.available ? (
        <Alert tone="warning" title="App cost unavailable">
          {report.detail ?? "Billing could not be read."}
        </Alert>
      ) : null}
      {report?.available ? (
        <>
          <div className={styles.kpiGrid}>
            <Kpi label="Today so far" value={formatUsd(report.todayAppUsd)} hint="App compute, partial day" />
            <Kpi label="Last 7 days" value={formatUsd(report.appUsdLast7Days)} hint="App compute" />
            <Kpi
              label="Per active day"
              value={formatUsd(report.appUsdPerActiveDay)}
              hint="App compute, average"
            />
            <Kpi
              label="Avg cost per sync"
              value={formatUsd(report.avgUsdPerSync)}
              hint={
                report.syncCount > 0
                  ? `${report.syncCount} sync${report.syncCount === 1 ? "" : "s"} · ${formatUsd(report.syncUsd)} warehouse and job`
                  : "No finished syncs in this window"
              }
            />
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={chartRows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
              <XAxis dataKey="label" stroke="var(--color-text-muted)" />
              <YAxis stroke="var(--color-text-muted)" width={72} tickFormatter={(value: number) => USD.format(value)} />
              <Tooltip formatter={(value) => USD.format(Number(value))} />
              <Legend />
              <Bar dataKey="app" name="App compute" stackId="cost" fill={COST_COLORS.app} />
              <Bar dataKey="warehouse" name="SQL warehouse" stackId="cost" fill={COST_COLORS.warehouse} />
              <Bar dataKey="job" name="Sync job" stackId="cost" fill={COST_COLORS.job} />
            </BarChart>
          </ResponsiveContainer>
          <p className={styles.caption}>{report.lagNote}</p>
          {report.unpricedRecords > 0 ? (
            <p className={styles.caption}>
              {report.unpricedRecords} usage record{report.unpricedRecords === 1 ? "" : "s"} had no list price and
              count as $0.
            </p>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}

const COST_COLORS = {
  app: "#9a3412",
  warehouse: "#2272b4",
  job: "#b45309",
};

/**
 * Health-monitor page for app admins.
 */
export function PerformancePage() {
  const [hours, setHours] = useState<number>(24);
  const summaryQuery = usePerformanceSummary(hours);
  const capacityQuery = usePerformanceCapacity();
  const summary: PerformanceSummary | undefined = summaryQuery.data;
  const hourRows = useMemo(() => (summary ? buildHourRows(summary.hourly) : []), [summary]);

  return (
    <div className={styles.page}>
      <PageHeader
        title="Performance"
        description="How long each part of the app takes, what is slower than expected, and whether to resize the app, Lakebase, or the SQL warehouse."
      />

      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="performance-window">
            Window
          </label>
          <select
            id="performance-window"
            className={identityStyles.select}
            value={hours}
            onChange={(event) => setHours(Number(event.target.value))}
          >
            {WINDOW_OPTIONS.map((option) => (
              <option key={option.hours} value={option.hours}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {summary ? (
          <span className={styles.freshness}>Updated {new Date(summary.generatedAt).toLocaleTimeString()}</span>
        ) : null}
      </div>

      {summaryQuery.isPending ? <Spinner label="Loading performance summary" /> : null}
      {summaryQuery.isError ? (
        <ErrorAlert error={summaryQuery.error} title="Could not load performance summary" />
      ) : null}

      {summary && summary.kpis.totalRequests === 0 ? (
        <Alert tone="info" title="No requests recorded yet">
          Timings are stored for every API request. Use the app for a few minutes, then refresh.
        </Alert>
      ) : null}

      {summary && summary.kpis.totalRequests > 0 ? (
        <>
          <KpiGrid kpis={summary.kpis} />

          <Card title="Recommendations">
            <Recommendations items={summary.recommendations} />
            <p className={styles.caption}>
              Recommendations use where the time of slow requests went. Each suggestion is a
              starting point, not an automatic change.
            </p>
          </Card>

          <div className={styles.chartGrid}>
            <Card title="Where time goes, per hour">
              <TimeSplitChart rows={hourRows} />
              <p className={styles.caption}>Average milliseconds per request in each hour.</p>
            </Card>
            <Card title="Latency and concurrency, per hour">
              <LatencyChart rows={hourRows} />
            </Card>
          </div>

          <Card title="Latency by activity">
            <ActivityTable rows={summary.activities} />
            <p className={styles.caption}>
              Expected times are set per activity in the backend catalog. Red cells are over expected.
            </p>
          </Card>
        </>
      ) : null}

      <Card title="Current sizing">
        {capacityQuery.isPending ? <Spinner label="Reading sizing" /> : null}
        {capacityQuery.isError ? (
          <ErrorAlert error={capacityQuery.error} title="Could not read sizing" />
        ) : null}
        {capacityQuery.data ? <CapacityList settings={capacityQuery.data.settings} /> : null}
        <p className={styles.caption}>
          Read with the Account Admin service principal. Autoscaling ranges show the minimum and maximum
          Lakebase compute units.
        </p>
      </Card>

      <AppCostCard />

      <Card title="Slow requests">
        <SlowRequestsTable key={hours} hours={hours} />
      </Card>
    </div>
  );
}
