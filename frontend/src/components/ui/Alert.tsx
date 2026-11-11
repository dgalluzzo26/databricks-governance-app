/**
 * @file Inline status and error banners with tone-based styling and ARIA roles.
 */
import type { ReactNode } from "react";
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
 * Convenience danger {@link Alert} that stringifies an unknown error.
 * @param props.error - Thrown value or Error whose message is shown in the body.
 * @param props.title - Optional alert title (default `"Something went wrong"`).
 */
export function ErrorAlert({ error, title = "Something went wrong" }: { error: unknown; title?: string }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <Alert tone="danger" title={title}>
      {message}
    </Alert>
  );
}
