/**
 * @file Global busy indicator: a top progress bar and live status when the app is working.
 */
import { useIsFetching, useIsMutating, useMutationState } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import styles from "./BusyBar.module.css";

/** Delay before showing the bar so instant requests do not flash. */
const SHOW_DELAY_MS = 120;
/** Keep the bar visible briefly after work ends so the finish animation is readable. */
const HIDE_DELAY_MS = 280;

/**
 * Reads whether React Query has in-flight mutations or fetches, and any `busyMessage` meta.
 *
 * @returns Busy flag plus a short status label for screen readers and the top bar chip.
 */
export function useAppBusy(): { busy: boolean; message: string | null } {
  const mutating = useIsMutating();
  const fetching = useIsFetching();
  const pendingMessages = useMutationState({
    filters: { status: "pending" },
    select: (mutation) => {
      const meta = mutation.meta as { busyMessage?: string } | undefined;
      return meta?.busyMessage;
    },
  });
  const labeled = pendingMessages.find((message): message is string => Boolean(message));
  const active = mutating > 0 || fetching > 0;
  const message =
    labeled ?? (mutating > 0 ? "Working…" : fetching > 0 ? "Refreshing…" : null);
  return { busy: active, message };
}

/**
 * Fixed indeterminate progress bar at the top of the viewport while the app is busy.
 *
 * @remarks Uses a short show/hide delay so fast requests do not flicker. Sets `aria-busy`
 * on `document.body` while visible. Pair with {@link BusyStatus} in the top bar for a text cue.
 */
export function BusyBar() {
  const { busy, message } = useAppBusy();
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (busy) {
      timer = setTimeout(() => setVisible(true), SHOW_DELAY_MS);
    } else {
      timer = setTimeout(() => setVisible(false), HIDE_DELAY_MS);
    }
    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [busy]);

  useEffect(() => {
    document.body.setAttribute("aria-busy", visible ? "true" : "false");
    return () => document.body.removeAttribute("aria-busy");
  }, [visible]);

  if (!visible) return null;

  return (
    <div
      className={styles.bar}
      role="progressbar"
      aria-valuetext={message ?? "Working"}
      aria-busy="true"
    >
      <div className={styles.indeterminate} />
    </div>
  );
}

/**
 * Compact live status chip for the top bar while mutations or refreshes are in flight.
 */
export function BusyStatus() {
  const { busy, message } = useAppBusy();
  if (!busy || !message) return null;
  return (
    <span className={styles.status} role="status" aria-live="polite">
      <span className={styles.pulse} aria-hidden />
      {message}
    </span>
  );
}
