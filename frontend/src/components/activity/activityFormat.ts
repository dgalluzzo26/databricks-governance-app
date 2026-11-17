/**
 * @file Small formatters shared by the Activity panel and the admin task history.
 */

/**
 * Formats a duration in seconds as `850 ms`, `12 s`, or `3m 05s`.
 *
 * @param seconds - Duration, or null while unknown.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m ${String(rest).padStart(2, "0")}s`;
}

/**
 * Formats an ISO time relative to `now`, for example `just now`, `4 min ago`, `2 h ago`.
 * Older than a day falls back to the local date and time.
 *
 * @param iso - ISO timestamp.
 * @param now - Reference time in milliseconds (defaults to the current time).
 */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(iso).toLocaleString();
}

/**
 * Seconds a task has run: finished tasks use their stored duration, running ones count to `now`.
 *
 * @param task - Start time, and stored duration when finished.
 * @param now - Reference time in milliseconds.
 */
export function elapsedSeconds(
  task: { startedAt: string; durationSeconds: number | null },
  now: number = Date.now(),
): number {
  if (task.durationSeconds !== null) return task.durationSeconds;
  return Math.max(0, (now - new Date(task.startedAt).getTime()) / 1000);
}
