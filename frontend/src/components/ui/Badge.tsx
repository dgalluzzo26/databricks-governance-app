/**
 * @file Compact tonal label used for roles, statuses, and similar metadata chips.
 */
import type { ReactNode } from "react";
import styles from "./Badge.module.css";

/** Color treatment for {@link Badge}. */
export type BadgeTone = "neutral" | "info" | "success" | "warning" | "danger";

/**
 * Small pill-style label.
 * @param props.tone - Color variant (default `"neutral"`).
 * @param props.children - Badge text or content.
 */
export function Badge({ tone = "neutral", children }: { tone?: BadgeTone; children: ReactNode }) {
  return <span className={`${styles.badge} ${styles[tone]}`}>{children}</span>;
}
