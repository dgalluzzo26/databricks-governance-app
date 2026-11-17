/**
 * @file Inline status and error banners with tone-based styling and ARIA roles.
 */
import type { ReactNode } from "react";
import { describeError } from "@/api/errors";
import styles from "./Alert.module.css";

/** Visual and semantic severity for {@link Alert}. */
export type AlertTone = "info" | "success" | "warning" | "danger";

/**
 * Non-modal status message for the page content area.
 * @param props.tone - Severity; `"danger"` uses `role="alert"`, others use `role="status"`.
 * @param props.title - Optional bold heading.
 * @param props.children - Optional body content under the title.
 */
export function Alert({ tone = "info", title, children }: { tone?: AlertTone; title?: ReactNode; children?: ReactNode }) {
  return (
    <div className={`${styles.alert} ${styles[tone]}`} role={tone === "danger" ? "alert" : "status"}>
      {title && <strong className={styles.title}>{title}</strong>}
      {children && <div>{children}</div>}
    </div>
  );
}

/**
 * Danger {@link Alert} for a thrown error: the message, a next-step hint for known error codes
 * (for example a stopped SQL warehouse), and the server reference ID when there is one.
 * A warehouse that is still starting is shown as a warning rather than a failure.
 * @param props.error - Thrown value, usually an `ApiError`.
 * @param props.title - Optional context title such as "Could not load policies"; defaults to
 *   a title derived from the error code.
 */
export function ErrorAlert({ error, title }: { error: unknown; title?: string }) {
  const described = describeError(error);
  const tone: AlertTone = described.code === "warehouse_unavailable" ? "warning" : "danger";
  return (
    <Alert tone={tone} title={title ?? described.title}>
      <span>{described.message}</span>
      {described.hint && <span className={styles.hint}>{described.hint}</span>}
      {described.requestId && <span className={styles.reference}>Reference: {described.requestId}</span>}
    </Alert>
  );
}
