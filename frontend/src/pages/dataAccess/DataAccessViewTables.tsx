/**
 * @file Paginated tables over the data-access Lakebase SQL views (`data_access_runs_v`,
 * `data_access_run_grants_v`), used on the data-access analytics page.
 *
 * Pages are fetched from the server (1-based); {@link TablePager} works in 0-based indexes.
 */
import { useState } from "react";
import { useDataAccessAnalyticsGrants, useDataAccessAnalyticsRuns } from "@/api/queries";
import type { DataAccessViewPage } from "@/api/types";
import { Alert, ErrorAlert, Spinner, TablePager } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useServerTableView } from "@/hooks/useServerTableView";
import type { TableColumn } from "@/hooks/useTableView";
import type { TablePageSize } from "@/components/ui";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./DataAccessAnalyticsPage.module.css";

type ModeFilter = "all" | "dry_run" | "apply";
type ChangeFilter = "all" | "add" | "revoke";

/** Reads a string column from a view row, or an empty string when absent. */
function text(row: Record<string, unknown>, key: string): string {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

/** Reads a numeric column from a view row, or null when absent. */
function number(row: Record<string, unknown>, key: string): number | null {
  const value = row[key];
  return typeof value === "number" ? value : null;
}

/** Formats an ISO timestamp for display, or an em dash when absent. */
function when(value: string): string {
  return value ? new Date(value).toLocaleString() : "—";
}

/** Loading, error, and unavailable states for one view page. */
function ViewStatus({
  data,
  isPending,
  isError,
  error,
  errorTitle,
}: {
  data: DataAccessViewPage | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  errorTitle: string;
}) {
  if (isError) return <ErrorAlert error={error} title={errorTitle} />;
  if (isPending && !data) return <Spinner label="Loading rows" />;
  if (data && !data.available) {
    return (
      <Alert tone="info" title={`${data.source} is not available`}>
        {data.detail ?? "The SQL views exist only on the Lakebase backend."}
      </Alert>
    );
  }
  return null;
}

/** Shared pager wiring: converts the server's 1-based page to TablePager's 0-based index. */
function usePaging() {
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(25);
  return {
    pageIndex,
    pageSize,
    page: pageIndex + 1,
    setPageIndex,
    setPageSize,
  };
}

/** Run-history columns. Keys match the backend `RUNS_VIEW_COLUMNS` whitelist. */
const RUN_COLUMNS: TableColumn<unknown>[] = [
  { key: "started", label: "Started", sortable: true, filterable: true },
  { key: "mode", label: "Mode", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "triggeredBy", label: "Triggered by", sortable: true, filterable: true },
  { key: "duration", label: "Duration", sortable: true, filterable: true },
  { key: "grants", label: "Grants +/−", sortable: true, filterable: true },
  { key: "members", label: "Members +/−", sortable: true, filterable: true },
  { key: "errors", label: "Errors", sortable: true, filterable: true },
  { key: "fingerprint", label: "Fingerprint", sortable: true, filterable: true },
];

/** Planned grant-change columns. Keys match the backend `GRANTS_VIEW_COLUMNS` whitelist. */
const GRANT_COLUMNS: TableColumn<unknown>[] = [
  { key: "started", label: "Started", sortable: true, filterable: true },
  { key: "mode", label: "Mode", sortable: true, filterable: true },
  { key: "change", label: "Change", sortable: true, filterable: true },
  { key: "securable", label: "Securable", sortable: true, filterable: true },
  { key: "fullName", label: "Full name", sortable: true, filterable: true },
  { key: "principal", label: "Principal", sortable: true, filterable: true },
  { key: "privilege", label: "Privilege", sortable: true, filterable: true },
  { key: "reason", label: "Reason", sortable: true, filterable: true },
];

/**
 * Paginated sync run history from `data_access_runs_v`, newest first.
 */
export function RunHistoryTable() {
  const [mode, setMode] = useState<ModeFilter>("all");
  const paging = usePaging();
  const view = useServerTableView(RUN_COLUMNS, { onChange: () => paging.setPageIndex(0) });
  const query = useDataAccessAnalyticsRuns({
    mode: mode === "all" ? undefined : mode,
    page: paging.page,
    pageSize: paging.pageSize,
    ...view.query,
  });
  const data = query.data;
  const rows = data?.rows ?? [];

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="runs-mode">
            Run mode
          </label>
          <select
            id="runs-mode"
            className={identityStyles.select}
            value={mode}
            onChange={(event) => {
              setMode(event.target.value as ModeFilter);
              paging.setPageIndex(0);
            }}
          >
            <option value="all">All modes</option>
            <option value="dry_run">Dry run</option>
            <option value="apply">Live (apply)</option>
          </select>
        </div>
        {data && data.available ? (
          <span className={styles.freshness}>Source: {data.source}</span>
        ) : null}
      </div>

      <ViewStatus
        data={data}
        isPending={query.isPending}
        isError={query.isError}
        error={query.error}
        errorTitle="Could not load run history"
      />

      {data && data.available && rows.length === 0 && !query.isFetching ? (
        <p className={identityStyles.muted}>No runs match these filters.</p>
      ) : null}

      {data && data.available && rows.length > 0 ? (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="Sync run history">
            <DataTableHead view={view} />
            <tbody>
              {rows.map((row) => {
                const runId = text(row, "runId");
                const duration = number(row, "durationSeconds");
                const errorCount = number(row, "applyErrorCount") ?? 0;
                const hasError = text(row, "error") !== "" || errorCount > 0;
                return (
                  <tr key={runId}>
                    <td>{when(text(row, "startedAt"))}</td>
                    <td>{text(row, "mode") === "apply" ? "Live" : "Dry run"}</td>
                    <td>{text(row, "status")}</td>
                    <td>{text(row, "triggeredBy")}</td>
                    <td>{duration === null ? "—" : `${duration.toFixed(1)} s`}</td>
                    <td>
                      +{number(row, "grantsToAdd") ?? 0} / −{number(row, "grantsToRevoke") ?? 0}
                    </td>
                    <td>
                      +{number(row, "membersToAdd") ?? 0} / −{number(row, "membersToRemove") ?? 0}
                    </td>
                    <td className={hasError ? styles.errorCell : undefined}>
                      {hasError
                        ? [text(row, "error"), errorCount ? `${errorCount} apply error(s)` : ""]
                            .filter(Boolean)
                            .join(" · ")
                        : "—"}
                    </td>
                    <td>
                      <code>{text(row, "fingerprint").slice(0, 12) || "—"}</code>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}

      {data && data.available ? (
        <TablePager
          total={data.total}
          page={paging.pageIndex}
          pageSize={paging.pageSize}
          onPageChange={paging.setPageIndex}
          onPageSizeChange={paging.setPageSize}
        />
      ) : null}
    </div>
  );
}

/**
 * Paginated planned grant changes from `data_access_run_grants_v`, newest run first.
 */
export function GrantChangesTable() {
  const [mode, setMode] = useState<ModeFilter>("all");
  const [change, setChange] = useState<ChangeFilter>("all");
  const paging = usePaging();
  const view = useServerTableView(GRANT_COLUMNS, { onChange: () => paging.setPageIndex(0) });
  const query = useDataAccessAnalyticsGrants({
    mode: mode === "all" ? undefined : mode,
    change: change === "all" ? undefined : change,
    page: paging.page,
    pageSize: paging.pageSize,
    ...view.query,
  });
  const data = query.data;
  const rows = data?.rows ?? [];

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="grants-mode">
            Grant mode
          </label>
          <select
            id="grants-mode"
            className={identityStyles.select}
            value={mode}
            onChange={(event) => {
              setMode(event.target.value as ModeFilter);
              paging.setPageIndex(0);
            }}
          >
            <option value="all">All modes</option>
            <option value="dry_run">Dry run</option>
            <option value="apply">Live (apply)</option>
          </select>
        </div>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="grants-change">
            Change
          </label>
          <select
            id="grants-change"
            className={identityStyles.select}
            value={change}
            onChange={(event) => {
              setChange(event.target.value as ChangeFilter);
              paging.setPageIndex(0);
            }}
          >
            <option value="all">Grants and revokes</option>
            <option value="add">Grants (add)</option>
            <option value="revoke">Revokes</option>
          </select>
        </div>
        {data && data.available ? (
          <span className={styles.freshness}>Source: {data.source}</span>
        ) : null}
      </div>

      <ViewStatus
        data={data}
        isPending={query.isPending}
        isError={query.isError}
        error={query.error}
        errorTitle="Could not load grant changes"
      />

      {data && data.available && rows.length === 0 && !query.isFetching ? (
        <p className={identityStyles.muted}>No grant changes match these filters.</p>
      ) : null}

      {data && data.available && rows.length > 0 ? (
        <div className={identityStyles.tableWrap}>
          <table className={identityStyles.table} aria-label="Planned grant changes">
            <DataTableHead view={view} />
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${text(row, "runId")}-${index}`}>
                  <td>{when(text(row, "startedAt"))}</td>
                  <td>{text(row, "mode") === "apply" ? "Live" : "Dry run"}</td>
                  <td>{text(row, "change")}</td>
                  <td>{text(row, "securableType")}</td>
                  <td>
                    <code>{text(row, "fullName")}</code>
                  </td>
                  <td>{text(row, "principal")}</td>
                  <td>{text(row, "privilege")}</td>
                  <td>{text(row, "reason") || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {data && data.available ? (
        <TablePager
          total={data.total}
          page={paging.pageIndex}
          pageSize={paging.pageSize}
          onPageChange={paging.setPageIndex}
          onPageSizeChange={paging.setPageSize}
        />
      ) : null}
    </div>
  );
}
