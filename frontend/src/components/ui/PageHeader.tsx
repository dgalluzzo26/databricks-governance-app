/**
 * @file Standard page title block with optional description and action slot.
 */
import type { ReactNode } from "react";
import styles from "./PageHeader.module.css";

/**
 * Top-of-page heading used by feature screens.
 * @param props.title - Primary page title (`h1`).
 * @param props.description - Optional supporting copy under the title.
 * @param props.actions - Optional controls aligned opposite the title (e.g. buttons).
 */
export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <header className={styles.header}>
      <div>
        <h1 className={styles.title}>{title}</h1>
        {description && <p className={styles.description}>{description}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
