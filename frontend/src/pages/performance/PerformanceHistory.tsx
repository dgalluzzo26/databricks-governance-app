/**
 * @file Admin history tabs on the Performance page: every user's requests (with success and
 * failure filters) and every user's tracked tasks with their steps.
 */
import { Fragment, useState } from "react";
import { usePerformanceRequests, usePerformanceTasks } from "@/api/queries";
import type { RequestStatusFilter, TaskStatus } from "@/api/types";
import { elapsedSeconds, formatDuration } from "@/components/activity/activityFormat";
import { StatusIcon, TaskStepList, stepProgress } from "@/components/activity/TaskSteps";
import { Badge, ErrorAlert, Spinner, TablePager } from "@/components/ui";
import type { TablePageSize } from "@/components/ui";
import { DataTableHead } from "@/components/ui/DataTableHead";
import { useServerTableView } from "@/hooks/useServerTableView";
import type { TableColumn } from "@/hooks/useTableView";
import identityStyles from "../identity/IdentityPage.module.css";
import styles from "./PerformancePage.module.css";

/** Formats milliseconds as `850 ms` or `12.4 s`. */
function ms(value: number): string {
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${Math.round(value)} ms`;
}

/** Request history columns. Keys match the backend `REQUEST_READERS` whitelist. */
const REQUEST_COLUMNS: TableColumn<unknown>[] = [
  { key: "when", label: "When", sortable: true, filterable: true },
  { key: "activity", label: "Activity", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "total", label: "Total", sortable: true, filterable: true },
  { key: "user", label: "User", sortable: true, filterable: true },
  { key: "error", label: "Error", sortable: true, filterable: true },
  { key: "request", label: "Request ID", sortable: false, filterable: true },
];

const REQUEST_FILTERS: { value: RequestStatusFilter; label: string }[] = [
  { value: "all", label: "All requests" },
  { value: "failed", label: "Failed only" },
  { value: "succeeded", label: "Succeeded only" },
];

/**
 * Every user's API requests in the window, newest first, with success and failure counts.
 * @param props.hours - Window length in hours.
 */
export function RequestsTab({ hours }: { hours: number }) {
  const [status, setStatus] = useState<RequestStatusFilter>("all");
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(25);
  const view = useServerTableView(REQUEST_COLUMNS, { onChange: () => setPageIndex(0) });
  const query = usePerformanceRequests({ hours, status, page: pageIndex + 1, pageSize, ...view.query });
  const data = query.data;

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="request-status">
            Outcome
          </label>
          <select
            id="request-status"
            className={identityStyles.select}
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as RequestStatusFilter);
              setPageIndex(0);
            }}
          >
            {REQUEST_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {data ? (
          <span className={styles.counts}>
            <Badge tone="success">{data.succeeded} succeeded</Badge>
            <Badge tone={data.failed > 0 ? "danger" : "neutral"}>{data.failed} failed</Badge>
          </span>
        ) : null}
      </div>

      {query.isPending && !data ? <Spinner label="Loading requests" /> : null}
      {query.isError ? <ErrorAlert error={query.error} title="Could not load request history" /> : null}
      {data && data.total === 0 ? <p className={identityStyles.muted}>No requests match in this window.</p> : null}
      {data && data.total > 0 ? (
        <>
          <div className={identityStyles.tableWrap}>
            <table className={identityStyles.table} aria-label="Request history">
              <DataTableHead view={view} />
              <tbody>
                {data.items.map((item, index) => (
                  <tr key={`${item.recordedAt}-${item.requestId ?? index}`}>
                    <td>{new Date(item.recordedAt).toLocaleString()}</td>
                    <td>
                      {item.activity}
                      <div className={styles.route}>
                        {item.method} {item.route}
                      </div>
                    </td>
                    <td>
                      <Badge tone={item.ok ? "success" : item.statusCode >= 500 ? "danger" : "warning"}>
                        {item.statusCode}
                      </Badge>
                    </td>
                    <td>{ms(item.durationMs)}</td>
                    <td>{item.userName ?? "—"}</td>
                    <td className={item.ok ? undefined : styles.errorCell}>
                      {item.ok ? "—" : (
                        <>
                          {item.errorCode ? <code>{item.errorCode}</code> : null} {item.errorMessage ?? ""}
                        </>
                      )}
                    </td>
                    <td className={styles.route}>{item.requestId ?? "—"}</td>
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
        </>
      ) : null}
    </div>
  );
}

/** Task history columns. Keys match the backend `TASK_READERS` whitelist. */
const TASK_COLUMNS: TableColumn<unknown>[] = [
  { key: "started", label: "Started", sortable: true, filterable: true },
  { key: "task", label: "Task", sortable: true, filterable: true },
  { key: "status", label: "Status", sortable: true, filterable: true },
  { key: "actor", label: "User", sortable: true, filterable: true },
  { key: "duration", label: "Duration", sortable: true, filterable: false },
  { key: "error", label: "Outcome", sortable: true, filterable: true },
  { key: "steps", label: "Steps", sortable: false, filterable: false },
];

const TASK_FILTERS: { value: TaskStatus | "all"; label: string }[] = [
  { value: "all", label: "All tasks" },
  { value: "running", label: "Running" },
  { value: "failed", label: "Failed" },
  { value: "succeeded", label: "Succeeded" },
];

/**
 * Every user's tracked tasks in the window, with expandable steps.
 * @param props.hours - Window length in hours.
 */
export function TasksTab({ hours }: { hours: number }) {
  const [status, setStatus] = useState<TaskStatus | "all">("all");
  const [pageIndex, setPageIndex] = useState(0);
  const [pageSize, setPageSize] = useState<TablePageSize>(25);
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(new Set());
  const view = useServerTableView(TASK_COLUMNS, { onChange: () => setPageIndex(0) });
  const query = usePerformanceTasks({
    hours,
    status: status === "all" ? null : status,
    page: pageIndex + 1,
    pageSize,
    ...view.query,
  });
  const data = query.data;

  const toggle = (taskId: string) =>
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(taskId)) next.delete(taskId);
      else next.add(taskId);
      return next;
    });

  return (
    <div className={styles.tableStack}>
      <div className={identityStyles.toolbar}>
        <div className={identityStyles.field}>
          <label className={identityStyles.label} htmlFor="task-status">
            Status
          </label>
          <select
            id="task-status"
            className={identityStyles.select}
            value={status}
            onChange={(event) => {
              setStatus(event.target.value as TaskStatus | "all");
              setPageIndex(0);
            }}
          >
            {TASK_FILTERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
        {data ? (
          <span className={styles.counts}>
            <Badge tone={data.running > 0 ? "info" : "neutral"}>{data.running} running</Badge>
            <Badge tone={data.failed > 0 ? "danger" : "neutral"}>{data.failed} failed</Badge>
          </span>
        ) : null}
      </div>

      {query.isPending && !data ? <Spinner label="Loading tasks" /> : null}
      {query.isError ? <ErrorAlert error={query.error} title="Could not load task history" /> : null}
      {data && data.total === 0 ? (
        <p className={identityStyles.muted}>
          No tasks in this window. Saving mask rules, syncing policy packs, recovery, and Job runs are tracked.
        </p>
      ) : null}
      {data && data.total > 0 ? (
        <>
          <div className={identityStyles.tableWrap}>
            <table className={identityStyles.table} aria-label="Task history">
              <DataTableHead view={view} />
              <tbody>
                {data.items.map((task) => {
                  const open = expanded.has(task.taskId);
                  return (
                    <Fragment key={task.taskId}>
                      <tr>
                        <td>{new Date(task.startedAt).toLocaleString()}</td>
                        <td>
                          {task.title}
                          <div className={styles.route}>{task.kind}</div>
                        </td>
                        <td>
                          <span className={styles.statusCell}>
                            <StatusIcon status={task.status} />
                            {task.status}
                          </span>
                        </td>
                        <td>{task.actor}</td>
                        <td>{formatDuration(elapsedSeconds(task))}</td>
                        <td className={task.status === "failed" ? styles.errorCell : undefined}>
                          {task.status === "failed"
                            ? `${task.errorCode ? `${task.errorCode}: ` : ""}${task.errorMessage ?? ""}`
                            : (task.summary ?? "—")}
                        </td>
                        <td>
                          {task.steps.length > 0 ? (
                            <button
                              type="button"
                              className={styles.linkButton}
                              aria-expanded={open}
                              onClick={() => toggle(task.taskId)}
                            >
                              {open ? "Hide" : stepProgress(task)}
                            </button>
                          ) : (
                            "—"
                          )}
                        </td>
                      </tr>
                      {open ? (
                        <tr className={styles.stepsRow}>
                          <td colSpan={TASK_COLUMNS.length}>
                            <TaskStepList task={task} />
                            {task.requestId ? <div className={styles.route}>Request {task.requestId}</div> : null}
                          </td>
                        </tr>
                      ) : null}
                    </Fragment>
                  );
                })}
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
        </>
      ) : null}
    </div>
  );
}
