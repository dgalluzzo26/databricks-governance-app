/**
 * @file Admin analytics dashboard for data-access sync runs.
 *
 * KPIs and charts over the stored run history (`GET /data-access/analytics`). The same run
 * data is exposed as Lakebase SQL views (`governance_app.data_access_*_v`) for ad-hoc queries.
 * Recharts renders the time series; tables list recent failures and desired-state fingerprints.
 */
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
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
import { useDataAccessAnalytics } from "@/api/queries";
import type { DataAccessAnalytics, DataAccessAnalyticsDay } from "@/api/types";
import { Alert, Card, ErrorAlert, PageHeader, Spinner } from "@/components/ui";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./DataAccessAnalyticsPage.module.css";

const WINDOW_OPTIONS = [7, 14, 30] as const;
type ModeFilter = "all" | "dry_run" | "apply";

/** Chart palette; mirrors the success / danger / primary / warning token hues. */
const COLORS = {
  success: "#2f9e6b",
  failed: "#dc2626",
  grantAdd: "#2272b4",
  grantRevoke: "#9a3412",
  memberAdd: "#0f766e",
  memberRemove: "#b45309",
  duration: "#2272b4",
};

/** One chart row per UTC day, with modes combined or filtered. */
export interface ChartDayRow {
  day: string;
  label: string;
  runs: number;
  success: number;
  failed: number;
  grantsAdded: number;
  grantsRevoked: number;
  membersAdded: number;
  membersRemoved: number;
  avgDurationSeconds: number | null;
}

/**
 * Collapses per-mode daily rows into one row per day for the selected mode filter.
 *
 * @param daily - Rows from the analytics payload (one per day and mode).
 * @param mode - `all`, `dry_run`, or `apply`.
 * @returns Rows sorted by day ascending. Average duration is weighted by finished runs.
 */
export function buildChartRows(daily: DataAccessAnalyticsDay[], mode: ModeFilter): ChartDayRow[] {
  const byDay = new Map<string, ChartDayRow & { durationTotal: number; finished: number }>();
  for (const row of daily) {
    if (mode !== "all" && row.mode !== mode) continue;
    const existing = byDay.get(row.day) ?? {
      day: row.day,
      label: row.day.slice(5),
      runs: 0,
      success: 0,
      failed: 0,
      grantsAdded: 0,
      grantsRevoked: 0,
      membersAdded: 0,
      membersRemoved: 0,
      avgDurationSeconds: null,
      durationTotal: 0,
      finished: 0,
    };
    const finished = row.success + row.failed;
    existing.runs += row.runs;
    existing.success += row.success;
    existing.failed += row.failed;
    existing.grantsAdded += row.grantsAdded;
    existing.grantsRevoked += row.grantsRevoked;
    existing.membersAdded += row.membersAdded;
    existing.membersRemoved += row.membersRemoved;
    if (row.avgDurationSeconds !== null && finished > 0) {
      existing.durationTotal += row.avgDurationSeconds * finished;
      existing.finished += finished;
    }
    byDay.set(row.day, existing);
  }
  return [...byDay.values()]
    .sort((a, b) => a.day.localeCompare(b.day))
    .map(({ durationTotal, finished, ...row }) => ({
      ...row,
      avgDurationSeconds: finished > 0 ? Math.round((durationTotal / finished) * 10) / 10 : null,
    }));
}

/** Formats a 0..1 rate as a whole percentage, or an em dash when unknown. */
function formatRate(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

/** Formats seconds with one decimal, or an em dash when unknown. */
function formatSeconds(seconds: number | null): string {
  return seconds === null ? "—" : `${seconds.toFixed(1)} s`;
}

/** Formats an ISO timestamp for display, or an em dash when absent. */
function formatWhen(value: string | null): string {
  return value ? new Date(value).toLocaleString() : "—";
}

/** One headline number with an optional caption. */
function Kpi({ label, value, hint, tone }: { label: string; value: string; hint?: string; tone?: "danger" }) {
  return (
    <div className={styles.kpi}>
      <span className={styles.kpiLabel}>{label}</span>
      <span className={`${styles.kpiValue}${tone === "danger" ? ` ${styles.kpiDanger}` : ""}`}>{value}</span>
      {hint ? <span className={styles.kpiHint}>{hint}</span> : null}
    </div>
  );
}

/** Headline tiles for the selected window. */
function KpiGrid({ data }: { data: DataAccessAnalytics }) {
  const { kpis } = data;
  return (
    <div className={styles.kpiGrid}>
      <Kpi
        label="Sync runs"
        value={String(kpis.totalRuns)}
        hint={`${kpis.successRuns} succeeded · ${kpis.failedRuns} failed · ${kpis.runningRuns} running`}
      />
      <Kpi label="Success rate" value={formatRate(kpis.successRate)} hint="Of finished runs" />
      <Kpi
        label="Failed runs"
        value={String(kpis.failedRuns)}
        tone={kpis.failedRuns > 0 ? "danger" : undefined}
        hint={kpis.lastFailureAt ? `Last ${formatWhen(kpis.lastFailureAt)}` : "No failures in window"}
      />
      <Kpi label="Avg duration" value={formatSeconds(kpis.avgDurationSeconds)} hint="Finished runs" />
      <Kpi label="Last success" value={formatWhen(kpis.lastSuccessAt)} />
      <Kpi
        label="Desired-state changes"
        value={String(kpis.fingerprintChanges)}
        hint={`${kpis.distinctFingerprints} distinct fingerprint${kpis.distinctFingerprints === 1 ? "" : "s"}`}
      />
    </div>
  );
}

/** Stacked bars: successful vs failed runs per day. */
function RunOutcomesChart({ rows }: { rows: ChartDayRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis allowDecimals={false} stroke="var(--color-text-muted)" />
        <Tooltip />
        <Legend />
        <Bar dataKey="success" name="Succeeded" stackId="runs" fill={COLORS.success} />
        <Bar dataKey="failed" name="Failed" stackId="runs" fill={COLORS.failed} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Grouped bars: planned grants and membership changes per day (successful runs only). */
function PlannedChangesChart({ rows }: { rows: ChartDayRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis allowDecimals={false} stroke="var(--color-text-muted)" />
        <Tooltip />
        <Legend />
        <Bar dataKey="grantsAdded" name="Grants added" fill={COLORS.grantAdd} />
        <Bar dataKey="grantsRevoked" name="Grants revoked" fill={COLORS.grantRevoke} />
        <Bar dataKey="membersAdded" name="Members added" fill={COLORS.memberAdd} />
        <Bar dataKey="membersRemoved" name="Members removed" fill={COLORS.memberRemove} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Line of average wall time per day for finished runs. */
function DurationChart({ rows }: { rows: ChartDayRow[] }) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
        <XAxis dataKey="label" stroke="var(--color-text-muted)" />
        <YAxis stroke="var(--color-text-muted)" unit=" s" />
        <Tooltip />
        <Line
          type="monotone"
          dataKey="avgDurationSeconds"
          name="Avg duration (s)"
          stroke={COLORS.duration}
          strokeWidth={2}
          connectNulls
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Table of the most recent failed runs. */
function FailuresTable({ data }: { data: DataAccessAnalytics }) {
  if (data.failures.length === 0) {
    return <p className={identityStyles.muted}>No failed runs in this window.</p>;
  }
  return (
    <div className={identityStyles.tableWrap}>
      <table className={identityStyles.table} aria-label="Recent failed sync runs">
        <thead>
          <tr>
            <th>Started</th>
            <th>Mode</th>
            <th>Triggered by</th>
            <th>Error</th>
          </tr>
        </thead>
        <tbody>
          {data.failures.map((failure) => (
            <tr key={failure.runId}>
              <td>{formatWhen(failure.startedAt)}</td>
              <td>{failure.mode === "apply" ? "Live" : "Dry run"}</td>
              <td>{failure.triggeredBy}</td>
              <td className={styles.errorCell}>{failure.error ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Table of desired-state fingerprints with how often each appeared. */
function FingerprintTable({ data }: { data: DataAccessAnalytics }) {
  if (data.fingerprints.length === 0) {
    return <p className={identityStyles.muted}>No successful runs in this window.</p>;
  }
  return (
    <div className={identityStyles.tableWrap}>
      <table className={identityStyles.table} aria-label="Desired-state fingerprints">
        <thead>
          <tr>
            <th>Fingerprint</th>
            <th>Runs</th>
            <th>First seen</th>
            <th>Last seen</th>
          </tr>
        </thead>
        <tbody>
          {data.fingerprints.map((fp) => (
            <tr key={fp.fingerprint}>
              <td>
                <code>{fp.fingerprint}</code>
              </td>
              <td>{fp.runs}</td>
              <td>{formatWhen(fp.firstSeen)}</td>
              <td>{formatWhen(fp.lastSeen)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Admin dashboard: window and mode filters, KPIs, charts, and recent failures.
 */
export function DataAccessAnalyticsPage() {
  const [days, setDays] = useState<number>(30);
  const [mode, setMode] = useState<ModeFilter>("all");
  const query = useDataAccessAnalytics(days);
  const data = query.data;
  const rows = useMemo(() => (data ? buildChartRows(data.daily, mode) : []), [data, mode]);

  return (
    <div className={styles.page}>
      <PageHeader
        title="Data access analytics"
        description="Sync health, planned changes, and desired-state stability from stored run history."
        actions={
          <Link to="/data-access" className={styles.backLink}>
            Back to Data access
          </Link>
        }
      />

      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="analytics-window">
            Window
          </label>
          <select
            id="analytics-window"
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
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="analytics-mode">
            Mode
          </label>
          <select
            id="analytics-mode"
            className={identityStyles.select}
            value={mode}
            onChange={(event) => setMode(event.target.value as ModeFilter)}
          >
            <option value="all">All modes</option>
            <option value="dry_run">Dry run</option>
            <option value="apply">Live (apply)</option>
          </select>
        </div>
        {data ? (
          <span className={styles.freshness}>
            Source: data_access_runs · updated {new Date(data.generatedAt).toLocaleTimeString()}
          </span>
        ) : null}
      </div>

      {query.isPending ? <Spinner label="Loading analytics" /> : null}
      {query.isError ? <ErrorAlert error={query.error} title="Could not load analytics" /> : null}

      {data && data.kpis.totalRuns === 0 ? (
        <Alert tone="info" title="No sync runs yet">
          No runs in the last {data.days} days. Run a dry run on the Data access page to start collecting history.
        </Alert>
      ) : null}

      {data && data.kpis.totalRuns > 0 ? (
        <>
          <KpiGrid data={data} />

          <div className={styles.chartGrid}>
            <Card title="Run outcomes per day">
              {rows.length > 0 ? (
                <RunOutcomesChart rows={rows} />
              ) : (
                <p className={identityStyles.muted}>No runs for this mode in the window.</p>
              )}
            </Card>
            <Card title="Planned changes per day">
              {rows.length > 0 ? (
                <>
                  <PlannedChangesChart rows={rows} />
                  <p className={styles.caption}>
                    Sums of successful runs. Dry-run numbers are planned changes, not applied ones.
                  </p>
                </>
              ) : (
                <p className={identityStyles.muted}>No runs for this mode in the window.</p>
              )}
            </Card>
            <Card title="Average run duration">
              {rows.length > 0 ? (
                <DurationChart rows={rows} />
              ) : (
                <p className={identityStyles.muted}>No runs for this mode in the window.</p>
              )}
            </Card>
            <Card title="Desired-state fingerprints">
              <FingerprintTable data={data} />
              <p className={styles.caption}>
                Each run hashes its desired grants, group nesting, and membership adds. Same hash = no change in desired state.
              </p>
            </Card>
          </div>

          <Card title="Recent failures">
            <FailuresTable data={data} />
          </Card>
        </>
      ) : null}
    </div>
  );
}
