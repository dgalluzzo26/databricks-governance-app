/**
 * @file Activity side panel: the signed-in user's running and recent tasks with their steps,
 * and recent errors (from the server and from this browser session).
 *
 * Opened from the top bar. The feed polls quickly while a task runs or an action is in flight,
 * and slowly otherwise so the badge picks up background Job outcomes.
 */
import { useIsMutating, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { queryKeys, useActivityFeed } from "@/api/queries";
import type { RequestRecord, TaskRecord } from "@/api/types";
import { clearErrors, useSessionErrors, type SessionError } from "@/app/errorLog";
import { ActivityIcon, CloseIcon } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { elapsedSeconds, formatDuration, timeAgo } from "./activityFormat";
import { StatusIcon, TaskStepList, stepProgress } from "./TaskSteps";
import styles from "./ActivityPanel.module.css";

/** localStorage key: newest activity the user has seen (drives the unseen badge). */
export const SEEN_KEY = "activity.seenAt";
/** localStorage key: errors at or before this time are hidden after "Clear". */
export const CLEARED_KEY = "activity.clearedAt";

/** One row in the panel's error list. */
export interface ActivityErrorEntry {
  key: string;
  at: string;
  title: string;
  message: string;
  status: number | null;
  requestId: string | null;
  /** `server` for stored failed requests; otherwise where the browser caught it. */
  source: "server" | SessionError["source"];
}

function readTime(key: string): number {
  const raw = typeof window === "undefined" ? null : window.localStorage.getItem(key);
  const value = raw ? Number(raw) : 0;
  return Number.isFinite(value) ? value : 0;
}

function writeTime(key: string, value: number): void {
  window.localStorage.setItem(key, String(value));
}

/**
 * Merges stored failed requests with this session's errors, newest first.
 *
 * Session errors that the server also stored (same request ID) and errors behind a failed task
 * (same request ID) are shown once; anything at or before `clearedAt` is hidden.
 *
 * @param server - Failed requests from `GET /activity`.
 * @param session - Errors caught in this browser.
 * @param failedTasks - Failed tasks already shown in the task list.
 * @param clearedAt - Epoch milliseconds of the last Clear.
 */
export function mergeErrors(
  server: RequestRecord[],
  session: SessionError[],
  failedTasks: TaskRecord[],
  clearedAt: number,
): ActivityErrorEntry[] {
  const taskRequests = new Set(failedTasks.map((task) => task.requestId).filter(Boolean));
  const serverIds = new Set(server.map((item) => item.requestId).filter(Boolean));
  const entries: ActivityErrorEntry[] = [
    ...server
      .filter((item) => !item.requestId || !taskRequests.has(item.requestId))
      .map((item, index) => ({
        key: `server-${item.requestId ?? index}`,
        at: item.recordedAt,
        title: item.activity,
        message: item.errorMessage ?? `HTTP ${item.statusCode}`,
        status: item.statusCode,
        requestId: item.requestId,
        source: "server" as const,
      })),
    ...session
      .filter((item) => !item.requestId || (!serverIds.has(item.requestId) && !taskRequests.has(item.requestId)))
      .map((item) => ({
        key: `session-${item.id}`,
        at: item.at,
        title: item.label ? `${item.title} · ${item.label}` : item.title,
        message: item.message,
        status: item.status,
        requestId: item.requestId,
        source: item.source,
      })),
  ];
  return entries
    .filter((entry) => new Date(entry.at).getTime() > clearedAt)
    .sort((a, b) => new Date(b.at).getTime() - new Date(a.at).getTime());
}

/** Ticks every second while `active`, so running durations update. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return now;
}

function TaskCard({ task, now, onNavigate }: { task: TaskRecord; now: number; onNavigate: () => void }) {
  const [expanded, setExpanded] = useState(task.status !== "succeeded");
  useEffect(() => {
    if (task.status === "failed") setExpanded(true);
  }, [task.status]);
  return (
    <li className={`${styles.task} ${styles[task.status]}`}>
      <div className={styles.taskHead}>
        <StatusIcon status={task.status} />
        <div className={styles.taskTitleBlock}>
          <strong className={styles.taskTitle}>{task.title}</strong>
          <span className={styles.meta}>
            {timeAgo(task.startedAt, now)} · {formatDuration(elapsedSeconds(task, now))}
            {task.steps.length > 0 ? ` · ${stepProgress(task)}` : ""}
          </span>
        </div>
        {task.steps.length > 0 ? (
          <button
            type="button"
            className={styles.linkButton}
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            {expanded ? "Hide steps" : "Show steps"}
          </button>
        ) : null}
      </div>
      {expanded ? (
        <div className={styles.taskSteps}>
          <TaskStepList task={task} />
        </div>
      ) : null}
      {task.status === "failed" && task.errorMessage ? (
        <p className={styles.taskError}>{task.errorMessage}</p>
      ) : null}
      {task.summary ? <p className={styles.summary}>{task.summary}</p> : null}
      <div className={styles.taskFoot}>
        {task.link ? (
          <Link to={task.link} className={styles.footLink} onClick={onNavigate}>
            Open page
          </Link>
        ) : null}
        {task.requestId ? <span className={styles.reference}>Ref {task.requestId}</span> : null}
      </div>
    </li>
  );
}

function ErrorRow({ entry, now }: { entry: ActivityErrorEntry; now: number }) {
  return (
    <li className={styles.errorRow}>
      <div className={styles.errorHead}>
        <StatusIcon status="failed" />
        <strong className={styles.errorTitle}>{entry.title}</strong>
        {entry.status ? <span className={styles.status}>{entry.status === 0 ? "offline" : entry.status}</span> : null}
      </div>
      <p className={styles.errorMessage}>{entry.message}</p>
      <span className={styles.meta}>
        {timeAgo(entry.at, now)}
        {entry.source !== "server" ? " · this browser" : ""}
        {entry.requestId ? ` · Ref ${entry.requestId}` : ""}
      </span>
    </li>
  );
}

/**
 * Top-bar button that opens the Activity panel. The badge shows running tasks, or failures the
 * user has not seen yet.
 */
export function ActivityButton() {
  const [open, setOpen] = useState(false);
  const [seenAt, setSeenAt] = useState(() => readTime(SEEN_KEY));
  const [clearedAt, setClearedAt] = useState(() => readTime(CLEARED_KEY));
  const mutating = useIsMutating();
  const queryClient = useQueryClient();
  const feed = useActivityFeed(mutating > 0);
  const sessionErrors = useSessionErrors();
  const previousMutating = useRef(mutating);
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (previousMutating.current === mutating) return;
    previousMutating.current = mutating;
    void queryClient.invalidateQueries({ queryKey: queryKeys.activity });
  }, [mutating, queryClient]);

  const tasks = feed.data?.tasks.items ?? [];
  const running = feed.data?.tasks.running ?? 0;
  const failedTasks = tasks.filter((task) => task.status === "failed");
  const errors = mergeErrors(feed.data?.errors.items ?? [], sessionErrors, failedTasks, clearedAt);
  const unseen =
    failedTasks.filter((task) => new Date(task.finishedAt ?? task.updatedAt).getTime() > seenAt).length +
    errors.filter((entry) => new Date(entry.at).getTime() > seenAt).length;
  const now = useNow(open && running > 0);

  const markSeen = useCallback(() => {
    const stamp = Date.now();
    writeTime(SEEN_KEY, stamp);
    setSeenAt(stamp);
  }, []);

  const close = useCallback(() => {
    markSeen();
    setOpen(false);
  }, [markSeen]);

  useEffect(() => {
    if (!open) return undefined;
    headingRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, close]);

  const clear = () => {
    const stamp = Date.now();
    writeTime(CLEARED_KEY, stamp);
    setClearedAt(stamp);
    clearErrors();
  };

  const label =
    running > 0
      ? `Activity: ${running} running`
      : unseen > 0
        ? `Activity: ${unseen} new problem${unseen === 1 ? "" : "s"}`
        : "Activity";

  return (
    <>
      <span className={styles.trigger}>
        <IconButton
          label={label}
          aria-expanded={open}
          aria-controls="activity-panel"
          onClick={() => (open ? close() : (markSeen(), setOpen(true)))}
        >
          <ActivityIcon />
        </IconButton>
        {running > 0 ? (
          <span className={`${styles.badge} ${styles.badgeRunning}`} aria-hidden>
            {running}
          </span>
        ) : unseen > 0 ? (
          <span className={`${styles.badge} ${styles.badgeDanger}`} aria-hidden>
            {unseen > 9 ? "9+" : unseen}
          </span>
        ) : null}
      </span>

      {open ? (
        <aside id="activity-panel" className={styles.panel} aria-label="Activity">
          <header className={styles.header}>
            <h2 className={styles.heading} ref={headingRef} tabIndex={-1}>
              Activity
            </h2>
            <IconButton label="Close activity" onClick={close}>
              <CloseIcon />
            </IconButton>
          </header>
          <p className={styles.intro}>
            Your own tasks and errors. Long actions show each step as it runs; Jobs started from the
            app keep reporting here after the page returns.
          </p>

          <section className={styles.section} aria-labelledby="activity-tasks">
            <h3 id="activity-tasks" className={styles.sectionTitle}>
              Tasks
              {running > 0 ? <span className={styles.count}>{running} running</span> : null}
            </h3>
            {feed.isPending ? <p className={styles.empty}>Loading…</p> : null}
            {feed.isError ? <p className={styles.empty}>Could not load your tasks. Retrying…</p> : null}
            {feed.data && tasks.length === 0 ? (
              <p className={styles.empty}>No tasks in the last 7 days. Saving rules, syncing packs, and Job runs show up here.</p>
            ) : null}
            {tasks.length > 0 ? (
              <ul className={styles.list}>
                {tasks.map((task) => (
                  <TaskCard key={task.taskId} task={task} now={now} onNavigate={close} />
                ))}
              </ul>
            ) : null}
          </section>

          <section className={styles.section} aria-labelledby="activity-errors">
            <h3 id="activity-errors" className={styles.sectionTitle}>
              Recent errors
              {errors.length > 0 ? (
                <button type="button" className={styles.linkButton} onClick={clear}>
                  Clear
                </button>
              ) : null}
            </h3>
            {errors.length === 0 ? <p className={styles.empty}>No errors in the last 24 hours.</p> : null}
            {errors.length > 0 ? (
              <ul className={styles.list}>
                {errors.map((entry) => (
                  <ErrorRow key={entry.key} entry={entry} now={now} />
                ))}
              </ul>
            ) : null}
          </section>
        </aside>
      ) : null}
    </>
  );
}
